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
  `rtk-cloudflare`). Re-apply the nix config (`just switch-rtk` in `nixos-config/`) only when
  `home.nix` itself changes.
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
