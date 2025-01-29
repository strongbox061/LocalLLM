#!/usr/bin/env python3
"""Temporary HTTP test client for Kokoro-FastAPI TTS service."""

import argparse
import requests


def synthesize(text: str, voice: str = "af", speed: float = 1.0) -> bytes:
    """Send a synthesis request and return the audio bytes."""
    url = "http://localhost:8880/speak"
    payload = {
        "text": text,
        "voice": voice,
        "speed": speed,
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to Kokoro at {url}")
        raise SystemExit(1)
    return resp.content


def main():
    parser = argparse.ArgumentParser(description="Test client for Kokoro TTS")
    parser.add_argument("text", help="Text to synthesize")
    parser.add_argument("--voice", default="af", help="Voice ID (default: af)")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed multiplier")
    parser.add_argument("-o", "--output", default="output.wav", help="Output file path")
    args = parser.parse_args()

    audio = synthesize(args.text, args.voice, args.speed)
    with open(args.output, "wb") as f:
        f.write(audio)
    print(f"Saved {len(audio)} bytes to {args.output}")


if __name__ == "__main__":
    main()
