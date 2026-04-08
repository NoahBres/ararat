#!/usr/bin/env bash

# Send command to dtach session
# Usage: ./send-cmd.sh "command"

SOCKET="/tmp/ararat.sock"
CMD="${1:-/model haiku}"

if [ ! -S "$SOCKET" ]; then
  echo "Socket not found: $SOCKET"
  exit 1
fi

printf "%s\r" "$CMD" | dtach -p "$SOCKET"
