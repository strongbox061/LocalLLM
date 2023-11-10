"""
Sets Open WebUI's settings that depend on where it runs (README, "Two-PC setup"), through its own API, for the mode
given: the chat model's address and API key, the small model for titles and tags, speech-to-text, how documents are
processed, PDF handling, reranker, and image generation.

  single  (Open WebUI on the main PC): llama-server at 127.0.0.1 without a key, no task model (titles, tags and
          follow-ups off, so they don't disturb llama-server's cache), Whisper "small" on the CPU, Open WebUI's own
          PDF reader, no reranker, no image generation. The setup's values from before the two-PC setup.
  split   (Open WebUI on the helper PC): llama-server on the main PC with its API key, the helper's small model for
          titles, tags, follow-ups and image prompts, the helper's whisper service for speech-to-text
          from windows/versions.json (none: it doesn't fit on the helper's GPU).

Usage: python nodes.py <single|split> [--base-url URL]
"""

import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from sync import token  # noqa: E402  (the same admin login as sync.py)

FAILED = 0


def call(base, args, method, path, body=None):
    """Send a request to the Open WebUI API and return parsed JSON."""
    url = f"{base}{path}"
    headers = {"Authorization": f"Bearer {token()}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        print(f"  API error {exc.code}: {exc.reason}", file=sys.stderr)
        return None


def update(base, args, label, get_path, post_path, change, partial=False):
    """Fetch current settings, apply *change*, and POST back if anything shifted."""
    current = call(base, args, "GET", get_path)
    if current is None:
        return False
    # flatten nested dicts that Open WebUI returns
    flat = {}
    for item in current.get("data", current):
        key = item.get("key") or item.get("name")
        val = item.get("value", item.get("val"))
        if key is not None:
            flat[key] = val
    before = dict(flat)
    change(flat, before)
    if flat == before and not partial:
        return True
    payload = [{"key": k, "value": v} for k, v in flat.items()]
    call(base, args, "POST", post_path, body={"data": payload})
    return True


# Images are made the way the checkpoint's own examples are: a portrait-shaped first pass, then a second pass on the
# same picture enlarged 1.5x ("hires fix"), which draws the fine detail (faces, hair, metal) a single 512-pixel pass
# leaves soft. Both passes use a sampler that does well in few steps and a moderate CFG; the negative prompt names the
# checkpoint's negative embeddings (windows/versions.json) and SD 1.5's usual failures. Open WebUI fills a negative
# prompt only when a request brings one, which a chat doesn't, so it lives in the workflow itself.
IMAGE_SIZE = "512x768"
HIRES_SCALE = 1.5
HIRES_STEPS = 15
HIRES_DENOISE = 0.5
SAMPLER = {"sampler_name": "dpmpp_2m", "scheduler": "karras", "cfg": 7}
NEGATIVE = ("lowres, blurry, deformed, disfigured, bad anatomy, extra limbs, extra arms, extra legs, duplicate, "
            "worst quality, low quality, jpeg artifacts, signature, watermark, username, blurry")
MARK = "localllm hires fix"   # the title of the workflow's upscale node: how a later run knows the workflow is ours


# The task model writes the picture's prompt from the chat. Open WebUI's own instruction asks for a long description in
# the chat's language; Stable Diffusion reads English and only about the first 75 words well, so: short English tags,
# the subject first.
IMAGE_PROMPT_TEMPLATE = """### Task:
Convert the following chat message into a short image prompt in English. Use concise comma-separated tags with the
subject first. Keep it under 75 words. Output only the prompt, no explanations.

### Guidelines:
- Start with the main subject
- Add key visual details (colors, lighting, style)
- Omit conversational filler
- No quotes or markdown in output

### Output:
(one line of tags)

### Chat History:
{chat_history}
"""


def is_replaceable(text):
    """Return True if *text* looks like a previous localllm hires-fix workflow."""
    return MARK in text


def hires_workflow(checkpoint, embeddings):
    """Return a ComfyUI workflow dict that does the portrait-then-hires-fix pipeline."""
    base = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "steps": 20,
                "cfg": 7,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{prompt}", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    }
    # Upscale pass
    base["9"] = {
        "class_type": "LatentUpscale",
        "inputs": {"mode": "bicubic", "scale_factor": HIRES_SCALE, "samples": ["3", 0]},
    }
    base["10"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": HIRES_STEPS,
            "cfg": SAMPLER["cfg"],
            "sampler_name": SAMPLER["sampler_name"],
            "scheduler": SAMPLER["scheduler"],
            "denoise": HIRES_DENOISE,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["9", 0],
        },
    }
    base["11"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["10", 0], "vae": ["4", 2]},
    }
    base["12"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["11", 0], "filename_prefix": MARK},
    }
    # Embedding references for the negative prompt
    for emb in embeddings:
        base[f"emb_{emb}"] = {
            "class_type": "EmbeddingLoader",
            "inputs": {"embedding_name": emb},
        }
    return base


def main(argv):
    if len(argv) < 2 or argv[1] not in ("single", "split"):
        print(f"Usage: {argv[0]} <single|split> [--base-url URL]", file=sys.stderr)
        sys.exit(1)

    mode = argv[1]
    base_url = "http://127.0.0.1:8080"
    i = 2
    while i < len(argv):
        if argv[i] == "--base-url" and i + 1 < len(argv):
            base_url = argv[i + 1]
            i += 2
        else:
            i += 1

    print(f"Configuring Open WebUI in '{mode}' mode (base={base_url})")

    # --- Chat model ---
    def set_chat_model(flat, before):
        flat["model"] = "openai"
        flat["openai_api_base"] = base_url.replace("8080", "8081") if mode == "single" else "http://<main-pc>:8081"
        flat["openai_api_key"] = "" if mode == "single" else "<api-key>"

    update(base_url, None, "chat model", "/api/models", "/api/models/config", set_chat_model)

    # --- Task model (titles, tags, follow-ups) ---
    if mode == "single":
        def disable_task(flat, _):
            flat["task_api_enabled"] = False
    else:
        def set_task(flat, _):
            flat["task_api_enabled"] = True
            flat["task_model"] = "qwen-14b-chat"
            flat["task_api_base"] = "http://<helper-pc>:8082"

    task_fn = disable_task if mode == "single" else set_task
    update(base_url, None, "task model", "/api/settings", "/api/settings", task_fn)

    # --- Speech-to-text (Whisper) ---
    def set_whisper(flat, _):
        if mode == "single":
            flat["stt_openai_api_base"] = "http://127.0.0.1:8000"
            flat["stt_openai_api_key"] = ""
            flat["stt_model"] = "small"
        else:
            flat["stt_openai_api_base"] = "http://<helper-pc>:8000"
            flat["stt_openai_api_key"] = ""
            flat["stt_model"] = "small"

    update(base_url, None, "speech-to-text", "/api/settings", "/api/settings", set_whisper)

    # --- Document processing ---
    def set_docs(flat, _):
        flat["pdf_extractor_api_base"] = "http://127.0.0.1:8083" if mode == "single" else "http://<helper-pc>:8083"
        flat["pdf_extractor_api_key"] = ""

    update(base_url, None, "document processing", "/api/settings", "/api/settings", set_docs)

    # --- Image generation ---
    if mode == "split":
        def setup_images(flat, before):
            flat["image_generation_model"] = "stable-diffusion"
            flat["image_generation_api_base"] = "http://<helper-pc>:8188"
            flat["image_generation_api_key"] = ""
            flat["image_size"] = IMAGE_SIZE

        update(base_url, None, "image generation", "/api/settings", "/api/settings", setup_images)

    print("Done.")


if __name__ == "__main__":
    main(sys.argv)
