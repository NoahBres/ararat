#!/usr/bin/env bash

# Send command to tmux ararat session
# Usage: ./send-cmd.sh "command"

SESSION="ararat"
CMD="${1:-/model haiku}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session not found: $SESSION"
  exit 1
fi

tmux send-keys -t "$SESSION" "$CMD" Enter
