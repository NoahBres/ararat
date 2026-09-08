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
