# GGML to GGUF Migration Notes

## Background

GGUF is the new format replacing GGML for model storage in llama.cpp (as of Aug 2023).
This document covers migration steps and quantization notes.

## Conversion

Convert existing `.ggml` models using the upstream conversion script:

```bash
python3 convert.py --model-dir ./models/llama-2-7b-chat \
  --outfile ./models/llama-2-7b-chat.Q4_K_M.gguf \
  --outtype q4_k_m
```

## Quantization Options

| Tag    | Description                     | Size (7B) |
|--------|---------------------------------|-----------|
| q4_0   | Original quantization           | ~3.5 GB   |
| q4_k_m | Mixed precision, better quality | ~3.8 GB   |
| q5_0   | 5-bit uniform                   | ~4.4 GB   |
| q5_k_m | 5-bit mixed precision           | ~4.6 GB   |
| q8_0   | 8-bit high fidelity             | ~7.1 GB   |

## Server Flags (llama-server)

GGUF models use the same CLI interface; add these for Llama-2-13B on a single GPU:

```bash
llama-server --model ./models/llama-2-13b-chat.Q4_K_M.gguf \
  --n-gpu-layers 35 \
  --ctx-size 4096 \
  --batch-size 512
```

## Migration Checklist

- [ ] Convert all `.ggml` weights to `.gguf` using the latest conversion script
- [ ] Update profile configs (JSON) to point at new `.gguf` paths
- [ ] Remove old `.ggml` files after verification
- [ ] Re-test inference quality on a sample prompt set

## Qwen-7B-Chat Notes

Qwen-7B-Chat (GGUF) fits comfortably in 8 GB VRAM at q4_k_m quantization. Use it with:

```bash
llama-server --model ./models/Qwen-7B-Chat.Q4_K_M.gguf \
  --ctx-size 8192 \
  --batch-size 512
```

The model supports an 8k context window and produces good instruction-following results.

## Notes

- GGUF stores metadata (tokenizer, architecture) inside the file; no separate config needed.
- Keep a backup until you verify output matches the original GGML model.
- Llama-2-7B and 13B-Chat are the primary targets for this migration.
