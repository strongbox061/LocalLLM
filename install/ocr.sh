#!/usr/bin/env bash
# OCR for scanned PDFs (tools/ocr/ocr-pdfs.sh): Tesseract with Spanish and English data, and OCRmyPDF (with uv, like
# Open WebUI; Arch has no package for it). It belongs where Open WebUI runs; nothing runs in the background, so it
# takes disk only (about 0.2 GB), and RAM only while a folder is being read.
#   install/ocr.sh            install what is missing
#   install/ocr.sh --update   also update OCRmyPDF
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

PACKAGES=(tesseract tesseract-data-spa tesseract-data-eng tesseract-data-osd ghostscript)
if pacman -Q "${PACKAGES[@]}" >/dev/null 2>&1; then
    echo "Tesseract and its Spanish and English data are already installed."
else
    sudo pacman -S --needed "${PACKAGES[@]}"
fi

if command -v ocrmypdf >/dev/null && [[ "${1:-}" != "--update" ]]; then
    echo "OCRmyPDF is already installed ($(ocrmypdf --version))."
elif command -v ocrmypdf >/dev/null; then
    uv tool upgrade ocrmypdf
else
    uv tool install ocrmypdf
fi
