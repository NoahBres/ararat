#!/usr/bin/env bash
# Usage: ./transcribe.sh <audio-file-path>
# Transcribes audio offline using mlx-whisper (Apple Silicon).
# Runs via uvx — no manual install required.

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

MODEL="${WHISPER_MODEL:-mlx-community/whisper-large-v3-mlx}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# mlx_whisper writes <basename>.txt to the output dir
BASENAME="$(basename "$AUDIO_FILE")"
NAME="${BASENAME%.*}"

uvx mlx_whisper \
  --model "$MODEL" \
  --output-dir "$TMPDIR" \
  --output-format txt \
  --verbose False \
  "$AUDIO_FILE" >&2

cat "$TMPDIR/${NAME}.txt"
