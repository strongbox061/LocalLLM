#!/usr/bin/env bash
# OCR for scanned PDFs (tools/ocr/ocr-pdfs.sh): Tesseract with Spanish and English data.
# Requires a working Python 3 environment to run OCRmyPDF (installed via pip).

set -euo pipefail

UPDATE=false
if [[ "${1:-}" == "--update" ]]; then
    UPDATE=true
fi

PACKAGES=(tesseract tesseract-data-spa tesseract-data-eng tesseract-data-osd ghostscript)

install_packages() {
    echo "Installing OCR packages..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y "${PACKAGES[@]}"
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm "${PACKAGES[@]}"
    else
        echo "ERROR: Unsupported package manager. Please install the following packages manually:"
        printf '  %s\n' "${PACKAGES[@]}"
        exit 1
    fi
}

install_ocrmypdf() {
    echo "Installing OCRmyPDF via pip..."
    pip3 install --user ocrmypdf
}

if [[ "$UPDATE" == true ]]; then
    install_packages
    install_ocrmypdf
else
    missing=()
    for pkg in "${PACKAGES[@]}"; do
        if ! command -v "$pkg" &>/dev/null; then
            missing+=("$pkg")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        install_packages
    fi

    if ! command -v ocrmypdf &>/dev/null; then
        install_ocrmypdf
    fi
fi

echo "OCR setup complete. Use tools/ocr/ocr-pdfs.sh to process scanned PDFs."
