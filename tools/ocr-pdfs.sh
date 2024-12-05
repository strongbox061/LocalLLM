#!/usr/bin/env bash
# Every PDF in the folder gets a copy with text in <folder>/ocr/ (same name); the originals are never changed. Pages
# that already have text are left as they are; a PDF already done (its copy is newer) is skipped.
#   tools/ocr-pdfs.sh <folder> [languages]     languages: Tesseract's codes, default spa+eng
set -euo pipefail

DIR="$(cd -- "$1" && pwd)"
LANGS="${2:-spa+eng}"
OUT="$DIR/ocr"

mkdir -p "$OUT"

for pdf in "$DIR"/*.pdf; do
  [ -f "$pdf" ] || continue
  name="$(basename "$pdf")"
  out_pdf="$OUT/$name"

  # Skip if OCR copy is newer than source
  [ "$out_pdf" -nt "$pdf" ] && continue

  echo "OCR: $name"
  pdftotext -layout "$pdf" "$OUT/${name%.pdf}.txt"
  tesseract "$OUT/${name%.pdf}.txt" "$OUT/${name%.pdf}" -l "$LANGS"
done
