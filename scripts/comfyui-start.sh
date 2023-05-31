#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${SCRIPT_DIR}/../comfyui"
VENV_DIR="${REPO_DIR}/venv"
COMFYUI_PORT="${COMFYUI_PORT:-8188}"

# ── Prerequisites ────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "[comfyui-start] python3 not found — install it first" >&2
  exit 1
fi

if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
  echo "[comfyui-start] pip not found — install it first" >&2
  exit 1
fi

# ── Clone / update repo ─────────────────────────────────────────────────
if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "[comfyui-start] Cloning ComfyUI …"
  git clone https://github.com/comfyanonymous/ComfyUI.git "${REPO_DIR}"
fi

# ── Virtual environment ─────────────────────────────────────────────────
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[comfyui-start] Creating virtualenv at ${VENV_DIR} …"
  python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"

# ── GPU check (optional, runs on CPU if missing) ────────────────────────
if command -v nvidia-smi &>/dev/null; then
  echo "[comfyui-start] NVIDIA GPU detected — CUDA mode enabled"
else
  echo "[comfyui-start] No NVIDIA GPU found — running on CPU (slow)" >&2
fi

# ── Install dependencies ────────────────────────────────────────────────
echo "[comfyui-start] Installing requirements …"
pip install --upgrade pip
pip install -r "${REPO_DIR}/requirements.txt"

# ── Start server ────────────────────────────────────────────────────────
echo "[comfyui-start] Starting ComfyUI on http://localhost:${COMFYUI_PORT}"
cd "${REPO_DIR}"
python main.py --port "${COMFYUI_PORT}"
