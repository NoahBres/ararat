# Server Provisioning

How to provision a new Hetzner server with Tailscale access (no public IPv4 needed).

## Prerequisites

- Hetzner API token in 1Password as `Hetzner - Default - API Token`
- SSH key in 1Password as `Hetzner` (SSH Key category)
- Tailscale auth key — generate at tailscale.com/admin/settings/keys (Reusable + Ephemeral)
- SSH public key registered in Hetzner Cloud Console (Security → SSH Keys)

## The problem this solves

Hetzner servers are IPv6-only by default. Adding a public IPv4 costs ~€0.50/mo and requires
the server to be powered off to assign. Instead, we install Tailscale via cloud-init on first
boot so the server joins the tailnet immediately — no public IP ever needed.

## Steps

### 1. Get the API token

```sh
TOKEN=$(op item get "Hetzner - Default - API Token" --reveal --format json | \
  python3 -c "import sys,json; data=json.load(sys.stdin); print(next(f['value'] for f in data['fields'] if f.get('value') and len(f['value']) > 10))")
```

### 2. Find the server ID

```sh
curl -s -H "Authorization: Bearer $TOKEN" https://api.hetzner.cloud/v1/servers | \
  python3 -c "import sys,json; [print(s['id'], s['name']) for s in json.load(sys.stdin)['servers']]"
```

Or when creating a new server, note the ID returned by the API.

### 3. Rebuild (or create) with cloud-init

Replace `<TAILSCALE_AUTH_KEY>` and `<SSH_PUBLIC_KEY>` below, then rebuild:

```sh
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "image": "ubuntu-24.04",
    "user_data": "#cloud-config\nusers:\n  - name: root\n    ssh_authorized_keys:\n      - <SSH_PUBLIC_KEY>\nruncmd:\n  - [\"sh\", \"-c\", \"curl -fsSL https://tailscale.com/install.sh | sh\"]\n  - [\"tailscale\", \"up\", \"--auth-key=<TAILSCALE_AUTH_KEY>\", \"--ssh\", \"--hostname=<HOSTNAME>\"]"
  }' \
  https://api.hetzner.cloud/v1/servers/<SERVER_ID>/actions/rebuild
```

**`--ssh`** enables Tailscale SSH (no need to manage `authorized_keys` separately going forward).
**`--hostname`** sets the name that appears in the Tailscale admin panel and MagicDNS.

The SSH public key for `<SSH_PUBLIC_KEY>` can be retrieved from 1Password:
```sh
op item get "Hetzner" --fields label="public key"
```

### 4. Wait and connect

Poll server status until `running`:
```sh
curl -s -H "Authorization: Bearer $TOKEN" https://api.hetzner.cloud/v1/servers/<SERVER_ID> | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['server']['status'])"
```

Then check tailscale.com/admin/machines — once the hostname appears, connect:
```sh
ssh root@<HOSTNAME>
```

## Notes

- cloud-init runs on first boot after rebuild; Tailscale auth takes ~30–60s after the server shows `running`
- Tailscale `--ssh` flag means you don't need a separate SSH key after initial setup
- The SSH key in cloud-init is a fallback in case Tailscale SSH is unavailable
- Ephemeral Tailscale nodes auto-remove from the admin panel when offline
