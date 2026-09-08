# Retired: the Ararat Telegram assistant

Retired **2026-09-08**. The bot wasn't used enough to justify running a persistent Claude
session for it. Nothing was destroyed — this note is the map back.

**Last commit with everything intact: `b610057`.** Every removed file is recoverable with
`git show b610057:<path>` or `git checkout b610057 -- <path>`.

---

## What the data is, and where it still lives

**None of the data was touched.** This is the part worth knowing if you're only here to read
the trackers.

- `private-data/` (gitignored, local only) — the caffeine, nicotine, alcohol, mood, and sleep
  trackers, plus `contacts.md`, `event-notes.md`, and `things-today-tracker.json`. Untouched,
  and still updated from ordinary Claude Code sessions in this repo; the tracker formats are
  documented in `CLAUDE.md`.
- `tools/sync-private-data.sh` — still the way `private-data/` moves between this machine and
  `rtk.local`. Unaffected by the teardown.
- `~/.claude/channels/telegram/` — **deliberately left in place.** Holds the live bot token
  (`.env`), the pairing allowlist (`access.json`), and `inbox/` with archived voice-note
  audio. Deleting it is what would break things; see the next section.

## What is still live

`tools/things-today-tracker.py` — the daily 09:07 launchd job on `rtk`
(`com.noahbres.things-today-tracker`) that flags Things 3 "Today" tasks sitting for 10+ days
and DMs about them. **It still works,** because the bot token is independent of the retired
remote-control session: it posts straight to the Bot API with a hardcoded chat id. Kept on
purpose. Don't delete `~/.claude/channels/telegram/.env`.

The rtk-api approval bot is a *separate* Telegram bot with its own token, also still live —
see `rtk-api/notes/NOTES.md`.

## What was removed

| Path | What it was |
|---|---|
| `telegram-plugin/` | Vendored patched Telegram MCP plugin (`server.ts`, ~44KB), patched for the fire-and-forget `mcp.notification` bug in claude-code#42037 |
| `claude-telegram.sh` | `expect` shim: `claude --remote-control --name Ararat --dangerously-skip-permissions --dangerously-load-development-channels "server:telegram"` |
| `.mcp.json` | Repo-root MCP registration that spawned the plugin via `bun` for every session in this directory |
| `.claude/hooks/telegram-reminder.sh` | Stop hook nagging every turn to send a Telegram reply |
| `tools/notify-startup.sh` | SessionStart hook posting "Ara is here!" |
| `tools/restore-crons.sh` | SessionStart hook recreating crons from `cron-state.json` across service restarts |
| `tools/send-cmd.sh` | Sent slash commands (`/clear`, `/model haiku`) into the `ararat` tmux session |
| `launchd.agents.ararat` | In `nixos-config/hosts/rtk/home.nix`, with its tmux-wrapping start script and the `attach`/`restart`/`kill-ararat` aliases |

`CLAUDE.md` also lost the Haiku-driver/Sonnet-subagent escalation scheme (it existed only
because that session ran on Haiku to keep costs down), the Telegram communication rules, and
the restart/clear procedures.

## How the pieces fit, if you revive it

The architecture, in the order a message flowed:

1. **launchd** (`com.noahbres.ararat` on `rtk`, `KeepAlive`, `RunAtLoad`) ran a start script
   that did `git pull --ff-only`, then launched `claude-telegram.sh` inside a detached tmux
   session named `ararat`, then polled `tmux has-session` every 5s and exited non-zero when
   the session died so launchd would restart it.
2. **`claude-telegram.sh`** spawned Claude in `--remote-control` mode on the development
   channel `server:telegram`, and `expect`-ed the initial Enter prompt.
3. **The plugin** (`telegram-plugin/server.ts`) polled the Bot API, delivered messages as
   `<channel source="telegram" chat_id=...>` tags, and exposed `reply` / `react` /
   `edit_message` / `download_attachment`. It transcribed voice notes itself before delivery.
4. **`CLAUDE.md`** made the session behave as an EA and enforced "always answer via `reply`",
   since the transcript was invisible to the user.

### Revival steps

1. `git checkout b610057 -- telegram-plugin claude-telegram.sh .mcp.json tools/send-cmd.sh tools/notify-startup.sh tools/restore-crons.sh .claude/hooks`
2. `git show b610057:.claude/settings.json` and restore the `hooks` block (Stop +
   SessionStart), which was edited rather than deleted.
3. `git show b610057:nixos-config/hosts/rtk/home.nix` — reinstate `araratatStart`,
   `launchd.agents.ararat`, and the three aliases. Then `just nix build-rtk`, and have Noah
   run `just nix build-deploy-rtk` (deploy needs his sudo).
4. `git show b610057:CLAUDE.md` for the EA persona, model-escalation strategy, and Telegram
   communication rules.
5. `cd telegram-plugin && bun install`.
6. Credentials should still be at `~/.claude/channels/telegram/` — if not, re-pair with the
   `/telegram:access` skill.

### Caveats

- The plugin was a **local patched fork**, not the upstream one. Pulling upstream instead
  reintroduces the `mcp.notification` bug (claude-code#42037) unless it's been fixed since.
- The teardown booted the agent out on `rtk` (`launchctl bootout gui/$UID/com.noahbres.ararat`)
  before the deleting commit landed, so it couldn't `git pull` itself into a crash loop. Do
  the same in reverse: deploy the nix change, then confirm the agent is actually loaded —
  `enable = true` doesn't reliably bootstrap an agent that isn't there yet.
- The **bot itself still exists in BotFather.** It was never revoked or deleted, so a revival
  reuses the same bot and chat id. Revoking it is a manual step in Telegram if you'd rather
  cut it off for real.
