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
Exit status 1 with the reason on stderr when something is missing or wrong.
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
    v = env.get(name, "").strip().lower() or default
    if v not in ("on", "off"):
        raise ValueError(f"{name} is '{v}' in helper.env: it must be on or off.")
    return v == "on"


def build(env, endpoints, model, versions):
    """Build and return the helper config dict."""
    if endpoints.get("mode") != "split":
        raise ValueError("The helper PC is only used in split mode (./localllm.sh switch split).")
    data_dir = env.get("HELPER_DATA_DIR", "").strip()
    if not data_dir or not (len(data_dir) > 2 and data_dir[1] == ":" and data_dir[2] == "\\"):
        raise ValueError(f"HELPER_DATA_DIR ('{data_dir}') must be a full Windows path like X:\\LocalLLM (helper.env).")
    device = env.get("HELPER_KOKORO_DEVICE", "").strip().lower() or "gpu"
    if device not in ("gpu", "cpu"):
        raise ValueError(f"HELPER_KOKORO_DEVICE is '{device}': it must be gpu or cpu.")
    webui_version = env.get("OPEN_WEBUI_VERSION", "").strip()
    if not webui_version:
        raise ValueError("The main PC's Open WebUI version is unknown: run ./backup.sh once (it writes versions.env).")

    svc = endpoints["services"]
    apps = env.get("LOCALLLM_APPS", "").strip() or endpoints.get("apps") or "pi,owui"
    services = []
    for name in HELPER_ORDER:
        s = svc.get(name)
        if not s or s.get("where") != "helper":
            continue
        if name in FEATURES and not flag(env, FEATURES[name]):
            continue
        services.append({"name": name, "title": s["title"], "port": s["port"], "health": s["health"],
                         "id": f"localllm-{name}", "owui_only": bool(s.get("owui_only"))})
    names = {s["name"] for s in services}
    llama = svc["llama"]["from_helper"]
    ctx, max_tokens = int(model.get("contextPerSlot") or 0), int(model.get("maxTokens") or 0)
    return {
        "mode": "split",
        "apps": apps,
        "data_dir": data_dir,
        "main_host": endpoints["main_host"],
        "main_user": env.get("MAIN_USER", "").strip() or getpass.getuser(),
        "helper_host": endpoints["helper_host"],
        "llama_url": llama,
        "searxng_search": svc["searxng"]["from_helper"] + "/search" if svc["searxng"]["where"] == "main" else "",
        "model_alias": model.get("alias") or "local",
        "model_name": model.get("name") or "Local Model",
        "context_window": (ctx - max_tokens) if ctx > max_tokens > 0 else 57344,
        "max_tokens": max_tokens or 8192,
        "model_vision": bool(model.get("vision", True)),
        "model_reasoning": str(model.get("reasoning") or "none").strip().lower() != "none",
        # Open WebUI compacts a chat at 7/8 of what's left in llama-server's slot (as services/open-webui.sh).
        "compaction_threshold": (ctx - max_tokens) * 7 // 8 if ctx > max_tokens > 0 else 0,
        "open_webui_version": webui_version,
        "kokoro_device": device,
        "services": services,
        "exposed": [n for n in ("open-webui", "search-gateway") if n in names],
        "versions": versions,
    }


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        endpoints = json.load(open(argv[1]))
        try:
            model = json.load(open(argv[2]))
        except OSError:
            model = {}
        versions = {k: v for k, v in json.load(open(os.path.join(REPO, "windows", "versions.json"))).items()
                    if not k.startswith("_")}
        # The small model lives with the other model definitions (models/), like every model name.
        versions["small_model"] = {k: v for k, v in json.load(open(os.path.join(REPO, "models", "helper-small.json"))).items()
                                   if not k.startswith("_")}
        print(json.dumps(build(os.environ, endpoints, model, versions), indent=2))
    except (ValueError, OSError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
