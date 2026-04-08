# Project Notes

## Things 3

### Reading todos (via `uvx things-cli`)
```sh
uvx things-cli today          # today's tasks
uvx things-cli inbox          # inbox
uvx things-cli todos          # all todos
uvx things-cli anytime        # anytime list
uvx things-cli someday        # someday list
uvx things-cli projects       # all projects
uvx things-cli search "query" # search

# Filters
uvx things-cli -p "Project Name" todos   # filter by project
uvx things-cli -a "Area Name" todos      # filter by area
uvx things-cli -t "tag" todos            # filter by tag

# Output formats
uvx things-cli --json today              # JSON output
uvx things-cli --csv --recursive all     # CSV with nesting
uvx things-cli --recursive areas         # nested tree view
```

### Writing todos (via URL scheme)
```sh
# Add to inbox
open "things:///add?title=My%20Todo"

# Add to today
open "things:///add?title=My%20Todo&when=today"

# Add with notes, deadline, tags
open "things:///add?title=My%20Todo&when=today&notes=Some%20notes&deadline=2026-04-01&tags=work"

# Add to a specific list/project
open "things:///add?title=My%20Todo&list=Project%20Name"

# Add to someday
open "things:///add?title=My%20Todo&when=someday"

# Create a project
open "things:///add-project?title=My%20Project&when=today"

# Navigate to a view
open "things:///show?id=today"
open "things:///show?id=inbox"
```

`when` accepts: `today`, `tomorrow`, `someday`, `anytime`, or a date like `2026-04-01`.

For **updating** existing todos, Things 3 requires an auth token (Settings > General > Enable Things URLs).

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
