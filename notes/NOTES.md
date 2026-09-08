# Notes

## Retired: the Ararat Telegram assistant

The Telegram bot, its patched MCP plugin (`telegram-plugin/`), the `claude-telegram.sh`
`--remote-control` shim, and the `com.noahbres.ararat` launchd agent on `rtk` were removed
in Sept 2026 — the bot wasn't used enough to justify running it. The trackers and
`private-data/` it fed are unaffected. Everything removed is recoverable from git history — **see
`notes/ararat-telegram-retired.md`** for the full inventory, where the data lives, and
step-by-step revival instructions.

Note the rtk-api approval bot is a *separate* Telegram bot with its own token and is still
live — see `rtk-api/notes/NOTES.md`.

## Hosts

- **`rnn`** — local machine (this laptop/desktop you're usually working on directly).
- **`rtk`** — a Mac mini physically hosted at home. It's the "remote Mac" — runs headless and is reached remotely over the Cloudflare tunnel below (not sitting on the same LAN as `rnn` day-to-day).

## SSH + Cloudflare Tunnel access to `rtk`

`rtk` is reachable remotely via a Cloudflare Tunnel rather than direct port-forwarding.

- **On `rtk`**: `cloudflared` runs as a launchd agent (`com.noahbres.cloudflared`, defined in `nixos-config/hosts/rtk/home.nix`) using a tunnel token stored at `/etc/cloudflared/tunnel-token`. That token must be manually deployed to the machine (it's not checked into the repo) — see the comment above `launchd.agents.cloudflared` in that file for the exact steps and where to find the token in Cloudflare Zero Trust (Networks → Tunnels → `<tunnel>` → Configure → Install connector).
- **Public hostname**: `ssh-rtk.noahbres.com` — routes through the tunnel to `rtk`'s SSH port.
- **Cloudflare Access app `ssh-rtk`** (self-hosted, domain `ssh-rtk.noahbres.com`, 24h session;
  app id in `private-data/infra-ids.md`) gates that hostname at the edge as of 2026-09-08. Two
  policies, so there are exactly two ways in:
  1. `rtk-api service token (owner)` — a `non_identity` (Service Auth) policy for the owner's own
     `rtk-api` service token (the same one `api.noahbres.com` uses). This is the non-interactive
     path the SSH aliases use. The `instinct` service token is deliberately **not** included —
     instinct gets the API, never the shell.
  2. `owner email OTP` — an `allow` policy for `noahbres@gmail.com`, for a browser login via
     `cloudflared access ssh` when the service token isn't at hand. **Caveat:** the One-time PIN
     login method still has to be enabled on the Zero Trust team (Settings → Authentication →
     Login methods → One-time PIN). The scoped API token in 1Password can't create identity
     providers (`auth.forbidden`), so this is a one-click dashboard step for Noah; until then
     only the service-token path works.
  Verified 2026-09-08: unauthenticated `https://ssh-rtk.noahbres.com/` now 302s to the Access
  login (was 200 straight from the tunnel), and `ssh` with the service token exported still
  reaches rtk.
- **sshd on rtk accepts keys only.** `services.openssh.extraConfig` in
  `nixos-config/hosts/rtk/configuration.nix` writes `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no`, `ChallengeResponseAuthentication no`, `PermitRootLogin no`
  into `/etc/ssh/sshd_config.d/100-nix-darwin.conf` (macOS's `sshd_config` includes that
  directory; nix-darwin already owns that file). Written 2026-09-08, **pending
  `just build-deploy-rtk`**; verify afterwards with
  `ssh rtk 'sudo sshd -T | grep -i passwordauth'` → `passwordauthentication no`.
- **`cloudflared` is installed on both hosts** via `home.packages` in `nixos-config/hosts/common/darwin/home.nix` (used on `rnn` as the client, and on `rtk` for both the client and the tunnel daemon above).

To connect from `rnn`, use the `rtk` or `rtk-cloudflare` SSH alias (see the deploy-rs section
below). Both run the same `rtk-ssh-proxy` script from
`nixos-config/hosts/common/darwin/home.nix`; on the tunnel path it reads the service token from
1Password (`op read "op://Private/rtk-api cloudflare access service token/client_id"` and
`.../credential`) into `TUNNEL_SERVICE_TOKEN_ID` / `TUNNEL_SERVICE_TOKEN_SECRET` for
`cloudflared access ssh`, respects those variables if already exported, and falls back to
cloudflared's interactive browser login if `op` is locked or unavailable. From a machine without
the nix config:

```sh
export TUNNEL_SERVICE_TOKEN_ID=$(op read "op://Private/rtk-api cloudflare access service token/client_id")
export TUNNEL_SERVICE_TOKEN_SECRET=$(op read "op://Private/rtk-api cloudflare access service token/credential")
ssh -o ProxyCommand="cloudflared access ssh --hostname %h" noah@ssh-rtk.noahbres.com
```

SSH itself still authenticates with the standard identity (`~/.ssh/id_rsa` on `rnn`, in
`~/.ssh/authorized_keys` on rtk) — Access is a second gate in front of it, not a replacement.

## rtk-api (api.noahbres.com)

A private, authenticated HTTP/MCP server on `rtk` exposing personal tools (Things 3, iMessage)
to AI agents and scripts. **Its notes live with the code**, not here:

- `rtk-api/notes/NOTES.md` — current state: architecture, auth, credentials, Cloudflare objects,
  gotchas, what's left.
- `rtk-api/notes/plan.md` — original design doc and rollout phases.
- `rtk-api/README.md` — dev/usage docs.

Host-level context that rtk-api depends on stays here: the Cloudflare Tunnel section above, and
the deploy-rs section below.

## Deploying nix config to `rtk` (deploy-rs)

`rtk` is headless, so nix changes are pushed from `rnn` with
[deploy-rs](https://github.com/serokell/deploy-rs). Set up 2026-09-08. Node config:
`deploy.nodes.rtk` in `nixos-config/flake.nix`; recipes in `nixos-config/justfile`.

**Why deploy-rs:** nix-darwin has no upstream `--target-host` (PR nix-darwin/nix-darwin#1631 still
open). **Why interactive sudo:** Noah doesn't want passwordless sudo, so `interactiveSudo = true`
prompts for rtk's password. Consequence: **nix deploys are human-only**; agents prepare and verify
(`just build-rtk`, `nix eval`), Noah runs the deploy.

**How it works:** builds the rtk closure on rnn (`remoteBuild = false`), `nix copy`s it over the
`rtk` SSH alias, activates via `sudo` on rtk. `autoRollback` is on (revert if activation itself
errors). `magicRollback` is **on** (fixed 2026-09-08 — see gotchas for the actual bug and fix).

**SSH aliases** (`nixos-config/hosts/common/darwin/home.nix`):
- `rtk` — preferred. ProxyCommand script tries the LAN/Tailscale path (`rtk.local`) first, falls
  back to the Cloudflare Access tunnel. Uses ControlMaster so deploy-rs's several SSH calls share
  one connection.
- `rtk-ts` — pure Tailscale (`rtk.<tailnet>.ts.net`; tailnet name and both hosts' 100.x IPs are
  in `private-data/infra-ids.md`).
- `rtk-cloudflare` — Cloudflare tunnel only (`ssh-rtk.noahbres.com` via `cloudflared access ssh`).

**Commands** (from anywhere in the repo — the root justfile imports `nixos-config/justfile`):

```sh
just build-rtk        # build the exact closure deploy-rtk ships, no sudo (leaves ./result)
just deploy-rtk-dry   # build + copy, don't activate
just deploy-rtk       # build, copy, activate (prompts for rtk sudo password)
just build-deploy-rtk # build-rtk then deploy-rtk (build finishes before the password prompt)
just switch-rtk       # local-only fallback: run ON rtk
just switch           # rnn itself
```

The "Git tree has uncommitted changes" warning is harmless; deploy-rs deploys the working tree.

**Workflows:**
- *nix change for rtk* → `just build-deploy-rtk`, then commit + push so rtk's checkout stays in
  sync for `git pull`s.
- *rtk-api Python change only* → `rtk-api/deploy.sh --remote`.
- *both* → deploy-rs first (installs the plist), then `deploy.sh --remote`.
- *bump inputs* → `just update`, `just switch` (rnn), `just build-deploy-rtk`. This is how
  `cloudflared` on rtk gets upgraded.
- *verify after deploy* → `ssh rtk 'readlink /nix/var/nix/profiles/system; launchctl list | grep noahbres; curl -s localhost:8787/health'`.

**Gotchas:**
- **Magic rollback timed out on every deploy (2026-09-08), root cause found and fixed.**
  `activate-rs` (deploy-rs's remote activation binary) confirms success by creating a lock file
  under `tempPath` (default `/tmp`) and watching that directory with the `notify` crate for the
  lock file to be *removed* — the local deploy-rs CLI does the removing over a second sudo'd SSH
  call. On macOS, `notify`'s FSEvents backend reports the *canonicalized* path, so events under
  `/tmp` (a symlink to `/private/tmp`) arrive as `/private/tmp/...` and never string-match the
  `/tmp/...` path the watcher is holding — confirmation can structurally never succeed, regardless
  of network speed or sudo timing. **Fix:** `tempPath = "/private/tmp";` on `deploy.nodes.rtk` in
  `flake.nix`, so the watched path and the reported path are already the same string. Two other
  things got fixed alongside this while chasing the bug (real issues, just not *the* cause):
  `packages.aarch64-darwin.deploy-rs` now comes from the `deploy-rs` flake input directly instead
  of nixpkgs, so the local CLI and the remote activation lib are always the same version; and
  `environment.etc."sudoers.d/deploy-rs-tty-tickets"` in `hosts/rtk/configuration.nix` disables
  macOS sudo's `tty_tickets` so the activate/wait/confirm sudo calls (each its own SSH session, own
  pty) can share one cached credential.
- If a deploy ever silently reverts again, tell is `readlink /nix/var/nix/profiles/system` not
  advancing after activation looked like it ran. If a deploy ever breaks SSH outright, Screen
  Sharing is the way in.
- **Don't deploy over the Cloudflare tunnel.** A deploy that changes the `cloudflared` plist
  restarts cloudflared and drops the tunnel mid-deploy. The `rtk` alias avoids this on the LAN;
  off-LAN use `rtk-ts`.
- **Login Items shows "sh" for any agent using home-manager's default wrapper** (`/bin/sh -c
  "wait4path /nix/store && exec ..."`). All four `com.noahbres.*` agents set
  `waitForNixStore = false`, which swaps in a launcher script named after the agent; safe because
  user agents start after login, when the store is long mounted. Two remaining "sh" entries are
  not ours: `org.nixos.activate-system` (nix-darwin) and `systems.determinate.nix-installer.nix-hook`.
- The old `~/Developer/nixos-config` checkout on rtk is stale (pre-merge); the live config is
  `~/Developer/ararat/nixos-config`. The stale one can be deleted.

## noahbres.com domain / DNS architecture

- **Registrar**: Namecheap. **DNS**: actually delegated to **Cloudflare**
  (`aldo.ns.cloudflare.com` / `destiny.ns.cloudflare.com`) — Namecheap's own "Advanced DNS" panel
  is inert for this domain (DNS Type shows "Custom DNS"); all real record edits happen in
  Cloudflare. Zone ID (and the record/rule ids below) in `private-data/infra-ids.md`.
- **Site**: hosted on Notion (a page published via Notion Sites), CNAME'd from
  `www.noahbres.com` -> `external.notion.site`, proxied through Cloudflare.
  `www.noahbres.com` is the **paid** custom domain slot in Notion (Public pages -> Domains,
  $96/year). Notion bills **per domain connected**, not per site — adding the bare apex as a
  second Notion custom domain would be a *second* $96/year charge, and there's no in-place way to
  rename/swap an existing domain slot (only delete + re-add, which risks a re-charge and site
  downtime). Decided against both; see below for the free workaround.
- **Apex (`noahbres.com`, no www)**: as of 2026-09-08, resolves via a free-tier setup instead of a
  second Notion domain:
  1. Cloudflare CNAME record `noahbres.com` -> `external.notion.site`, proxied (record id in
     `infra-ids.md`). This exists just so Cloudflare's edge sees traffic for
     the apex (Notion itself 403s the bare domain since only `www` is registered there).
  2. Cloudflare **Page Rule** (Free plan, 3-rule quota) `noahbres.com/*` -> 301 redirect to
     `https://www.noahbres.com/$1` (rule id in `infra-ids.md`). This is what actually
     makes the apex work — it intercepts before hitting Notion.
- **API tokens**: two narrowly-scoped Cloudflare tokens were created for this and saved in
  1Password — "Cloudflare - noahbres.com DNS edit token" and "Cloudflare - noahbres.com Page
  Rules edit token". The account login item is "Name Cheap" / "Cloudflare" (also in 1Password);
  logging into either via browser hit a Cloudflare bot-check wall for headless automation, so the
  working pattern was: open a **headed** agent-browser session, have Noah log in manually, then
  drive the rest — or mint a scoped API token instead where the surface supports it (much more
  reliable than fighting the dashboard UI).
