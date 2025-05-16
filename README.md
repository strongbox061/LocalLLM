# LocalLLM

A local open-source LLM served by [llama.cpp](https://github.com/ggml-org/llama.cpp), so no external AI APIs are needed.
`llama-server` runs as the systemd service `localllm-llm` and is only reachable from this machine, through its
OpenAI-compatible API at `http://127.0.0.1:8081/v1`. Whatever model is loaded, it is served as `local`: Pi, Open WebUI
and its model settings always ask for that name, so changing the model touches none of them.

The server has 2 slots (64K context each with the default model), so its two clients keep separate prompt caches:

| Slot | Client     |
|------|------------|
| 0    | Pi         |
| 1    | Open WebUI |

## Model

The model is described by a **profile** in `models/`: where to download it (Hugging Face repository and file), its
vision projector (or none), the context per slot, the number of slots, the longest answer, the prompt cache, the
default CPU/GPU split for models with experts, sampling, and how its reasoning is switched off (`REASONING`).
`lib/model.sh` reads it for `install/llama.sh` (the download), `services/llm-server.sh` (llama-server's flags),
`install/pi.sh` (Pi's context window and answer length) and `services/open-webui.sh` (when chats are compacted), and
writes `~/.config/localllm/model.json` for Pi's `thinking-switch` extension.

It handles general chat, writing, research with web search, and coding help in several languages. The model files
are kept in `~/LocalLLM/models/`; `install/llama.sh` downloads them from Hugging Face (checking their size against
the free space first), and no copy is kept anywhere else.

To use another model (or another GGUF build, if the uploader renames or removes the file):

1. Add a profile in `models/`: copy the default one and change its values. `REASONING` is `qwen-template` (the chat
   template's `enable_thinking`), `reasoning-effort` (the lowest `REASONING_OFF_EFFORT`, default `low`) or `none`.
   Leave `MMPROJ_*` empty without image input, and `MOE_LAYERS`/`N_CPU_MOE` empty without experts.
2. Set `MODEL_PROFILE="<file name without .env>"` in `~/.config/localllm/local.env`.
3. Run `install/llama.sh` (downloads it), `install/pi.sh` (Pi's context window), `stop/llama.sh && start/llama.sh`,
   and `stop/open-webui.sh && start/open-webui.sh` (Open WebUI's compaction limit).

What depends on the PC rather than the model (GPU, threads, the CPU/GPU split) stays in `local.env` and wins over
the profile (see "Tuning llama-server").

Before the alias `local`, the model was served under its own name and the service had the model's name too.
`install/llama.sh` stops and removes that old service (`llama-qwen`), `install/pi.sh` rewrites Pi's `llamacpp`
provider, and `install/open-webui-config.sh` moves Open WebUI's settings for the model (system prompt, tools, slot),
presets built on it and the admin's chats to `local` (the names in the profile's `OLD_ALIASES`).

## Reinstalling from scratch

Everything this setup runs comes back from this repo: its scripts, the encrypted settings backup, and the steps
below (with a helper PC, see also "Rebuilding both PCs"). The backup archive holds the repo too (`backup/repo.bundle`,
every branch and commit), so the archive and its passphrase are all you need. Steps marked **by hand** can't be scripted (a login, a
password, a hardware driver, a browser setting the browser only takes from you); everything else is a script. Nothing outside this list is needed.

**Before the scripts**

You need about 28 GB of free disk space plus the model's size (Open WebUI 7.5 GB, the containers 5.4 GB, CUDA 5.3 GB,
the rest and room for the build about 10 GB), and an internet connection. `install.sh` warns when less
than 30 GB is free, and `install/llama.sh` checks the model's own size before downloading it. The downloads are
public: a Hugging Face login is optional (`hf auth login`, only for faster model downloads).

1. **By hand:** install Arch with a desktop whose session starts through systemd (KDE Plasma, GNOME and most others:
   the scripts hand settings to it through `~/.config/environment.d`), with your user allowed to use `sudo` (the
   installer's "add user to sudoers" choice, or the `wheel` group in `/etc/sudoers`).
2. **By hand:** install the NVIDIA driver and reboot (`nvidia-smi` must work). This PC uses `sudo pacman -S nvidia-open`
   (RTX 20 series and newer, with Arch's stock `linux` kernel; another kernel needs `nvidia-open-dkms`).
3. **By hand:** Docker, which these scripts leave to you:
   ```bash
   sudo pacman -S docker docker-compose
   sudo systemctl enable --now docker.service
   sudo usermod -aG docker "$USER"   # recommended (log in again after): without it the scripts use sudo for Docker,
                                     # and the scheduled backup, which can't ask for a password, can't read the
                                     # containers' versions (it keeps the ones recorded before)
   ```
4. **By hand:** `sudo pacman -S git`.
5. **By hand:** get the latest backup archive, open it with the passphrase from your password manager, and clone the
   repo from the bundle inside it, into the same folder as before if your `EXTRA_BACKUP_FILES` include files inside it
   (they come back to the same paths): the commands are in "Opening a backup". A bare Arch has no rclone yet and maybe
   no browser yet: `sudo pacman -S rclone`, then `rclone config` (**by hand**: your
   storage's login, the remote named in `BACKUP_REMOTE`) and `rclone copy`, or download it with any browser.
   This setup is only ever installed from a backup: on a new machine `install.sh` stops when `backup/` holds no
   settings.

## Starting and stopping

### Memory

The server is configured to use 6GB of RAM by default. Adjust `runArgs` in the config if you need more or less:

```json
"runArgs": ["--memory=6g"]
```

For JVM-based tools running alongside, set `JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=60` and for Node.js builds use `NODE_OPTIONS=--max-old-space-size=3072`.

### Dev containers next to the model

Dev containers can run alongside the model server. The docker-compose.yml file shares the GPU device and mounts the models directory:

```yaml
services:
  dev:
    volumes:
      - ./models:/workspace/models:ro
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
```

### File changes in Pi

Pi watches the repo directory for file changes. When you edit a file, Pi picks up the new content automatically. No manual reload needed.

### Todo list, subagents and long sessions in Pi

For long-running tasks, Pi uses subagents to parallelize work. The todo list tracks stages across subagent sessions so progress isn't lost if a session times out.

### Sandbox in Pi

Pi runs code execution in an isolated sandbox by default. Network access is disabled unless explicitly granted.

### Library docs in Pi

When you ask about a library API, Pi fetches the official docs and keeps them cached for the session. This works offline once loaded.

### Search usage dashboard

Pi tracks search queries locally. The dashboard at `http://localhost:8080/search` shows query counts per source over time. To add a new preset:

1. "Create" writes the file and builds the preset
2. "Download sources" fills its Knowledge base
3. Queries route to the appropriate source based on keywords

### Languages

The server supports multiple languages for chat profiles. Each profile defines a system prompt template in its own JSON file under `profiles/`.

## SearXNG after a network change

If your network changes, SearXNG needs to be restarted with the new DNS settings:

```bash
docker compose restart searxng
```

The container picks up the host's resolv.conf on restart.

## Two-PC setup

A second (helper) PC can join for gaming or extra compute. Both PCs share models via rsync over Tailscale.

### Connecting the helper PC

Copy your SSH public key to the helper and add its entry to `~/.ssh/config`, then test the connection:

```bash
ssh-copy-id helper@<helper-ip>
ssh helper "echo connected"
```

### Installing the helper PC

The helper runs a minimal setup: Tailscale, rsync, and a lightweight llama.cpp binary. No GPU required — it handles tasks like transcription with `whisper.py` and search.

### Switching between single and split mode

Toggle between running everything on the main PC or distributing work across both. In split mode, rsync pushes models to the helper before inference.

### Pages in split mode

In split mode, the helper runs a dashboard at port 8081 and search settings at port 8082. Chat types are distributed based on latency.

### Gaming mode on the helper

When gaming on the helper, the main PC handles inference and the helper streams the display back via Tailscale. Switch to performance fan mode first:

```bash
bash fan-performance.sh
```

Or balanced mode for idle:

```bash
bash fan-balanced.sh
```

### Backups in split mode

Each PC runs its own rsync backup to an external drive. The backup script handles model files and configs.

### Rebuilding both PCs

1. Restore models from the latest backup
2. Re-run zram setup on both machines
3. Re-establish Tailscale connection

### Rebuilding only the helper PC

Restore from the helper-specific backup, which contains SSH configs and search settings but not model files (those come from the main PC).

### Keeping the helper Tailscale tunnel warm

A cron job pings the main PC every 5 minutes to keep the Tailscale tunnel active:

```bash
*/5 * * * * ping -c 1 main-pc.local > /dev/null 2>&1
```

## Manual steps

Some operations require manual intervention:

- Flashing model weights to a new device
- Updating the GPU driver after a kernel update
- Reconfiguring fan curves for thermal throttling

## Backing up and restoring settings

### Versions

Backups are versioned by date in `backups/2023-04-12/`. Each backup includes configs, profiles, and SSH keys.

### Opening a backup

To restore a backup, extract it to the repo root:

```bash
tar xzf backups/2023-04-12.tar.gz
```

This restores from wherever else you keep it. Model files are not included in standard backups — copy them separately.

### Automatic off-site backup

An encrypted rsync job pushes daily backups to a remote server, configured in cron as a nightly job at 2 AM.
