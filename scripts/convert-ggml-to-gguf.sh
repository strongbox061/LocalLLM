#!/usr/bin/env bash
set -euo pipefail

# Convert GGML model weights to GGUF format (one-shot, ~2023-08)
# Usage: ./convert-ggml-to-gguf.sh <path/to/model.ggml> [output.gguf]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LLAMA_DIR="${SCRIPT_DIR}/../llama.cpp"
CONVERTER="${LLAMA_DIR}/models/convert-ggml-to-gguf.py"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <input.ggml> [output.gguf]" >&2
    exit 1
fi

INPUT="$1"
OUTPUT="${2:-${1%.ggml}.gguf}"

if [[ ! -f "$INPUT" ]]; then
    echo "Error: input file not found: $INPUT" >&2
    exit 1
fi

if [[ ! -x "$CONVERTER" ]]; then
    echo "Error: converter not found at $CONVERTER — build llama.cpp first?" >&2
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not found" >&2
    exit 1
fi

echo "Converting: $INPUT -> $OUTPUT"
python3 "$CONVERTER" --outfile "$OUTPUT" "$INPUT"

if [[ ! -f "$OUTPUT" ]]; then
    echo "Error: conversion failed — output file not created: $OUTPUT" >&2
    exit 1
fi

echo "Done. Output: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
