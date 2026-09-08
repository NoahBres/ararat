# Project Notes

Personal automation repo for Noah — trackers, notes, personal tooling, the `rtk-api`
service, and the nix-darwin config. Work happens in an ordinary interactive Claude Code
session in this directory; answers go to the terminal.

**Personal-assistant habits still apply.** When Noah mentions something worth remembering
— a person, a preference, a plan, a habit — file it in `private-data/` and say so. Connect
dots across sessions rather than answering narrowly.

> **Note:** the Ararat Telegram bot was retired (Sept 2026). The bot, its patched MCP
> plugin, the `--remote-control` shim, and the `com.noahbres.ararat` launchd agent are all
> gone; the trackers and `private-data/` live on and are updated from normal sessions.
> `notes/ararat-telegram-retired.md` has the inventory, the data map, and revival steps.
>
> **Do not delete `~/.claude/channels/telegram/.env`.** The bot token there is independent
> of the retired remote-control session, and `tools/things-today-tracker.py` still reads it
> to send its daily stale-task alert.

---

## Remembering Instructions

**Always default to CLAUDE.md** for any instruction, rule, or preference the user wants saved. Do NOT use the memory system unless the content is sensitive, private, or personally identifying (i.e., something that shouldn't be in a public GitHub repo). If in doubt, use CLAUDE.md.

---

## Scheduling & Timezones

When scheduling cron jobs or reminders, always be timezone-aware:

- **Noah's home timezone is Pacific (PDT, UTC-7 / PST, UTC-8)**
- Always convert requested local times to UTC for cron expressions
- If the user's timezone is ambiguous (e.g. traveling, no prior context), **ask before scheduling**
- Example: "10am tomorrow" from California = 17:00 UTC (PDT)

---

## Caffeine Tracking

Log entries to `private-data/caffeine-tracker.md` whenever Noah reports caffeine intake in a session. Use the current time in both UTC and Pacific (default timezone).

**Format:** append a row to the markdown table:
```
| 40 | Red Bull (half) | 2026-04-09 18:15 UTC | 2026-04-09 11:15 PDT |
```

**Default drink:** "Red Bull" = Sugar Free Red Bull 8.4 fl oz = **80mg**. If Noah says "drank a red bull", "had a caffeine", or similar without specifying, log 80mg Red Bull.

**Half drinks:** If Noah says "half a Red Bull" or "80mg red bull half", log **half the stated mg** (e.g. half of 80mg = 40mg).

---

## Mood Tracking

Log entries to `private-data/mood-tracker.md` whenever Noah reports his mood in a session. Use exact UTC and Pacific times. Mood is free-form text.

**Format:** append a row to the markdown table:
```
| 2026-04-14 03:00 UTC | 2026-04-13 20:00 PDT | excellent, focused | beautiful weather |
```

---

## Alcohol Tracking

Log entries to `private-data/alcohol-tracker.md` whenever Noah reports drinking in a session. Use Pacific date.

**Format:** append a row to the markdown table:
```
| 2026-04-13 | evening | medium | wine |
```

**Time of day options:** `afternoon`, `evening`, `night` (default to `evening` if unspecified).

**Quantity options:** `little`, `medium`, `lot` (use Noah's words or judgment if vague).

**Notes:** optional — type of drink, occasion, etc. Leave blank if nothing notable.

---

## Nicotine Tracking

Log entries to `private-data/nicotine-tracker.md` whenever Noah reports nicotine intake in a session. Use the current time in both UTC and Pacific (default timezone).

**Format:** append a row to the markdown table:
```
| 2 | Nicotine Gum 2mg (Polacrilex) | 2026-04-13 18:00 UTC | 2026-04-13 11:00 PDT |
```

**Default product:** "nicotine gum", "gum", "had a nicotine", or similar without specifying = **2mg Polacrilex nicotine gum (Target)**.

---

## Google Workspace

- **Gmail**: Use `gws-gmail` / `gws-gmail-read` skills
- **Calendar**: Use the Google Calendar MCP tools (already loaded); authenticate with `mcp__claude_ai_Google_Calendar__authenticate` if needed
- For other Google Workspace tasks (Drive, Docs, etc.), check https://github.com/googleworkspace/cli for additional skills

---

## Available Capabilities

**Keep this section up to date.** Whenever a new skill or tool is added to the repo, add it here. This is the authoritative reference for what's available in this session.



### MCP Tools
- **Google Calendar** — `mcp__claude_ai_Google_Calendar__authenticate` + calendar tools (read/create events)
- **CardPointers** — `list_my_cards`, `recommend_card`, `search_my_offers`, `list_my_offers` (credit card recommendations and offers)

### Skills
- **ship** — commit all unstaged changes as atomic commits (with secrets scan) and push; triggered by "ship", "ship it", "commit and push"
- **gws-gmail** / **gws-gmail-read** — send and read Gmail
- **imessage-lookup** — look up iMessages by contact name (resolves name → identifier → chat.db)
- **contacts-search** — fuzzy-search contacts by name; returns phone numbers / emails
- **agent-browser** — browser automation CLI (navigate, fill forms, click, screenshot, scrape); prefer it over built-in browser/web tools. Vendored at `.claude/skills/agent-browser/`, pinned in `nixos-config/skills-lock.json`
- **shiori-sh** — save URLs to Shiori and search/list saved bookmarks (`bunx @shiori-sh/cli`)
- Other Google Workspace skills available at https://github.com/googleworkspace/cli

### Local Files

**`notes/`**
- `notes/SHOPPING-GENERAL.md` — general shopping list; read/update when user asks about shopping
- `notes/NOTES.md` — repo-wide implementation notes (the rtk Cloudflare Tunnel, deploy-rs, DNS). **rtk-api's own notes live in `rtk-api/notes/`, not here.**
- `notes/llm-projects.md` — curated list of interesting LLM-related projects

**`rtk-api/`**
- Private authenticated HTTP/MCP server for personal tools (Things 3, iMessage), deployed on `rtk` as `api.noahbres.com` / `mcp.noahbres.com`.
- **All rtk-api notes live in `rtk-api/notes/`** — keep them there, not in `notes/`:
  - `rtk-api/notes/NOTES.md` — current state: architecture, auth/principals, credentials, Cloudflare objects, gotchas, open items.
  - `rtk-api/notes/plan.md` — original design doc and rollout phases.
  - `rtk-api/README.md` — dev/usage docs.

**`tools/`**
- `tools/things-today-tracker.py` — flags Things 3 "Today" tasks that have been sitting for 10+ days and sends a Telegram alert; see `tools/things-today-tracker.md` for full docs
- `tools/things-today-tracker.md` — documentation for the things-today-tracker script (launchd schedule, usage, data store location)
- `tools/imessage-query.py` — queries chat.db for messages by phone/email identifier; used by the imessage-lookup skill
- `tools/contacts-search.py` — fuzzy-searches AddressBook contacts by name; used by the contacts-search skill
- `tools/sync-private-data.sh` — bidirectional rsync of `private-data/` between this machine and `rtk.local`
- `rtk-api/deploy.sh` — deploys rtk-api on `rtk` (git pull, `uv sync --frozen`, restart launchd agent, poll `/health`); `--remote` runs it over SSH from `rnn`

**`private-data/`** (gitignored)

**Proactive updates:** When you encounter information that seems useful to remember — about people, preferences, habits, recurring situations, or anything personal — write it to the appropriate file in `private-data/` without being asked. Always tell Noah when you do (e.g. "I've noted X's address in contacts."). Use good judgment about what's worth keeping.

- `private-data/contacts.md` — private contact notes (addresses, phone numbers, gate codes, etc.); **fuzzy-search this first** whenever Noah asks about a person by name (e.g. "is X in contacts?", "what's X's address?", "do we have notes on X?").
- `private-data/caffeine-tracker.md` — caffeine intake log; append entries when Noah reports caffeine
- `private-data/nicotine-tracker.md` — nicotine intake log; append entries when Noah reports nicotine
- `private-data/alcohol-tracker.md` — alcohol intake log (date, time of day, little/medium/lot); append entries when Noah reports drinking
- `private-data/mood-tracker.md` — mood log with exact UTC + Pacific timestamps; free-form mood text
- `private-data/sleep-tracker.md` — sleep log
- `private-data/things-today-tracker.json` — persistent UUID → first_seen map used by things-today-tracker.py
- `private-data/event-notes.md` — temporary notes tied to upcoming events (trips, reservations, deadlines, etc.); search this when Noah asks about something specific. Each entry has an expiry date — when expired or the event passes, **move** the entry to `event-notes-archive.md` rather than deleting it.
- `private-data/event-notes-archive.md` — cold storage for expired event notes. Do NOT load this proactively — only search it if Noah explicitly asks about something historical.

---

## nixos-config

`nixos-config/` is a nix-darwin config, merged into this repo as a subfolder (history preserved). Use it whenever Noah asks about Nix, nix-darwin, or system/host configs. Key paths: `nixos-config/flake.nix`, `nixos-config/hosts/` (per-host configs, e.g. `hosts/common/darwin/home.nix`).

**Running its `just` recipes:** the root `justfile` exposes `nixos-config/justfile` as a module, so
every recipe is callable from the repo root as `just nix <recipe>` (e.g. `just nix build-rtk`) and
runs with `nixos-config/` as its working directory. `just` alone lists them.

**Deploying to rtk:** nix changes to rtk go through deploy-rs and need Noah's sudo password, so
agents never run `just nix deploy-rtk` themselves. Prepare and verify (`just nix build-rtk`, `nix
eval`), then ask Noah to run `just nix build-deploy-rtk`, and confirm afterwards over SSH (`readlink
/nix/var/nix/profiles/system` must advance — silent rollbacks have happened). Python-only changes
to rtk-api deploy without root via `rtk-api/deploy.sh --remote`. Full notes: `notes/NOTES.md`
(host/deploy) and `rtk-api/notes/NOTES.md` (the service itself).

---

## System Environment

This machine runs **macOS**. Standard macOS tooling applies.

**Python package management:** Always use `uv` (or `uvx` for one-off tools) instead of `pip` or `pip3` directly.

---

## Things 3

**Reminders default:** Always create reminders in Things 3 (via URL scheme) unless Noah explicitly says to use cron.

**Auth token:** stored in `.env` as `THINGS_AUTH_TOKEN` — required for update operations via URL scheme.

**Supported URL scheme actions:** `add`, `add-project`, `update`, `update-project`, `show`, `search`. There is no `delete` action — items cannot be deleted via URL scheme.

```sh
# Update a todo (requires auth token + UUID from uvx things-cli --json)
open "things:///update?auth-token=$THINGS_AUTH_TOKEN&id=UUID&title=New%20Title"
```

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

---

## Git Permissions

You have write access to the repository via a fine-grained GitHub PAT token with "Contents" write permission scoped to this repo only. You can autonomously commit and push changes.

### Commit Strategy

When asked to commit and push, split unstaged changes into **atomic commits by subject/change**. Each commit should represent a single logical unit of work. Then push all commits together.

Example: If changes span 3 different features/fixes, create 3 separate commits with clear, focused messages — then push all of them.

### Secrets Sweep

Before committing anything, scan all staged files for secrets — API keys, tokens, passwords, and credentials. Look for patterns like hardcoded tokens in example commands, `.env`-style values embedded in docs, etc. If found, redact or replace with a placeholder (e.g. `$VAR_NAME`) and flag it to Noah before proceeding.
