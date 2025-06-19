"""
Sets Open WebUI's settings that depend on where it runs (README, "Two-PC setup"), through its own API, for the mode
given: the chat model's address and API key, the small model for titles and tags, speech-to-text, how documents are
read (Docling) and ranked (a reranker), and image generation (ComfyUI). Everything else stays as it is.

  single  (Open WebUI on the main PC): llama-server at 127.0.0.1 without a key, no task model (titles, tags and
          follow-ups off, so they don't disturb llama-server's cache), Whisper "small" on the CPU, Open WebUI's own
          PDF reader, no reranker, no image generation. The setup's values from before the two-PC setup.
  split   (Open WebUI on the helper PC): llama-server on the main PC with its API key, the helper's small model for
          titles, tags, follow-ups and image prompts, the helper's whisper service for speech-to-text
          (large-v3-turbo on the GPU), Docling, ComfyUI (a two-pass workflow, see hires_workflow), and the reranker
          from windows/versions.json (none: it doesn't fit on the helper's GPU).

Usage: python layout.py <single|split> [--base-url URL]
"""

import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync import token  # noqa: E402  (the same admin login as sync.py)

FAILED = 0


def call(base, args, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{base}{path}", data=data, method=method,
                  headers={"Authorization": f"Bearer {token(*args)}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=600) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:
            detail = e.reason
        return e.code, {"detail": detail}


def update(base, args, label, get_path, post_path, change, partial=False):
    """Reads a settings group, applies change(current) -> wanted values, and writes it back when something differs."""
    global FAILED
    status, current = call(base, args, "GET", get_path)
    if status != 200:
        print(f"  FAILED   {label}: can't read ({status})")
        FAILED += 1
        return
    wanted = change(json.loads(json.dumps(current)))
    diff = {k: v for k, v in wanted.items() if current.get(k) != v}
    if not diff:
        print(f"  same     {label}")
        return
    body = diff if partial else {**current, **wanted}
    status, result = call(base, args, "POST", post_path, body)
    if status == 200:
        print(f"  updated  {label}: {', '.join(sorted(diff))}")
    else:
        print(f"  FAILED   {label}: {status} {(result or {}).get('detail', '')}")
        FAILED += 1


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
            "cloned face, mutated hands, watermark, text, signature, jpeg artifacts, worst quality, low quality")
MARK = "localllm hires fix"   # the title of the workflow's upscale node: how a later run knows the workflow is ours

# The task model writes the picture's prompt from the chat. Open WebUI's own instruction asks for a long description in
# the chat's language; Stable Diffusion reads English and only about the first 75 words well, so: short English tags,
# the subject first.
IMAGE_PROMPT_TEMPLATE = """### Task:
Write the prompt for a Stable Diffusion 1.5 image generator, for the picture the user asks for in the chat below.

### Guidelines:
- Always write in English, whatever language the chat is in.
- Write comma-separated tags and short phrases, not sentences: at most 50 words in all.
- Start with the main subject and what it is doing, then the setting, then the style (photo, illustration,
  watercolor, 3d render...), the lighting and the framing (portrait, full body, wide shot).
- End with a few quality tags, such as: highly detailed, sharp focus, masterpiece.
- Keep only what the user asked for; don't add people, objects or text they didn't mention.
- Name well-known characters plainly and add a short description of how they look.

### Output:
Strictly return in JSON format:
{
    "prompt": "the tags"
}

### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>"""


def is_replaceable(text):
    """Open WebUI's default ComfyUI workflow (KSampler node 3, negative prompt node 7), or one this script wrote."""
    if not text:
        return True
    try:
        wf = json.loads(text)
        if wf.get("10", {}).get("_meta", {}).get("title") == MARK:
            return True
        return wf["3"]["class_type"] == "KSampler" and wf["7"]["class_type"] == "CLIPTextEncode" and len(wf) == 7
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


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

    # --- Read settings.json (shared with sync.py) ---
    settings_path = Path(__file__).resolve().parent / "settings.json"
    try:
        s = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Error: {settings_path} not found", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: {settings_path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    mode = argv[1]
    base_url = s.get("llama_url", "http://127.0.0.1:8081").rstrip("/") + "/v1"

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
