#!/usr/bin/env bash
set -euo pipefail

# Batch Tesseract OCR script — processes all images in a directory
# Usage: ./scripts/tesseract-batch.sh <input_dir> [output_dir]

INPUT_DIR="${1:?Usage: $0 <input_dir> [output_dir]}"
OUTPUT_DIR="${2:-${INPUT_DIR}/ocr_output}"

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "Error: '$INPUT_DIR' is not a directory" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

count=0
for img in "$INPUT_DIR"/*.{png,jpg,jpeg,tiff,bmp}; do
  [[ -e "$img" ]] || continue
  base="$(basename "$img" | sed 's/\.[^.]*$//')"
  tesseract "$img" "$OUTPUT_DIR/$base" -l eng
  echo "  OCR'd: $img -> $OUTPUT_DIR/$base.txt"
  count=$((count + 1))
done

echo "Done. Processed $count image(s)."
