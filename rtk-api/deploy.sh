#!/usr/bin/env bash
# Deploy rtk-api on rtk: pull latest, sync deps, restart the launchd agent,
# then poll /health until it comes up (or times out).
#
# Run directly on rtk:
#   ./rtk-api/deploy.sh
#
# Run from rnn (SSHes into rtk via the `rtk` alias -- LAN/Tailscale first -- and runs the same
# steps there):
#   ./rtk-api/deploy.sh --remote
#
# The nix-managed launchd agent (nixos-config/hosts/rtk/home.nix) only needs
# re-applying (`just switch-rtk`) when home.nix itself changes -- this script
# is for ordinary code deploys.

set -euo pipefail

REMOTE=false
if [[ "${1:-}" == "--remote" ]]; then
  REMOTE=true
fi

remote_cmd='
set -euo pipefail
cd ~/Developer/ararat
git pull --ff-only
cd rtk-api
uv sync --frozen
launchctl kickstart -k gui/$UID/com.noahbres.rtk-api
for i in $(seq 1 10); do
  if curl -sf localhost:8787/health; then
    echo
    echo "rtk-api: healthy"
    exit 0
  fi
  sleep 1
done
echo "rtk-api: did not become healthy within 10s" >&2
exit 1
'

if [[ "$REMOTE" == "true" ]]; then
  ssh rtk "$remote_cmd"
else
  eval "$remote_cmd"
fi
