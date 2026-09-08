# Notes

## Telegram Plugin
- Implemented custom Telegram plugin based on https://github.com/anthropics/claude-code/issues/42037
- Working correctly in current setup
- Using local patched version instead of node_modules version (commit: `573d4ad`)

## Hosts

- **`rnn`** — local machine (this laptop/desktop you're usually working on directly).
- **`rtk`** — a Mac mini physically hosted at home. It's the "remote Mac" — runs headless and is reached remotely over the Cloudflare tunnel below (not sitting on the same LAN as `rnn` day-to-day). This is also where the `ararat` Telegram assistant session actually runs (see `launchd.agents.ararat` in `nixos-config/hosts/rtk/home.nix`).

## SSH + Cloudflare Tunnel access to `rtk`

`rtk` is reachable remotely via a Cloudflare Tunnel rather than direct port-forwarding.

- **On `rtk`**: `cloudflared` runs as a launchd agent (`com.noahbres.cloudflared`, defined in `nixos-config/hosts/rtk/home.nix`) using a tunnel token stored at `/etc/cloudflared/tunnel-token`. That token must be manually deployed to the machine (it's not checked into the repo) — see the comment above `launchd.agents.cloudflared` in that file for the exact steps and where to find the token in Cloudflare Zero Trust (Networks → Tunnels → `<tunnel>` → Configure → Install connector).
- **Public hostname**: `ssh-rtk.noahbres.com` — routes through the tunnel to `rtk`'s SSH port.
- **No Cloudflare Access policy is currently attached** to that hostname — anyone who knows the hostname can reach the SSH port through the tunnel. The actual auth boundary today is SSH itself (publickey, with password/keyboard-interactive also offered by the server). Standing up an Access policy (email OTP / SSO / service token) is a reasonable hardening step since it isn't there yet.
- **`cloudflared` is installed on both hosts** via `home.packages` in `nixos-config/hosts/common/darwin/home.nix` (used on `rnn` as the client, and on `rtk` for both the client and the tunnel daemon above).

To connect from `rnn` (or any machine with `cloudflared` + the right SSH key):

```sh
ssh -o ProxyCommand="cloudflared access ssh --hostname %h" noah@ssh-rtk.noahbres.com
```

Uses the standard SSH identity (currently `~/.ssh/id_rsa` on `rnn`) — no separate Cloudflare Access login/service-token step needed since no Access policy is enforced.

## hometools (api.noahbres.com / mcp.noahbres.com)

`hometools` is a private, authenticated HTTP surface running on `rtk` that exposes personal
"tools" (Things 3, iMessage, etc.) to AI agents over REST and MCP. Full design: see
`notes/plans/hometools-api.md`. Usage/dev docs: `hometools/README.md`.

- **Hostnames**: `api.noahbres.com` (REST, Cloudflare Access service-token auth) and
  `mcp.noahbres.com` (MCP, capability-URL auth: `https://mcp.noahbres.com/<secret>/mcp`).
  Both route through the existing `rtk` Cloudflare Tunnel to one local process.
- **Port**: `127.0.0.1:8787` on `rtk` only — never bound publicly; all ingress is via the tunnel.
- **Secrets**: `~/.config/hometools/env` on `rtk` (`chmod 600`, gitignored, never committed).
  Holds `HOMETOOLS_BEARER_TOKEN`, `HOMETOOLS_MCP_SECRET`, `CF_ACCESS_TEAM_DOMAIN`,
  `CF_ACCESS_AUD`, `THINGS_AUTH_TOKEN`, `IMESSAGE_WRITE_ENABLED`, `IMESSAGE_WRITE_ALLOWLIST`.
  Also backed up in 1Password (item `hometools`).
- **launchd**: `com.noahbres.hometools`, defined in `nixos-config/hosts/rtk/home.nix`
  (modelled on `things-today-tracker` / `ararat`). Runs
  `uv run --frozen hometools serve --host 127.0.0.1 --port 8787` from
  `~/Developer/ararat/hometools`, sourcing the secrets file first. Logs: `/tmp/hometools.log`,
  `/tmp/hometools-error.log`.
- **Aliases** (on `rtk`): `restart-hometools`, `kill-hometools`, `hometools-log`.
- **Deploy**: `hometools/deploy.sh` — pulls, `uv sync --frozen`, kicks the launchd agent, polls
  `/health`. Run directly on `rtk`, or `hometools/deploy.sh --remote` from `rnn` (SSHes in via
  `rtk-cloudflare`). Re-apply the nix config (`just deploy-rtk` from `rnn`, see "Deploying nix config to `rtk`"
  below) only when `home.nix` itself changes.
- **Cloudflare state (as of 2026-09-08)**: tunnel `ararat` (id `a1428f1d-04a1-496f-a8f0-18bc7ee54152`,
  account `b912898d014811a465b4b3bf29ba9c0b`) has ingress `api.noahbres.com -> http://localhost:8787`
  and a proxied CNAME `api` -> `<tunnel-id>.cfargotunnel.com`, both created via API. **Cloudflare
  Access is NOT enabled on the account yet** (dashboard-only "Enable Access" step), so `api.` is
  protected by the hometools bearer token alone (TLS + 256-bit random token, constant-time compare).
  `mcp.noahbres.com` not created yet. API tokens in 1Password: `cloudflare-token-creator` (can mint
  tokens) and `cloudflare-hometools-token` (scoped: Tunnel/Access/DNS/WAF on noahbres.com, expires
  2026-10-08). Bearer token + MCP secret: 1Password item `hometools`.
- **TCC gotcha**: under launchd, first access to another app's container (Things group container,
  chat.db, AddressBook) pops macOS's "access data from other apps" prompt on rtk's screen and blocks
  that call until clicked. `things` is imported lazily and tool calls run in a threadpool so the
  server still boots and `/health` answers; Things/iMessage calls hang until the prompt is approved
  or FDA is granted to the python binary (plan §6.1). Approve via Screen Sharing.
- **Manual Phase 0 steps** (Noah only — see plan §3 for full detail):
  1. Add `api.noahbres.com` and `mcp.noahbres.com` as public hostnames on the `rtk` Cloudflare
     Tunnel → `localhost:8787`.
  2. Put Cloudflare Access (service token) in front of `api.noahbres.com`; note the AUD tag and
     team domain.
  3. Do **not** put Access on `mcp.noahbres.com` (Claude.ai can't send custom headers) — add a
     rate-limit rule there instead.
  4. Create `~/.config/hometools/env` on `rtk` with the variables listed above.
  5. Grant Full Disk Access to the uv-managed Python (needed for iMessage only) via Screen
     Sharing on `rtk`.
  6. If iMessage write is ever enabled, approve the Automation/TCC prompt for Messages.app via
     Screen Sharing.

## Deploying nix config to `rtk` (deploy-rs)

`rtk` is headless and reachable only through the Cloudflare tunnel, so nix changes are pushed
from `rnn` with [deploy-rs](https://github.com/serokell/deploy-rs) rather than by SSHing in and
running `darwin-rebuild` there. Set up 2026-09-08. Node config: `deploy.nodes.rtk` in
`nixos-config/flake.nix`; recipes in `nixos-config/justfile`.

Why deploy-rs and not `darwin-rebuild --target-host`: nix-darwin has no upstream remote-deploy
support (PR nix-darwin/nix-darwin#1631 still open). Why not passwordless sudo: Noah doesn't want
it. deploy-rs is configured with `interactiveSudo = true`, so it prompts for the rtk sudo password
and never needs a NOPASSWD rule. Consequence: **nix deploys to rtk are human-only** — agents can't
run them non-interactively. Code deploys for hometools don't need root and stay agent-runnable.

**How it works:** builds the rtk closure locally on `rnn` (`remoteBuild = false`, so the mini
never compiles anything), `nix copy`s it over the `rtk-cloudflare` SSH alias, activates via
`sudo` on rtk, then reconnects to confirm. If it can't reconnect within the timeout (e.g. the
new config broke `cloudflared` or sshd), rtk rolls back to the previous generation by itself
("magic rollback"). This is the whole point — a bad deploy can't strand the box.

**Commands** (run from `rnn`, inside `nixos-config/`):

```sh
just deploy-rtk-dry   # build + copy, don't activate — sanity check first
just build-rtk        # build the exact closure deploy-rtk ships, no sudo (leaves ./result)
just deploy-rtk       # build, copy, activate (prompts for rtk sudo password), auto-rollback
just build-deploy-rtk # build-rtk then deploy-rtk, so the build finishes before the sudo prompt
just switch-rtk       # local-only fallback: run ON rtk, plain darwin-rebuild switch
just switch           # rnn itself (unchanged)
```

Nix's "Git tree has uncommitted changes" warning is harmless; deploy-rs deploys the working tree.

**Workflows:**

- *Change something in `hosts/rtk/home.nix` or shared config* → `just deploy-rtk-dry`, then
  `just deploy-rtk`. Commit + push afterwards so rtk's own checkout (`~/Developer/ararat`) stays
  in sync for the hometools/ararat `git pull`s.
- *Change only hometools Python code* → `hometools/deploy.sh --remote`. No nix involved.
- *Change both* → deploy-rs first (it installs the launchd plist), then `deploy.sh --remote`.
- *Bump inputs* → `just update`, `just switch` on rnn, then `just deploy-rtk`. Remember the
  `cloudflared` daemon on rtk is nix-managed, so this is how it gets upgraded.
- *Deploy failed / rolled back* → the previous generation is still active; fix the config and
  redeploy. If SSH is dead anyway, Screen Sharing is the only way in.
- *deploy-rs CLI itself* comes from nixpkgs (`packages.aarch64-darwin.deploy-rs`), binary-cached,
  so no Rust build. The activation library comes from the `deploy-rs` flake input; a version
  mismatch warning between the two is expected and harmless.

**Gotchas:**
- The old `switch-rtk` recipe used to SSH to `rtk.local` and run `just switch` in
  `~/Developer/nixos-config` — that path is the pre-merge standalone checkout on rtk and is stale.
  The live config is `~/Developer/ararat/nixos-config`. The stale checkout can be deleted.
- `just deploy-rtk` needs rnn on a network where `cloudflared access ssh` works (any internet).
- Home-manager is integrated as a nix-darwin module, so user-level launchd agents also go through
  this root-level deploy; there is no sudo-free path for them today.

## noahbres.com domain / DNS architecture

- **Registrar**: Namecheap. **DNS**: actually delegated to **Cloudflare**
  (`aldo.ns.cloudflare.com` / `destiny.ns.cloudflare.com`) — Namecheap's own "Advanced DNS" panel
  is inert for this domain (DNS Type shows "Custom DNS"); all real record edits happen in
  Cloudflare. Zone ID: `1a485d0b081c74cfe34537c498114b55`.
- **Site**: hosted on Notion (a page published via Notion Sites), CNAME'd from
  `www.noahbres.com` -> `external.notion.site`, proxied through Cloudflare.
  `www.noahbres.com` is the **paid** custom domain slot in Notion (Public pages -> Domains,
  $96/year). Notion bills **per domain connected**, not per site — adding the bare apex as a
  second Notion custom domain would be a *second* $96/year charge, and there's no in-place way to
  rename/swap an existing domain slot (only delete + re-add, which risks a re-charge and site
  downtime). Decided against both; see below for the free workaround.
- **Apex (`noahbres.com`, no www)**: as of 2026-09-08, resolves via a free-tier setup instead of a
  second Notion domain:
  1. Cloudflare CNAME record `noahbres.com` -> `external.notion.site`, proxied (id
     `066c515f3eb7bfd69b6940578920225c`). This exists just so Cloudflare's edge sees traffic for
     the apex (Notion itself 403s the bare domain since only `www` is registered there).
  2. Cloudflare **Page Rule** (Free plan, 3-rule quota) `noahbres.com/*` -> 301 redirect to
     `https://www.noahbres.com/$1` (id `926e3397abadd76ce51621fc3f30c74b`). This is what actually
     makes the apex work — it intercepts before hitting Notion.
- **API tokens**: two narrowly-scoped Cloudflare tokens were created for this and saved in
  1Password — "Cloudflare - noahbres.com DNS edit token" and "Cloudflare - noahbres.com Page
  Rules edit token". The account login item is "Name Cheap" / "Cloudflare" (also in 1Password);
  logging into either via browser hit a Cloudflare bot-check wall for headless automation, so the
  working pattern was: open a **headed** agent-browser session, have Noah log in manually, then
  drive the rest — or mint a scoped API token instead where the surface supports it (much more
  reliable than fighting the dashboard UI).
