#!/usr/bin/env bash
set -euo pipefail

# build-llama-cpu.sh — Build llama.cpp for CPU-only inference (GGML format).
# Usage: ./scripts/build-llama-cpu.sh [LLAMA_CPP_DIR]
# Builds the `main` binary with OpenBLAS acceleration.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LLAMA_CPP_DIR="${1:-${REPO_ROOT}/llama.cpp}"
BUILD_DIR="${LLAMA_CPP_DIR}/build"

BUILD_TYPE="${BUILD_TYPE:-Release}"
echo "==> Building llama.cpp for CPU (GGML q4_0, type=${BUILD_TYPE}) in ${LLAMA_CPP_DIR}"

if [[ ! -d "${LLAMA_CPP_DIR}/.git" ]]; then
  echo "Error: not a git repo — clone llama.cpp first:" >&2
  echo "  git clone https://github.com/ggerganov/llama.cpp.git ${LLAMA_CPP_DIR}" >&2
  exit 1
fi

cd "${LLAMA_CPP_DIR}"
git fetch --tags
LATEST_TAG="$(git tag -l 'v*' | sort -V | tail -1)"
echo "==> Checking out ${LATEST_TAG}"
git checkout "${LATEST_TAG}"

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

if [[ "${BUILD_TYPE}" == "Debug" ]]; then
  CXX_FLAGS="-O0 -g3 -march=native"
else
  CXX_FLAGS="-O3 -march=native"
fi

make -j"$(nproc)" \
  LLAMA_OPENBLAS=1 \
  BUILD=${BUILD_TYPE} \
  CXXFLAGS="${CXX_FLAGS}"

echo "==> Build complete: ${BUILD_DIR}/main"
echo "    GGML backend: openblas (CPU only)"
echo "    Binary size: $(du -h "${BUILD_DIR}/main" | cut -f1)"
