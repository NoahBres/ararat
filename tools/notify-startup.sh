#!/usr/bin/env bash
# SessionStart hook: sends a Telegram message when Ararat starts up.
# Fires on every new session (restart or /clear).

ENV_FILE="$HOME/.claude/channels/telegram/.env"

if [ ! -f "$ENV_FILE" ]; then
  exit 0
fi

# Load bot token and chat ID
source "$ENV_FILE"

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
  exit 0
fi

curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="$TELEGRAM_CHAT_ID" \
  -d text="Ara is here!" \
  > /dev/null

exit 0
