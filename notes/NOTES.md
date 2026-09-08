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

## rtk-api (api.noahbres.com)

`rtk-api` is a private, authenticated HTTP API running on `rtk` (the home Mac mini) that exposes
personal "tools" — Things 3 and iMessage today — to AI agents and scripts. Built 2026-09-08.
Design doc: `notes/plans/rtk-api.md`. Dev/usage docs: `rtk-api/README.md`. Code: `rtk-api/`.

### Architecture in one paragraph

One Python 3.12 process (FastAPI + fastmcp, managed by `uv`) listens on `127.0.0.1:8787` on rtk.
A tool is a plain function registered with `@tool("things.list")` in `rtk-api/src/rtk_api/tools/`;
the registry turns it into both a REST route (`POST /v1/things/list`, JSON kwargs in, `{"ok",
"result"|"error"}` out) and an MCP tool. New tool modules dropped into `tools/` are auto-discovered.
Write tools are flagged `write=True` and append to `~/.local/state/rtk-api/audit.log`. Tool calls
run in a threadpool so one blocked call never freezes `/health`. Ingress is the existing
Cloudflare Tunnel; nothing is bound publicly.

### Calling it

```sh
curl https://api.noahbres.com/v1/things/list \
  -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET" \
  -H "content-type: application/json" -d '{"view":"today"}'
curl -H ... https://api.noahbres.com/v1/tools        # registry with JSON schemas
curl https://api.noahbres.com/health                  # unauthenticated (still needs Access headers at the edge)
```

Tools (20): `system.{ping,version,echo}`; `things.{list,search,get,projects,areas,tasks_in}` (read),
`things.{add,update,complete,add_project,batch}` (write, via `things:///` URL scheme; there is no
delete in the scheme); `imessage.{chats,recent,with_contact,search,unread}` (read, chat.db +
Contacts name resolution), `imessage.send` (write, **off** unless `IMESSAGE_WRITE_ENABLED=true`
AND recipient in `IMESSAGE_WRITE_ALLOWLIST`; first real send will pop an Automation prompt for
Messages.app on rtk's screen).

### Auth (two layers)

1. **Cloudflare Access** at the edge (team `bold-poetry-9de0`, Zero Trust Free). Access app
   `rtk-api` on `api.noahbres.com`, one policy: Service Auth for service token `rtk-api` (expires
   2027-09-08). Anything without valid `CF-Access-Client-Id`/`-Secret` headers gets 403 before
   reaching rtk. Consequence: clients that can't set custom headers (Claude.ai's connector UI)
   can't use `api.`; that's what the future `mcp.noahbres.com` capability-URL design is for.
2. **The server itself** accepts a request if the `Cf-Access-Jwt-Assertion` JWT verifies against
   the team's JWKS with the app's AUD, *or* `Authorization: Bearer $RTK_API_BEARER_TOKEN`
   (only reachable from localhost now), *or* the path starts with `/$RTK_API_MCP_SECRET/` (MCP,
   not exposed yet). Failed auth → 401.

### Credentials index (all in 1Password, Private vault)

| Item | What |
|---|---|
| `rtk-api` | bearer token + MCP secret (mirror of `~/.config/rtk-api/env` on rtk) |
| `rtk-api cloudflare access service token` | client id + secret for the Access headers |
| `cloudflare-rtk-api-token` | scoped Cloudflare API token (Tunnel/Access/DNS/WAF on noahbres.com), expires 2026-10-08 |
| `cloudflare-token-creator` | Cloudflare token that can mint other tokens |
| `Things` | Things auth token also lives in `~/Developer/ararat/.env` on rtk |

Runtime secrets file on rtk: `~/.config/rtk-api/env` (`chmod 600`, never in git):
`RTK_API_BEARER_TOKEN`, `RTK_API_MCP_SECRET`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`,
`THINGS_AUTH_TOKEN`, `IMESSAGE_WRITE_ENABLED`, `IMESSAGE_WRITE_ALLOWLIST`.

### Cloudflare objects (account `b912898d014811a465b4b3bf29ba9c0b`, zone `1a485d0b081c74cfe34537c498114b55`)

- Tunnel `ararat` (`a1428f1d-04a1-496f-a8f0-18bc7ee54152`), remote-managed by token in
  `/etc/cloudflared/tunnel-token` on rtk. Ingress: `ssh-rtk.noahbres.com -> ssh://localhost:22`,
  `api.noahbres.com -> http://localhost:8787`. Public hostnames live in the Zero Trust dashboard /
  API, not in a local `config.yml`.
- DNS: proxied CNAME `api` -> `<tunnel-id>.cfargotunnel.com`.
- Access app `rtk-api` + service token `rtk-api` (above). `ssh-rtk.noahbres.com` still has **no**
  Access policy (SSH keys are the only guard there).
- Not created: `mcp.noahbres.com`, WAF rate-limit rules.

### Running on rtk

- launchd agent `com.noahbres.rtk-api` (nix: `nixos-config/hosts/rtk/home.nix`). It runs
  `~/Applications/rtk-api.app/Contents/MacOS/rtk-api <start-script>`; the start script sources the
  env file and execs `uv run --frozen rtk-api serve`. Logs `/tmp/rtk-api.log`,
  `/tmp/rtk-api-error.log`. Aliases on rtk: `restart-rtk-api`, `kill-rtk-api`, `rtk-api-log`.
- **`rtk-api.app` is a tiny C launcher** (`rtk-api/launcher/`, built once on rtk with
  `launcher/build.sh`, ad-hoc signed, bundle id `com.noahbres.rtk-api`). It spawns the real command
  as a child and forwards signals. Purpose: macOS attributes TCC permissions to a launchd job's
  main executable, so this makes the prompts and the Full Disk Access list say "rtk-api" instead
  of "python3.12"/"uvx". **Full Disk Access is granted to it** (covers Things' group container,
  chat.db, AddressBook). Rebuilding the launcher (`--force`) changes its signature and requires
  re-granting.
- Python is uv-managed CPython 3.12.13 (`~/.local/share/uv/python/...`), pinned in
  `rtk-api/.python-version`. Not nix-managed on purpose (nix store paths churn on every rebuild).
- **Code deploy** (no root, agent-runnable): `rtk-api/deploy.sh --remote` from rnn — pulls,
  `uv sync --frozen`, kickstarts the agent, polls `/health`. Nix deploy only when `home.nix` changes.

### Gotchas learned

- **TCC prompts block silently under launchd.** First access to another app's container pops
  "rtk-api would like to access data from other apps" on rtk's screen and the syscall blocks until
  clicked. `things` is imported lazily and tools run in a threadpool so the server still boots;
  the affected call just hangs. Approve via Screen Sharing (or FDA covers it).
- SSH sessions have Full Disk Access implicitly, so "it works over ssh" proves nothing about
  launchd.
- `things.search` only returns incomplete items by default.
- The service-token JWT check needs outbound HTTPS from rtk to
  `bold-poetry-9de0.cloudflareaccess.com/cdn-cgi/access/certs` (cached after first fetch).

### Not done / future

- `mcp.noahbres.com` for Claude.ai (design in plan §2/§5/§7): same process, capability URL, no
  Access, plus a Cloudflare rate-limit rule. Code path exists behind `RTK_API_MCP_SECRET`; untested
  against Claude.ai.
- iMessage send enablement (flip the two env vars, approve the Automation prompt).
- Access policy on `ssh-rtk.noahbres.com`.
- `things-today-tracker` still runs as bare `/usr/bin/python3` and triggers its own "uvx" TCC
  prompts; could be routed through the same launcher.

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
errors). **`magicRollback` is OFF** — see gotchas.

**SSH aliases** (`nixos-config/hosts/common/darwin/home.nix`):
- `rtk` — preferred. ProxyCommand script tries the LAN/Tailscale path (`rtk.local`) first, falls
  back to the Cloudflare Access tunnel. Uses ControlMaster so deploy-rs's several SSH calls share
  one connection.
- `rtk-ts` — pure Tailscale (`rtk.taile4ea05.ts.net`, rtk = 100.83.51.37, rnn = 100.108.87.111).
- `rtk-cloudflare` — Cloudflare tunnel only (`ssh-rtk.noahbres.com` via `cloudflared access ssh`).

**Commands** (from `rnn`, inside `nixos-config/`):

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
- **Magic rollback is disabled.** Its confirm step is a second sudo'd SSH call; under interactive
  sudo it kept failing and silently reverting good deploys (three times on 2026-09-08 — the box
  looked "deployed" but was on the old generation, and the failed generations were deleted). Tell
  is `readlink /nix/var/nix/profiles/system` not advancing. If a deploy ever breaks SSH, Screen
  Sharing is the way in.
- **Don't deploy over the Cloudflare tunnel.** A deploy that changes the `cloudflared` plist
  restarts cloudflared and drops the tunnel mid-deploy. The `rtk` alias avoids this on the LAN;
  off-LAN use `rtk-ts`.
- **Login Items shows "sh" for any agent using home-manager's default wrapper** (`/bin/sh -c
  "wait4path /nix/store && exec ..."`). All four `com.noahbres.*` agents set
  `waitForNixStore = false`, which swaps in a launcher script named after the agent; safe because
  user agents start after login, when the store is long mounted. Two remaining "sh" entries are
  not ours: `org.nixos.activate-system` (nix-darwin) and `systems.determinate.nix-installer.nix-hook`.
- deploy-rs CLI comes from nixpkgs (binary-cached); the activation lib from the flake input. A
  version-mismatch warning between them is harmless.
- The old `~/Developer/nixos-config` checkout on rtk is stale (pre-merge); the live config is
  `~/Developer/ararat/nixos-config`. The stale one can be deleted.

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
