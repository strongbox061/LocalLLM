# ComfyUI Workflow Notes

## Setup

ComfyUI runs locally via Python. Clone the repo and install dependencies:

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
pip install -r requirements.txt
python main.py --listen
```

Place model checkpoints in the `models/checkpoints/` directory.

## DreamShaper Workflow

DreamShaper (v6/v7) works well for stylized art and character generation.

### Basic SD 1.5 Pipeline

1. **Load Checkpoint** — select `dreamshaper_6.safetensors`
2. **KSampler** settings:
   - Steps: 20–30
   - CFG: 7–9
   - Sampler: `euler_ancestral` or `dpmpp_2m`
   - Scheduler: `normal` or `karras`
3. **CLIP Text Encode** — positive prompt in one node, negative in another
4. **VAE Decode** — connects KSampler output to VAE
5. **Save Image** — outputs to the `output/` directory

### Tips

- Use a seed for reproducibility; set it explicitly in KSampler
- For higher res, add a Latent Upscale node between KSampler and VAE Decode (2x with nearest-exact)
- DreamShaper responds well to CFG 8–9; lower CFG can make images too soft
- Negative prompt should include common artifacts: `lowres, bad anatomy, extra limbs`

### Workflow Export

Export the workflow JSON from the UI (`Save (API Format)`) and store it for versioning.

## Notes

- ComfyUI is node-based; workflows are saved as JSON
- DreamShaper is tuned for Stable Diffusion 1.5, not SDXL (came later)
- GGML models are for text generation; ComfyUI uses diffusion checkpoints (.safetensors)
