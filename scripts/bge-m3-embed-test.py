#!/usr/bin/env python3
"""Prototype: test BAAI/bge-m3 embeddings locally via transformers."""

from __future__ import annotations

import torch

MODEL_ID = "BAAI/bge-m3"


def load_model(model_id: str = MODEL_ID) -> tuple:
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id)
    return tokenizer, model


def encode(tokenizer, model, texts: list[str]) -> list[list[float]]:
    inputs = tokenizer(texts, padding=True, truncation=True, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
    embeddings = outputs.last_hidden_state.mean(dim=1).tolist()
    return embeddings


if __name__ == "__main__":

    texts = [
        "LocalLLM manages local large language models.",
        "Clean chat history reduces context window noise.",
    ]
    print(f"Loading {MODEL_ID} ...")
    tokenizer, model = load_model()

    embeddings = encode(tokenizer, model, texts)
    for text, emb in zip(texts, embeddings):
        norm = (sum(v * v for v in emb) ** 0.5)
        print(f"  [{norm:.4f}] {text}")
    print("Done.")
