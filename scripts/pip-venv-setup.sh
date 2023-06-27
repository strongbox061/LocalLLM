#!/usr/bin/env bash
set -euo pipefail

# pip-venv-setup.sh — Legacy python -m venv and pip bootstrapper
# Creates an isolated Python environment and installs core dependencies.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/venv"

echo "==> Setting up Python virtual environment at ${VENV_DIR}"

# Detect Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install it before running this script." >&2
    exit 1
fi

# Create venv if it doesn't exist
if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}"
    echo "==> Virtual environment created."
else
    echo "==> Virtual environment already exists at ${VENV_DIR}."
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
