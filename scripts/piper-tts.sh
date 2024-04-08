#!/usr/bin/env bash
set -euo pipefail

# Local Piper TTS experiment — early integration for offline voice synthesis
# Requires: piper (https://github.com/rhasspy/piper) installed and on PATH

VOICE="${PIPER_VOICE:-en_US-lessac-medium}"
OUTPUT_DIR="${OUTPUT_DIR:-./tts-output}"
RATE="${PIPER_RATE:-1.0}"

usage() {
  echo "Usage: $0 [--voice <name>] [--rate <float>] <input-text>"
  echo "  --voice   Piper voice ID (default: en_US-lessac-medium)"
  echo "  --rate    Speech rate multiplier (default: 1.0)"
  echo "  <input>   Text to synthesize"
  exit 1
}

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --voice) VOICE="$2"; shift 2 ;;
    --rate)  RATE="$2"; shift 2 ;;
    --help|-h) usage ;;
    *) break ;;
  esac
done

[[ $# -lt 1 ]] && usage

INPUT_TEXT="$*"
mkdir -p "$OUTPUT_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUTPUT_FILE="${OUTPUT_DIR}/tts-${TIMESTAMP}.wav"

echo "Synthesizing: ${INPUT_TEXT}"
echo "Voice: ${VOICE} | Rate: ${RATE} -> ${OUTPUT_FILE}"

piper --model "${VOICE}" --output_file "${OUTPUT_FILE}" <<< "${INPUT_TEXT}"

if [[ -f "${OUTPUT_FILE}" ]]; then
  SIZE_KB="$(du -k "${OUTPUT_FILE}" | cut -f1)"
  echo "Done (${SIZE_KB} KB): ${OUTPUT_FILE}"
else
  echo "Error: Piper produced no output file" >&2
  exit 1
fi
