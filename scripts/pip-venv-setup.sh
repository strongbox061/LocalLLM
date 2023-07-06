#!/usr/bin/env bash
set -euo pipefail

# pip-venv-setup.sh — Legacy python -m venv and pip bootstrapper
# Creates an isolated Python environment and installs core dependencies.
# Usage: ./pip-venv-setup.sh [--recreate]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/venv"
RECREATE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --recreate)
            RECREATE=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Detect Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install it before running this script." >&2
    exit 1
fi

# Recreate or create venv
if [[ "$RECREATE" == true ]] && [[ -d "${VENV_DIR}" ]]; then
    echo "==> Recreating virtual environment at ${VENV_DIR}"
    rm -rf "${VENV_DIR}"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}"
    echo "==> Virtual environment created."
else
    echo "==> Virtual environment exists at ${VENV_DIR}."
fi

# Activate and upgrade pip
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip setuptools wheel

# Install requirements if present
if [[ -f "${PROJECT_ROOT}/requirements.txt" ]]; then
    echo "==> Installing requirements from requirements.txt..."
    pip install -r "${PROJECT_ROOT}/requirements.txt"
else
    echo "==> No requirements.txt found. Skipping dependency installation."
fi

echo "==> Done. Activate with: source ${VENV_DIR}/bin/activate"
