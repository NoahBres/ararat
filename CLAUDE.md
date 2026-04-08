# Project Notes

> **Note for Claude**: macOS-specific instructions (Things 3, etc.) are in `claude-macos.md`. Only read that file if running on macOS.

## Telegram UX — Long-Running Tasks

When starting a long-running task (e.g., git operations, API calls, multi-step work), send a Telegram message via the `reply` tool first to acknowledge the request and let the user know work is underway. The user interacts through Telegram and cannot see intermediate tool output, so silence looks like a disappeared bot.

---

## Local TODO

There's a local `TODO.md` file in the repo for quick task tracking with Backlog, In Progress, and Done sections.

---

## 1Password CLI

Credentials are stored in 1Password and accessible via `op` CLI (already authenticated in shell).

```sh
# List items by category
op item list --categories "SSH Key" --format json
op item list --categories "API Credential" --format json

# Get an item's fields (use --reveal for hidden values)
op item get "Item Name" --reveal --format json

# Extract a specific field value
op item get "Item Name" --reveal --fields label=credential
```

### Known items
- **`Hetzner - Default - API Token`** — Hetzner Cloud API token (field: `credential`)
- **`Hetzner`** (SSH Key category) — SSH key pair used for Hetzner servers (fields: `public key`, `private key`, `fingerprint`)

To use the SSH private key, write it to a temp file:
```sh
op item get "Hetzner" --reveal --format json | python3 -c "
import sys, json, stat, os
data = json.load(sys.stdin)
key = next(f['value'] for f in data['fields'] if f['label'] == 'private key')
with open('/tmp/hetzner_key', 'w') as f: f.write(key + '\n')
os.chmod('/tmp/hetzner_key', stat.S_IRUSR | stat.S_IWUSR)
print('Key written to /tmp/hetzner_key')
"
ssh -i /tmp/hetzner_key root@<host>
```

---

## Hetzner Cloud API

API token lives in 1Password as `Hetzner - Default - API Token`. Base URL: `https://api.hetzner.cloud/v1`

```sh
TOKEN=$(op item get "Hetzner - Default - API Token" --reveal --format json | python3 -c "import sys,json; data=json.load(sys.stdin); print(next(f['value'] for f in data['fields'] if f.get('value') and len(f['value']) > 10))")

# List servers
curl -s -H "Authorization: Bearer $TOKEN" https://api.hetzner.cloud/v1/servers

# List SSH keys
curl -s -H "Authorization: Bearer $TOKEN" https://api.hetzner.cloud/v1/ssh_keys

# Rebuild server with cloud-init (wipes disk)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"image": "ubuntu-24.04", "user_data": "<cloud-init yaml>"}' \
  https://api.hetzner.cloud/v1/servers/{id}/actions/rebuild
```

**Notes:**
- Assigning/removing Primary IPs requires the server to be **powered off**
- New servers default to IPv6-only (no IPv4) — add a Primary IP if needed (~€0.50/mo)
- See `server-provisioning.md` for the full Tailscale bootstrap pattern

---

## Google Workspace Skills

The `gws-gmail` and `gws-gmail-read` skills in `.claude/skills/` are sourced from https://github.com/googleworkspace/cli

For any ambiguity or issues with Google Workspace tasks (Gmail, Calendar, Drive, etc.), check this repo for additional skills or capabilities that can be pulled in.

---

## Remote Model Switching

The Ararat remote control session can switch between Claude models via the `send-cmd.sh` script:

```sh
./send-cmd.sh "/model haiku"
./send-cmd.sh "/model sonnet"
./send-cmd.sh "/model opus"
```

The script sends commands via dtach to the socket at `/tmp/ararat.sock` (the same session that handles Telegram messages). Claude can execute these commands autonomously to switch its own model during a conversation.

## Session Clearing

When you ask to clear the session (e.g., "clear pls", "clear", or similar), run:

```sh
./send-cmd.sh "/clear"
```

This clears the Ararat remote control session's context.

## Restarting Ararat

When asked to restart (e.g., "restart", "restart yourself", "restart ararat"), run:

```sh
systemctl --user restart ararat.service
```

This restarts the Ararat Telegram bot systemd user service. Note: restarting will terminate the current session, so this should be the last action taken.

## Git Permissions

Claude has write access to the repository via a fine-grained GitHub PAT token with "Contents" write permission scoped to this repo only. I can autonomously commit and push changes.

### Commit Strategy

When asked to commit and push, split unstaged changes into **atomic commits by subject/change**. Each commit should represent a single logical unit of work. Then push all commits together.

Example: If changes span 3 different features/fixes, create 3 separate commits with clear, focused messages — then push all of them.
