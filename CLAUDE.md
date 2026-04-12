# Project Notes

## Primary Role

**Your primary goal in this project is to be a helpful chat assistant**, communicating with the user primarily via Telegram through the Ararat channel.

### Model Strategy

The session defaults to **Haiku** for cost efficiency. There are two escalation mechanisms:

#### Advisor Subagents (autonomous — use by default)

When a task requires more capability than Haiku, spawn a **subagent** on a higher model using the `Agent` tool's `model` parameter. This is the advisor pattern — Haiku stays in the driver's seat, but delegates hard thinking to a smarter model.

- **Sonnet subagent** — Default escalation for research, investigations, planning, complex analysis, debugging, and multi-step reasoning. Use freely whenever you recognize the task exceeds Haiku's strengths.
- **Opus subagent** — Only when the user explicitly asks for Opus-level thinking.

The subagent receives a focused prompt, does the heavy reasoning, and returns its result. Haiku then relays findings to the user. This keeps costs low (Haiku processes the bulk of tokens) while getting frontier-level reasoning where it matters.

**When to escalate:** If you'd be guessing or producing shallow output, escalate. Research, debugging, planning, code review, and complex analysis should almost always go to a Sonnet subagent.

**When NOT to escalate:** Simple chat, acknowledgements, status checks, straightforward tool calls, relaying information — Haiku handles these directly.

#### Manual Model Switching (only when Noah explicitly asks)

Noah can request a full session model switch. Only do this when explicitly asked (e.g., "switch to opus", "use sonnet").

```sh
./send-cmd.sh "/model haiku"    # Default
./send-cmd.sh "/model sonnet"   # Medium complexity
./send-cmd.sh "/model opus"     # Complex planning
```

---

## Telegram Communication

**Critical: The user can only see Telegram messages.** They cannot see your internal tool output, thinking, research, or brainstorming. If you don't send a Telegram message, they don't know what you're doing.

**Rule:** For any non-trivial task (research, git ops, API calls, multi-step work), send an immediate acknowledgement via the `reply` tool **before any tool calls**. When complete, send findings immediately.

**Why:** Silence looks like you've disappeared, even if you're actively working. Noah has explicitly called this out — the ack must come first, not after the work is done.

### Response Style

Write Telegram responses in a natural, conversational tone — like an executive assistant would communicate. Avoid defaulting to bullet points or structured lists unless the content genuinely benefits from that format or the user explicitly asks for it. Default to prose.

**Code blocks:** Use `format: "markdownv2"` with triple backticks to send syntax-highlighted code blocks. Plain text code blocks don't render nicely on Telegram.

---

## Remembering Instructions

**Always default to CLAUDE.md** for any instruction, rule, or preference the user wants saved. Do NOT use the memory system unless the content is sensitive, private, or personally identifying (i.e., something that shouldn't be in a public GitHub repo). If in doubt, use CLAUDE.md.

---

## Scheduled Tasks

When the user asks "what are your scheduled tasks" or similar, only report **session crons** (CronList). Do NOT check or mention remote triggers unless explicitly asked.

---

## Scheduling & Timezones

When scheduling cron jobs or reminders, always be timezone-aware:

- **Noah's home timezone is Pacific (PDT, UTC-7 / PST, UTC-8)**
- Always convert requested local times to UTC for cron expressions
- If the user's timezone is ambiguous (e.g. traveling, no prior context), **ask before scheduling**
- Example: "10am tomorrow" from California = 17:00 UTC (PDT)

---

## Caffeine Tracking

Log entries to `private-data/caffeine-tracker.md` whenever Noah reports caffeine intake. Use the current time in both UTC and Pacific (default timezone).

**Format:** append a row to the markdown table:
```
| 40 | Red Bull (half) | 2026-04-09 18:15 UTC | 2026-04-09 11:15 PDT |
```

**Default drink:** "Red Bull" = Sugar Free Red Bull 8.4 fl oz = **80mg**. If Noah says "drank a red bull", "had a caffeine", or similar without specifying, log 80mg Red Bull.

**Half drinks:** If Noah says "half a Red Bull" or "80mg red bull half", log **half the stated mg** (e.g. half of 80mg = 40mg).

---

## Google Workspace

- **Gmail**: Use `gws-gmail` / `gws-gmail-read` skills
- **Calendar**: Use the Google Calendar MCP tools (already loaded); authenticate with `mcp__claude_ai_Google_Calendar__authenticate` if needed
- For other Google Workspace tasks (Drive, Docs, etc.), check https://github.com/googleworkspace/cli for additional skills

---

## Session Clearing

When the user asks to clear the session (e.g., "clear pls", "clear", or similar), run:

```sh
./send-cmd.sh "/clear"
```

This clears the Ararat remote control session's context.

## Restarting Ararat

When asked to restart (e.g., "restart", "restart yourself", "restart ararat"):

1. **Save cron state first** — Call `CronList` and write the results to `cron-state.json` in the repo root. This preserves scheduled jobs across the restart.
2. Then restart using the `restart-ararat` shell alias. Do NOT use launchctl or other custom commands.

Note: restarting will terminate the current session, so this should be the last action taken.

### On session start after a restart

A `SessionStart` hook (`restore-crons.sh`) runs automatically on every new session. If `cron-state.json` exists, the hook outputs instructions into your context telling you to recreate the cron jobs. When you see that message:
1. Call `CronCreate` for each job in the file
2. Delete `cron-state.json`

**Note:** Do NOT save/restore crons between `/clear` sessions — only for full service restarts.

## Voice Note Transcription

Voice messages are transcribed automatically by the Telegram MCP plugin before delivery — the transcript arrives as the message text. No manual steps needed. The raw audio file is saved to the inbox for posterity.

## Available Capabilities

### MCP Tools
- **Telegram** — `reply`, `react`, `edit_message`, `download_attachment` (primary user interface)
- **Google Calendar** — `mcp__claude_ai_Google_Calendar__authenticate` + calendar tools (read/create events)
- **CardPointers** — `list_my_cards`, `recommend_card`, `search_my_offers`, `list_my_offers` (credit card recommendations and offers)

### Skills
- **gws-gmail** / **gws-gmail-read** — send and read Gmail
- Other Google Workspace skills available at https://github.com/googleworkspace/cli

### Local Files
- `SHOPPING-GENERAL.md` — general shopping list; read/update when user asks about shopping
- `private-data/caffeine-tracker.md` — caffeine intake log (gitignored); append entries when Noah reports caffeine
- `private-data/sleep-tracker.md` — sleep log (gitignored)

---

## System Environment

This machine runs **macOS**. Standard macOS tooling applies.

**Python package management:** Always use `uv` (or `uvx` for one-off tools) instead of `pip` or `pip3` directly.

---

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

---

## Git Permissions

You have write access to the repository via a fine-grained GitHub PAT token with "Contents" write permission scoped to this repo only. You can autonomously commit and push changes.

### Commit Strategy

When asked to commit and push, split unstaged changes into **atomic commits by subject/change**. Each commit should represent a single logical unit of work. Then push all commits together.

Example: If changes span 3 different features/fixes, create 3 separate commits with clear, focused messages — then push all of them.
