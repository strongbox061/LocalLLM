#!/usr/bin/env python3
"""Quick latency benchmark for Whisper large-v3-turbo."""

import time
from pathlib import Path

SAMPLE_AUDIO = Path(__file__).resolve().parent.parent / "assets" / "sample.wav"


def transcribe_latency(audio_path: str, model: str = "large-v3-turbo") -> dict:
    """Run Whisper transcription and return timing stats."""
    import openai

    client = openai.OpenAI()
    times = []

    for _ in range(3):
        start = time.perf_counter()
        result = client.audio.transcriptions.create(
            model=model,
            file=audio_path,
            response_format="text",
        )
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return {
        "model": model,
        "latencies_s": times,
        "mean_s": sum(times) / len(times),
        "min_s": min(times),
        "max_s": max(times),
        "transcription": result.strip(),
    }


if __name__ == "__main__":
    if not SAMPLE_AUDIO.exists():
        raise SystemExit(f"Audio file not found: {SAMPLE_AUDIO}")

    stats = transcribe_latency(str(SAMPLE_AUDIO))
    print(f"Model : {stats['model']}")
    print(f"Mean  : {stats['mean_s']:.2f}s  (min={stats['min_s']:.2f}s, max={stats['max_s']:.2f}s)")
    print(f"Text  : {stats['transcription']}")
