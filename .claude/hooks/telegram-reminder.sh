#!/bin/bash
# Stop hook: fires at the end of every Claude response turn.
# Reminds Claude to send a Telegram reply if it hasn't already.

echo "REMINDER: Before ending this turn, make sure you've sent a reply via the Telegram reply tool. The user cannot see your internal transcript — only Telegram messages reach them."
exit 0
