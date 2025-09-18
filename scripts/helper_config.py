#!/usr/bin/env python3
"""The helper PC's config.json, made on the main PC (the helper never reads ~/.config/localllm itself).

    python3 lib/helper_config.py <endpoints.json> <model.json>    prints the JSON

From the environment (lib/common.sh fills it from local.env, helper.env and versions.env):
    HELPER_DATA_DIR        the helper's data folder (Python environments, models, Open WebUI's data); required
    HELPER_SMALL_MODEL     on/off: the small model for Open WebUI's titles and tags (default on)
    HELPER_IMAGES          on/off: ComfyUI for images (default on)
    HELPER_DOCLING         on/off: Docling for PDFs and scans (default on)
    HELPER_KOKORO_DEVICE   gpu/cpu (default gpu)
    OPEN_WEBUI_VERSION     the main PC's Open WebUI version (the helper runs the same one, so the data can move)
"""

import getpass
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Start order on the helper; stop goes the other way round (Open WebUI and the search gateway first).
HELPER_ORDER = ["kokoro", "voice-router", "whisper", "docling", "small-model", "comfyui", "search-gateway", "open-webui"]

FEATURES = {"small-model": "HELPER_SMALL_MODEL", "comfyui": "HELPER_IMAGES", "docling": "HELPER_DOCLING"}


def flag(env, name, default="on"):
    """Return the value of an on/off feature flag from the environment dict."""
    return env.get(name, default) == "on"


def build(env, endpoints, model, versions):
    """Build and return the helper config dict."""
    data_dir = env.get("HELPER_DATA_DIR")
    if not data_dir:
        print("error: HELPER_DATA_DIR is not set", file=sys.stderr)
        sys.exit(1)

    config = {
        "helper_data_dir": data_dir,
        "open_webui_version": env.get("OPEN_WEBUI_VERSION", ""),
        "kokoro_device": env.get("HELPER_KOKORO_DEVICE", "gpu"),
        "features": {
            "small_model": flag(env, "HELPER_SMALL_MODEL"),
            "images": flag(env, "HELPER_IMAGES"),
            "docling": flag(env, "HELPER_DOCLING"),
        },
        "endpoints": endpoints,
        "model": model,
    }

    return config


def main(argv):
    if len(argv) < 3:
        print(f"usage: {sys.argv[0]} <endpoints.json> <model.json>", file=sys.stderr)
        sys.exit(1)

    endpoints_path = argv[1]
    model_path = argv[2]

    with open(endpoints_path) as f:
        endpoints = json.load(f)
    with open(model_path) as f:
        model = json.load(f)

    env = {
        "HELPER_DATA_DIR": os.environ.get("HELPER_DATA_DIR"),
        "HELPER_SMALL_MODEL": os.environ.get("HELPER_SMALL_MODEL", "on"),
        "HELPER_IMAGES": os.environ.get("HELPER_IMAGES", "on"),
        "HELPER_DOCLING": os.environ.get("HELPER_DOCLING", "on"),
        "HELPER_KOKORO_DEVICE": os.environ.get("HELPER_KOKORO_DEVICE", "gpu"),
        "OPEN_WEBUI_VERSION": os.environ.get("OPEN_WEBUI_VERSION", ""),
    }

    config = build(env, endpoints, model, {})
    print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main(sys.argv)
