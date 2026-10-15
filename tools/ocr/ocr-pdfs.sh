#!/usr/bin/env bash
# Gives scanned PDFs a text layer, so Open WebUI can read and search them (README, "Protected chats in Open WebUI").
# Every PDF in the folder gets a copy with text in <folder>/ocr/ (same name); the originals are never changed. Pages
# that already have text are left as they are; a PDF already done (its copy is newer) is skipped.
#   tools/ocr/ocr-pdfs.sh <folder> [languages]     languages: Tesseract's codes, default spa+eng
# Uses Tesseract and OCRmyPDF (install.sh installs them where Open WebUI runs). RAM only while it runs (~0.3-1 GB).
set -euo pipefail

[[ $# -ge 1 && -d "$1" ]] || { echo "Usage: $0 <folder with PDFs> [languages, default spa+eng]" >&2; exit 2; }
DIR="$(cd -- "$1" && pwd)"
LANGS="${2:-spa+eng}"
command -v ocrmypdf >/dev/null || { echo "ERROR: ocrmypdf is not installed: run install.sh (OCR step)." >&2; exit 1; }
for lang in ${LANGS//+/ }; do
    tesseract --list-langs 2>/dev/null | grep -qx "$lang" \
        || { echo "ERROR: Tesseract has no '$lang' language data (pacman: tesseract-data-$lang)." >&2; exit 1; }
done

OUT="$DIR/ocr"
mkdir -p "$OUT"
done_count=0 skipped=0 failed=0
shopt -s nullglob nocaseglob
for pdf in "$DIR"/*.pdf; do
    target="$OUT/$(basename -- "$pdf")"
    if [[ -f "$target" && "$target" -nt "$pdf" ]]; then
        skipped=$((skipped + 1))
        continue
    fi
    echo "OCR: $(basename -- "$pdf")"
    # --skip-text: pages with text stay as they are; exit code 6 = the whole file already had text (copied as is).
    rc=0
    ocrmypdf --skip-text --rotate-pages --deskew -l "$LANGS" --output-type pdf --quiet "$pdf" "$target" || rc=$?
    if [[ $rc -eq 0 ]]; then
        done_count=$((done_count + 1))
    elif [[ $rc -eq 6 ]]; then
        cp -- "$pdf" "$target"
        done_count=$((done_count + 1))
    else
        rm -f -- "$target"
        failed=$((failed + 1))
        echo "  failed (ocrmypdf exit code $rc)" >&2
    fi
done
echo "Done: $done_count PDF(s) in $OUT, $skipped already done, $failed failed. Upload the copies in ocr/."
[[ $failed -eq 0 ]]
