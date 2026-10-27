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

The embedding model is set by sync.py (LOCALLLM_EMBEDDING), which also indexes the Knowledge bases again with it.
Run by ./localllm.sh switch (through windows/setup.ps1's Python on the helper), with Open WebUI's Python.

Usage: python topology.py <single|split> <Open WebUI URL> <webui.db> <.webui_secret_key> <settings.json>
settings.json: {"llama_url", "llama_key", "small_model": {"url", "alias"} or null, "whisper_model", "whisper_url" or "",
                "reranking_model", "docling_url" or "", "comfyui": {"url", "checkpoint", "embeddings"} or null}
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
    """The two-pass workflow, with the node numbers of Open WebUI's default one (so the same field mapping fits)."""
    negative = ", ".join([f"embedding:{e}" for e in embeddings] + [NEGATIVE])
    sampler = lambda latent, steps, denoise: {"class_type": "KSampler", "inputs": {  # noqa: E731
        "seed": 0, "steps": steps, **SAMPLER, "denoise": denoise,
        "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": latent}}
    return {
        "3": sampler(["5", 0], 30, 1.0),
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "10": {"class_type": "LatentUpscaleBy", "_meta": {"title": MARK},
               "inputs": {"upscale_method": "nearest-exact", "scale_by": HIRES_SCALE, "samples": ["3", 0]}},
        "11": sampler(["10", 0], HIRES_STEPS, HIRES_DENOISE),
        # Tiled decoding: the enlarged picture in one piece doesn't fit next to the model on a 6 GB card.
        "8": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["11", 0], "vae": ["4", 2], "tile_size": 512,
                                                         "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]}},
    }


def main(argv):
    if len(argv) != 6 or argv[1] not in ("single", "split"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    mode, base, db, key, settings_file = argv[1], argv[2].rstrip("/"), argv[3], argv[4], argv[5]
    s = json.loads(Path(settings_file).read_text(encoding="utf-8"))
    args = (db, key)
    split = mode == "split"
    small = s.get("small_model") if split else None
    comfy = s.get("comfyui") if split else None

    def openai(c):
        # The chat model is always the first connection; the helper's small model the second (split mode only).
        urls, keys = [s["llama_url"].rstrip("/") + "/v1"], [s.get("llama_key") or "none"]
        configs = {"0": (c.get("OPENAI_API_CONFIGS") or {}).get("0") or {"enable": True}}
        if small:
            urls.append(small["url"].rstrip("/") + "/v1")
            keys.append("none")
            configs["1"] = {"enable": True, "model_ids": [small["alias"]]}
        return {"ENABLE_OPENAI_API": True, "OPENAI_API_BASE_URLS": urls, "OPENAI_API_KEYS": keys,
                "OPENAI_API_CONFIGS": configs}
    update(base, args, "chat model connection", "/openai/config", "/openai/config/update", openai)

    def tasks(c):
        on = bool(small)
        return {"TASK_MODEL": "", "TASK_MODEL_EXTERNAL": small["alias"] if small else "",
                "ENABLE_TITLE_GENERATION": on, "ENABLE_TAGS_GENERATION": on, "ENABLE_FOLLOW_UP_GENERATION": on,
                "IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE": IMAGE_PROMPT_TEMPLATE if comfy else ""}
    update(base, args, "titles, tags and follow-ups", "/api/v1/tasks/config", "/api/v1/tasks/config/update", tasks)

    def audio(c):
        whisper = s.get("whisper_url") if split else ""
        if whisper:
            # The helper's whisper service (services/whisper_server.py), as an OpenAI-compatible engine.
            stt = {**c["stt"], "ENGINE": "openai", "OPENAI_API_BASE_URL": whisper.rstrip("/") + "/v1",
                   "OPENAI_API_KEY": "not-needed", "MODEL": s["whisper_model"], "WHISPER_MODEL": s["whisper_model"]}
        else:
            stt = {**c["stt"], "ENGINE": "", "WHISPER_MODEL": s["whisper_model"] if split else "small"}
        tts = {**c["tts"], "ENGINE": "openai", "OPENAI_API_BASE_URL": "http://127.0.0.1:8881/v1"}
        return {"stt": stt, "tts": tts}
    # The audio settings go back whole: Open WebUI wants both groups (stt and tts) in every update.
    update(base, args, "voice (Whisper, Kokoro)", "/api/v1/audio/config", "/api/v1/audio/config/update", audio)

    def retrieval(c):
        docling = s.get("docling_url") if split else ""
        return {"CONTENT_EXTRACTION_ENGINE": "docling" if docling else "",
                "DOCLING_SERVER_URL": docling or c.get("DOCLING_SERVER_URL") or "",
                "RAG_RERANKING_ENGINE": "", "RAG_RERANKING_MODEL": s["reranking_model"] if split else ""}
    update(base, args, "documents (Docling, reranker)", "/api/v1/retrieval/config", "/api/v1/retrieval/config/update",
           retrieval, partial=True)

    def images(c):
        if not comfy:
            return {"ENABLE_IMAGE_GENERATION": False}
        # Which fields of Open WebUI's default ComfyUI workflow get the prompt, model, size and steps (its admin page
        # fills these in by hand; without them ComfyUI is asked for a "model.safetensors" that doesn't exist). No
        # negative_prompt entry: a chat request has none, and Open WebUI would write "None" over NEGATIVE.
        nodes = [{"type": "prompt", "key": "text", "node_ids": ["6"]},
                 {"type": "model", "key": "ckpt_name", "node_ids": ["4"]},
                 {"type": "width", "key": "width", "node_ids": ["5"]},
                 {"type": "height", "key": "height", "node_ids": ["5"]},
                 {"type": "n", "key": "batch_size", "node_ids": ["5"]},
                 {"type": "steps", "key": "steps", "node_ids": ["3"]},
                 {"type": "seed", "key": "seed", "node_ids": ["3", "11"]}]
        current = c.get("COMFYUI_WORKFLOW")
        workflow = (json.dumps(hires_workflow(comfy["checkpoint"], comfy.get("embeddings") or []), indent=2)
                    if is_replaceable(current) else current)
        return {"ENABLE_IMAGE_GENERATION": True, "IMAGE_GENERATION_ENGINE": "comfyui",
                "COMFYUI_BASE_URL": comfy["url"], "IMAGE_GENERATION_MODEL": comfy["checkpoint"],
                "IMAGE_SIZE": IMAGE_SIZE, "IMAGE_STEPS": 30, "ENABLE_IMAGE_PROMPT_GENERATION": True,
                "COMFYUI_WORKFLOW_NODES": nodes, "COMFYUI_WORKFLOW": workflow}
    update(base, args, "images (ComfyUI)", "/api/v1/images/config", "/api/v1/images/config/update", images)

    print(f"Open WebUI set for {mode} mode." if not FAILED else f"{FAILED} setting group(s) failed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
