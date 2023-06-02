#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$ROOT_DIR/common.sh"

echo "=== Faster-Whisper CLI Transcription Test ==="
echo "Date:  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

if ! python3 -c "import faster_whisper" 2>/dev/null; then
  echo "ERROR: faster-whisper not installed. Run: pip install faster-whisper"
  exit 1
fi

TEST_AUDIO="${1:-$ROOT_DIR/test-audio.wav}"
if [[ ! -f "$TEST_AUDIO" ]]; then
  echo "No test audio at $TEST_AUDIO — skipping (pass)"
  exit 0
fi

OUTPUT_DIR="$ROOT_DIR/.whisper-output"
mkdir -p "$OUTPUT_DIR"

python3 -c "
from faster_whisper import WhisperModel
import os

device = 'cuda' if __import__('torch').cuda.is_available() else 'cpu'
compute = 'float16' if device == 'cuda' else 'int8'
model = WhisperModel('small', device=device, compute_type=compute)
segments, info = model.transcribe('$TEST_AUDIO', beam_size=5)
text = ' '.join(s.text for s in segments).strip()

os.makedirs('$OUTPUT_DIR', exist_ok=True)
with open('$OUTPUT_DIR/transcript.txt', 'w') as f:
    f.write(text)
print(f'Language: {info.language} (conf={info.language_probability:.2f})')
print(f'Text: {text}')
"

if [[ ! -s "$OUTPUT_DIR/transcript.txt" ]]; then
  echo "FAIL: Empty transcript generated"
  exit 1
fi

TRANSCRIPT_LEN=$(wc -c < "$OUTPUT_DIR/transcript.txt")
if [[ "$TRANSCRIPT_LEN" -lt 5 ]]; then
  echo "FAIL: Transcript too short ($TRANSCRIPT_LEN bytes)"
  exit 1
fi

echo "PASS: Transcription test completed successfully (${TRANSCRIPT_LEN} bytes)"
