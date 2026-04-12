#!/usr/bin/env bash
# Outputs cron restore instructions if cron-state.json exists.
# Runs as a SessionStart hook — output is injected into Claude's context.
CRON_STATE="/home/noah/Developer/ararat/cron-state.json"

if [ -f "$CRON_STATE" ]; then
  echo "ARARAT STARTUP RESTORE: cron-state.json was found, indicating the service was restarted."
  echo "You MUST recreate the following cron jobs using CronCreate, then delete cron-state.json."
  echo "---"
  cat "$CRON_STATE"
  echo "---"
  echo "After recreating all cron jobs, delete cron-state.json."
fi
