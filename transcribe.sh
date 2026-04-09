#!/usr/bin/env bash
# Usage: ./transcribe.sh <audio-file-path>
# Transcribes audio using OpenAI Whisper API.
# Requires OPENAI_API_KEY in .env or environment.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <audio-file-path>" >&2
  exit 1
fi

AUDIO_FILE="$1"

if [[ ! -f "$AUDIO_FILE" ]]; then
  echo "File not found: $AUDIO_FILE" >&2
  exit 1
fi

# Load .env if present
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$SCRIPT_DIR/.env"; set +a
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is not set" >&2
  exit 1
fi

curl -s https://api.openai.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -F "file=@$AUDIO_FILE" \
  -F "model=whisper-1" \
  -F "response_format=text"
