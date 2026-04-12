# Things 3 Today Tracker

Tracks how long tasks have been sitting in Things 3 "Today" and sends a Telegram alert for tasks ignored for 10+ days.

## How it works
1. Calls `uvx things-cli --json today` to get current Today tasks
2. Loads `private-data/things-today-tracker.json` — a UUID → first_seen map
3. Adds any new task UUIDs with the current timestamp; removes nothing (tasks disappear naturally when completed)
4. Flags any task where `(now - first_seen) >= 10 days`
5. If flagged tasks exist, sends a Telegram message directly via the Bot API

## Key files
- **Script:** `tools/things-today-tracker.py`
- **Data store:** `private-data/things-today-tracker.json` (gitignored — persists across sessions)
- **Bot token:** read from `~/.claude/channels/telegram/.env` (TELEGRAM_BOT_TOKEN)
- **launchd agent:** defined in `~/Developer/nixos-config/hosts/rtk/home.nix` as `launchd.agents.things-today-tracker`
  - Label: `com.noahbres.things-today-tracker`
  - Schedule: daily at 9:07am
  - Logs: `/tmp/things-today-tracker.log` / `/tmp/things-today-tracker-error.log`

## Manual usage
```sh
# Dry run — shows flagged tasks without sending Telegram
python3 tools/things-today-tracker.py --dry-run

# Custom threshold
python3 tools/things-today-tracker.py --threshold 5

# Check launchd agent status
launchctl list | grep things-today-tracker
```

## Applying the launchd config
After changes to nixos-config, run `darwin-rebuild switch` to activate/update the launchd agent.
