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

The default profile (`models/default`) is `models/qwen3.6-35b-a3b.env`: Qwen3.6 35B-A3B, 4-bit, with image input.
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

**The scripts**

6. `./install.sh`: installs everything and restores `backup/` (the table below), at the versions the backup recorded
   (see "Versions"). It asks for your sudo password. pacman also asks "Proceed with installation? [Y/n]" for each
   group of packages, and sometimes which of several packages should provide something ("Enter a number
   (default=1)"): press Enter each time, the defaults are the right answer.
7. **By hand:** log out and back in once, so the desktop picks up any new launchers/environment (such as Nix, if your
   `BROWSER_INSTALLER` uses it) and
   terminals have `~/.local/bin` on their PATH (`pi`, `plannotator`, `agent-browser`; set by `install/pi.sh`).
8. `./start.sh`: starts llama-server, Open WebUI, Kokoro and the search gateway.

**After the scripts**

9. Check Context7 (Pi's library docs). Its sign-in came back with the backup, so this only confirms it:
    ```bash
    install/pi-mcp.sh        # prints "Context7: signed in."
    tests/context7-check.sh  # a real docs lookup through Pi (about a minute): ends with "Context7: ready"
    ```
    **By hand, only if it says "not signed in"** (the sign-in expired, or its key was lost): `pi`, then inside it `/mcp-auth context7`,
    approve in the browser, `/quit`, and run the two commands again.
10. Open Open WebUI (a rebuilt main PC is in single mode: http://127.0.0.1:3000; `./localllm.sh status` always shows
    its address) and sign in with your usual account. The accounts, chats, settings, tools and functions, the search
    services' API keys and the protected chats (their presets and Knowledge bases) all came back with the backup.
    **Only if a protected chat's Knowledge base is empty:** `install/chat-types.sh --refresh-sources` downloads its
    sources again (see "Protected chats").
11. **By hand:** the passphrase file for new backups. The restored `~/.config/localllm/local.env` names where it goes
    (`BACKUP_PASSPHRASE_FILE`), along with the archive folder, the rclone remote and how many archives to keep; put
    the passphrase there, alone on one line, and `chmod 600` it.
12. Turn on the scheduled backup: `rclone config` if you haven't yet (**by hand**: your storage's login), then
    `install/backup-timer.sh --enable` (one test upload, then the timer is on; see "Automatic off-site backup").

To check that nothing on this machine is missing from these steps, run `tests/restorability-check.sh`: it lists
what's installed and fails on anything no script, backup or step here brings back, then test-restores the backup into
a temporary folder. Programs of your own that aren't part of this setup go one per line in
`~/.config/localllm/restorability-ignore` (kept out of the repo; the settings backup saves it).

Packages this PC needs beyond the setup's own (your laptop vendor's fan tool for `tools/fans/`, say) go in
`EXTRA_PACKAGES` in `local.env`; `install.sh` installs them right after the restore.

`install.sh` can be run again at any time: finished steps are skipped. `./install.sh --update` also updates
your browser (if `BROWSER_INSTALLER` is set in `local.env`), llama.cpp, Pi, its packages and Open WebUI.

| Step | Script | What it does |
|------|--------|--------------|
| 1 | `restore.sh --missing-only` | Puts the settings in `backup/` back in place, without touching existing ones |
| 2 | `BROWSER_INSTALLER` (optional) + `install/browser-address.sh` | Runs your personal browser installer from `local.env` (if set; keep it in `EXTRA_BACKUP_FILES`), and when `BROWSER_POLICY_DIR` is set writes the Chromium-style autoplay policy for Open WebUI's address (voice mode) |
| 3 | `install/memory.sh` | zram (compressed swap in RAM), swap tuning in `/etc/sysctl.d`, and browser Memory Saver on by default when `BROWSER_POLICY_DIR` is set (see "Memory") |
| 4 | `install/llama.sh` | Builds llama.cpp with CUDA in `~/LocalLLM/llama.cpp`, downloads the profile's model and its vision projector with the Hugging Face CLI, writes `~/.config/localllm/model.json`, installs the `localllm-llm` service |
| 5 | `install/searxng.sh` | SearXNG with Docker Compose in `~/LocalLLM/searxng` |
| 6 | `install/pi.sh` | Pi, `~/.pi/agent/models.json` with the `llamacpp` provider (the model `local`, sized from the profile; written by `install/pi-models.sh`, which `localllm.sh` also runs, since split mode adds llama-server's API key), and `~/.local/bin` on PATH from the next login (`~/.config/environment.d/60-localllm-path.conf`) |
| 7 | `install/pi-packages.sh` | Pi's npm packages (the ones in the restored `settings.json`), agent-browser (at least the version `pi-agent-browser-native` needs; an older one leaves Pi unable to open pages) |
| 8 | `install/plannotator.sh` | The `plannotator` program (binary only), for the Inbox, at the same version as Pi's Plannotator extension, with the Inbox tool on for Pi (see "Plannotator Inbox") |
| 9 | `install/pi-config.sh` | Copies `pi/` (extensions, `AGENTS.md`, skills) into `~/.pi/agent` |
| 10 | `install/pi-mcp.sh` | Pi's documentation sources, GitMCP and Context7, in `~/.pi/agent/mcp-adapter.json` (Context7 by sign-in, kept in an encrypted file whose key it makes once), and says whether Context7 is signed in (see "Library docs in Pi") |
| 11 | `install/open-webui.sh` | Open WebUI with uv, Chromium for Playwright, the `open-webui` user service |
| 12 | `install/ocr.sh` | Tesseract (Spanish and English data) and OCRmyPDF, for `tools/ocr/ocr-pdfs.sh` (see "Protected chats in Open WebUI") |
| 13 | `install/search-gateway.sh` | The search gateway's own Python environment and the `search-gateway` user service, which Pi and Open WebUI search through; moves an older setup's search settings and counts out of Open WebUI (see "Web search in Pi") |
| 14 | `install/kokoro.sh` | The Kokoro text-to-speech image (CPU only, about 5 GB on disk) and the `voice-router` user service (see "Voice in Open WebUI") |
| 15 | `install/tailscale.sh` | Tailscale (the private network to the helper PC, see "Two-PC setup"): installs it, enables `systemd-resolved` (with `/etc/resolv.conf` linked to it so NetworkManager doesn't overwrite MagicDNS on network changes), starts `tailscaled` at boot, lets you use `tailscale serve` without sudo, and signs in when this PC isn't signed in yet (**by hand**: scan the QR code on your phone or open the link it prints on any device — no browser needed on this PC) |
| 16 | `install/helper-keepalive.sh` | When `HELPER_HOST` is set: installs the helper Tailscale keepalive timer (`--enable` turns it on) |
| 17 | `install/backup-timer.sh` | rclone and the `localllm-backup` timer; it turns the timer on only with `--enable` (step 12; see "Automatic off-site backup") |

Each script can also be run on its own. After editing anything in `pi/`, run `install/pi-config.sh` to copy it
into Pi again. After editing a tool or function in `open-webui/`, run `install/open-webui-config.sh` (with Open WebUI
running) to push it into Open WebUI (it also moves Open WebUI from the model's old name to `local`, see "Model", and
sets document search: the multilingual embedding model and hybrid search, see "Protected chats in Open WebUI").
After editing a chat type, run `install/chat-types.sh`. `install/uninstall-searxng.sh` removes SearXNG and its data.

## Starting and stopping

```bash
./start.sh     # llama-server + Open WebUI + Kokoro + search gateway + Plannotator Inbox, and checks SearXNG
./stop.sh      # stops all of those except SearXNG, which frees the VRAM and about 19 GB of RAM
```

`start.sh` and `stop.sh` only run `./localllm.sh start` and `./localllm.sh stop`, the one way to start and stop
everything (on both PCs in the two-PC setup, see "Two-PC setup"; on this PC alone otherwise):

```bash
./localllm.sh start|stop|restart|status            # everything (stop leaves SearXNG running)
./localllm.sh start|stop|restart|status <service>  # one: llama, searxng, search-gateway, open-webui, kokoro, plannotator
./localllm.sh status                               # a table: each service, where it runs, its address, running or not
./localllm.sh apps pi | pi,owui                    # pi = Pi only (1 full slot with 2x context); pi,owui = 2 slots
                                                   # with Open WebUI enabled
./localllm.sh settings | chat-types                # the search settings or chat types page (split mode: see
                                                   # "Pages in split mode")
./localllm.sh monitor [port|--detect-hw]           # live web monitor (CPU, GPU, RAM on both PCs, mode & apps;
                                                   # default http://127.0.0.1:8095, Ctrl+C stops it)
```

Start goes in the order of the table below (llama-server first, so Pi can work even if a later service fails to
start); stop goes the other way round, llama-server last. A service that fails is reported at the end
("Not done: ...") and the others are still started or stopped.

The model takes most of the machine while it runs (see "Memory" below). Run `./stop.sh` before heavy work (two big
container projects at once, games) and `./start.sh` afterwards; loading the model again takes a minute or two.

Each tool also has its own pair of scripts, which `localllm.sh` calls on this PC:

| Tool | Start | Stop |
|------|-------|------|
| llama-server | `start/llama.sh` | `stop/llama.sh` |
| SearXNG | `start/searxng.sh` | `stop/searxng.sh` (not called by `stop.sh`; removes the containers, see below) |
| Open WebUI | `start/open-webui.sh` | `stop/open-webui.sh` |
| Search gateway | `start/search-gateway.sh` | `stop/search-gateway.sh` |
| Kokoro (text-to-speech) | `start/kokoro.sh` | `stop/kokoro.sh` |
| Plannotator Inbox | `start/plannotator.sh` | `stop/plannotator.sh` |

| Tool | Runs as | Starts at boot | Address |
|------|---------|----------------|---------|
| SearXNG | Docker containers with `restart: always` | Yes, unless stopped with `stop/searxng.sh` | http://127.0.0.1:8080 |
| llama-server | systemd service `localllm-llm` (not enabled) | No, `./start.sh` | http://127.0.0.1:8081/v1 |
| Open WebUI | systemd user service `open-webui` (not enabled) | No, `./start.sh` | http://127.0.0.1:3000 (single mode; in split mode it runs on the helper, see "Two-PC setup"; `./localllm.sh status` shows every current address) |
| Search gateway | systemd user service `search-gateway` (not enabled) | No, `./start.sh` | http://127.0.0.1:8090 |
| Kokoro (text-to-speech) | Docker container `kokoro` (`services/kokoro/`, CPU only, capped at 3 GB of RAM) | No, `./start.sh` | http://127.0.0.1:8880 |
| Voice router (picks Kokoro's voice by language) | systemd user service `voice-router` (not enabled), started and stopped with Kokoro | No, `./start.sh` | http://127.0.0.1:8881 |
| Plannotator Inbox | background process `plannotator inbox` (no tab; open it with `plannotator inbox --open`) | No, `./start.sh` (Pi also starts it when it sends) | printed by `start/plannotator.sh` (the port can change) |
| Pi | command `pi` | No, run it in a project folder | |
| Off-site backup | systemd user timer `localllm-backup` | Runs every 6 hours (00:00, 06:00, 12:00, 18:00), once enabled (see "Automatic off-site backup") | |

```bash
systemctl status localllm-llm                # running?
curl http://127.0.0.1:8081/v1/models         # model available?
journalctl -u localllm-llm -f                # llama-server logs
journalctl --user -u open-webui -f           # Open WebUI logs
journalctl --user -u search-gateway -f         # search gateway logs
docker logs -f kokoro                        # Kokoro logs
journalctl --user -u voice-router -f         # voice router logs
```

### Memory

With everything from `./start.sh` running, the 31 GB of RAM go roughly like this:

| What | RAM |
|------|-----|
| llama-server: the part of the model kept on the CPU | ~16.4 GB, pinned (CUDA page-locked memory: swap can't move it, so the model never slows down from swapping) |
| llama-server: prompt cache and the rest | ~1 GB, growing up to `CACHE_RAM_MIB` (2 GB) more |
| Open WebUI + Whisper | ~2.6 GB |
| Kokoro | ~1 GB, capped at 3 GB |
| Desktop | ~2 GB |
| **Left for editor, browser and containers** | **~8–10 GB** |

The GPU is full too (7.7 of 8 GB), so using VRAM as swap (projects like `nbd-vram`) doesn't apply here.

What `install/memory.sh` sets up, so it comes back after a reinstall:

- **zram:** compressed swap in RAM, used before the disk swap partition. Idle pages (background tabs, language
  servers, idle container processes) shrink to about a third and stay in RAM instead of going to the SSD.
- **Swap tuning** for zram (`/etc/sysctl.d/99-localllm-zram.conf`).
- **Browser Memory Saver** on by default when `BROWSER_POLICY_DIR` is set in `local.env`, as a *recommended* policy
  (`${BROWSER_POLICY_DIR}/recommended/localllm-memory.json`), so it can still be turned off in your browser's settings.

`services/localllm-llm.service` also sets `OOMScoreAdjust=-500`: if RAM runs out completely, the kernel kills a
runaway build or tab instead of llama-server, which is the largest process.

When memory is tight, in this order:

1. Lower `LLAMA_CACHE_RAM_MIB` in `local.env` (see "Tuning llama-server").
2. `LLAMA_VISION=off` in `local.env` (~0.9 GB, no image input).
3. `stop/kokoro.sh` when answers don't need to be read aloud (up to 3 GB).
4. Whisper `base` instead of `small` (Admin Panel → Settings → Audio): saves only ~0.3 GB and is clearly worse in
   Spanish, so it's a last resort.
5. `./stop.sh` (frees ~19 GB).

To check: `free -h`, `zramctl` (DATA is what was swapped, COMPR what it takes in RAM), and
`cat /proc/pressure/memory` (a `full avg60` well above 0 means the machine is waiting on memory).

### Dev containers next to the model

One big full-stack project at a time (Spring + Angular, .NET + React, with a database) fits next to the model. Give each
container a memory limit, the way Kokoro has one: a runaway build then fails inside its container (exit code 137,
"Killed") instead of freezing the desktop. In a dev container the language server (jdtls, Roslyn, tsserver) runs
inside the container too, so the limit has to include it.

| Project | Limit |
|---------|-------|
| Spring Boot + Angular (+ jdtls) | `6g` |
| .NET + React (+ C# language server) | `4g` |
| Database (Postgres and the like) | `1g` |

```jsonc
// .devcontainer/devcontainer.json
"runArgs": ["--memory=6g"],
"containerEnv": {
  "JAVA_TOOL_OPTIONS": "-XX:MaxRAMPercentage=60",    // the JVM takes only 25% of the limit by default
  "NODE_OPTIONS": "--max-old-space-size=3072"        // only if big Angular builds run out of heap
}
```

```yaml
# docker-compose.yml
services:
  api:
    mem_limit: 6g
  db:
    mem_limit: 1g
```

.NET needs no setting: its garbage collector already keeps its heap at about 75% of the container's limit.

### Voice in Open WebUI

- **Dictation (speech-to-text):** the microphone button in the message box, or the call (headphones) icon for
  hands-free voice mode. Whisper `small` runs inside Open WebUI on the CPU, never the GPU, and detects the language
  by itself (English and Spanish both work). It uses about 0.6 GB of RAM once used; its model (about 0.5 GB) is kept
  in `~/.open-webui/cache/whisper`. In split mode the helper's own Whisper service does it instead (see "Two-PC
  setup"): large-v3-turbo on its GPU, about 1 s for a 5-second recording, including the first one after a start. It
  picks the language only among Spanish, English and French, the voice router's (`WHISPER_LANGUAGES` in
  `services/whisper_server.py`), so a short or unclear clip can't come back in another language.
- **Reading answers aloud (text-to-speech):** the speaker icon under an answer, and in voice mode. Open WebUI sends
  the text, one sentence at a time, to the voice router (`services/voice_router.py`), which passes it on to the
  local Kokoro server. Both run on the CPU only; a sentence takes about a second.
- **The language you hear is picked automatically:** Kokoro takes the language from the voice, so the voice is set
  to `auto` and the voice router detects each sentence's language: Spanish → `em_alex`, English → `bm_george`
  (British), French → `ff_siwis`. Very short pieces such as "OK." or "7.3.4" keep the previous sentence's voice. In
  a mixed answer, the sentence in the other language gets that language's voice.
  - To change those voices, edit `VOICES` at the top of `services/voice_router.py`, then run `stop/kokoro.sh` and
    `start/kokoro.sh`.
  - To use one fixed voice instead, pick it in Settings → Audio (your own settings, not the Admin Panel); the router
    passes any voice other than `auto` through unchanged. Kokoro has 46 English voices (`af_`/`am_` American,
    `bf_`/`bm_` British), Spanish `ef_dora`, `em_alex`, `em_santa`, and French `ff_siwis`:
    `curl http://127.0.0.1:8881/v1/audio/voices` lists them all.
  - To log each choice, set `VOICE_ROUTER_DEBUG=1` in `services/voice-router.service`, run `install/kokoro.sh`,
    then `stop/kokoro.sh` and `start/kokoro.sh`.
- **Voice mode in every browser, phones included:** some browsers (Brave, Safari on iPhone and iPad, some phone
  browsers) refuse to let voice mode play its answers (`NotAllowedError … user gesture` in the console; voice mode
  then shows only the three dots and stays silent): Open WebUI copies every sentence into one shared audio player
  and starts it seconds after your last tap (Open WebUI issue #28681). `open-webui/loader.js` fixes it with nothing
  to install on the device. Open WebUI loads `/static/loader.js` on every page (an empty file of its own, meant for
  this); `open-webui/install_loader.py` puts this one there each time Open WebUI starts, on whichever PC runs it
  (`services/open-webui.sh`, `windows/run-service.ps1`), and `./localllm.sh deploy` puts a changed one in place at
  once (reload the page). An Open WebUI update brings back its empty file; the next start puts this one back.
  It unlocks voice mode's player during a tap, and plays a sentence the browser still refuses through a fresh audio
  object. For Chromium-based browsers that require autoplay to be allowed for Open WebUI's address, set
  `BROWSER_POLICY_DIR` in `local.env` (for example `/etc/chromium/policies`, `/etc/opt/chrome/policies`, or
  `/etc/brave/policies`): `install/browser-address.sh` (run by `install.sh` and by `./localllm.sh switch`) writes
  `managed/localllm-autoplay.json` there for `http://127.0.0.1:3000`, `http://localhost:3000`, and (in split mode)
  `https://<helper's Tailscale name>` (restart the browser to apply it).
  It also tunes voice mode's microphone, with the two values at the top of `loader.js` (then restart Open WebUI, or
  `./localllm.sh deploy` in split mode, and reload the page):
  - **Speech threshold** `SPEECH_THRESHOLD_DB` (−40 dB; Open WebUI's own is −55 dB): sound below it doesn't count as
    you talking. Raise it if voices or laughter from another room still get picked up, lower it if it misses you.
  - **Automatic gain control** `AUTO_GAIN` (off): the browser's gain control boosts distant voices while you are
    silent.
- **Microphone access:** browsers only allow it on localhost or HTTPS: in single mode it works at
  http://127.0.0.1:3000 on this PC; in split mode Open WebUI is served over HTTPS at the helper's Tailscale name (a
  real certificate, `tailscale serve`), so voice also works from your other devices and a phone in your tailnet.
  Open WebUI shows "Permission denied when accessing media devices" for any microphone failure, not only a refused
  permission. When the site's microphone is allowed and it still appears, run
  `navigator.mediaDevices.getUserMedia({audio:true}).catch(e => console.log(e.name))` in the browser's console:
  `NotFoundError` means the browser sees no microphone at all, typically after the desktop's sound system (PipeWire)
  restarted while the browser was open. Restart the browser (e.g. `chrome://restart` keeps the tabs).
- **Settings:** Admin Panel → Settings → Audio holds the Whisper model and the text-to-speech address (the voice
  router, `http://127.0.0.1:8881/v1`, model `kokoro`, voice `auto`). In split mode its speech-to-text engine is
  "OpenAI" at `http://127.0.0.1:8882/v1`, the helper's Whisper service (`./localllm.sh switch` sets it). They are
  saved in `webui.db`, so the backup covers them; `services/open-webui.sh` holds their defaults. If memory gets tight in single mode, set Whisper back to `base` there.

### Plannotator Inbox

The Inbox is one browser window that stays open, where the model (through Pi) leaves messages: a question only you can
answer, a decision, or a summary of finished work with files to annotate. The model sends and keeps working; you answer when
you can, and your reply reaches that Pi session as its next message.

```bash
plannotator inbox          # opens the Inbox; leave it running
plannotator inbox --open   # opens the window of an Inbox that already runs (./start.sh starts it without a tab)
```

- **Every plan goes to the Inbox:** each time the model writes or revises a plan, `pi/AGENTS.md` ("Plans") has it write
  `PLAN.md`, send it to the Inbox as an attachment, and stop until you answer. Annotate it there and press Send; the model
  revises it, or starts the work once you approve (in words: there is no Approve button). If the Inbox isn't running,
  Pi starts it in the background; open it with `/plannotator-inbox`. Nothing technically stops the model from editing code
  before you answer.
- **Plannotator plan mode is the strict version:** with `/plannotator-plan-mode` (Ctrl+Alt+P) on, only markdown
  files can be written, so the model can't touch the code while planning. Its planning instructions are replaced by
  `pi/plannotator.json` (copied to `~/.pi/agent/plannotator.json` by `install/pi-config.sh`): plans go to the Inbox too,
  and no review tab opens. Approve in the Inbox, then turn plan mode off yourself (Ctrl+Alt+P) and tell the model to go
  ahead. Without the review tab there is no Approve button and no `[DONE:n]` progress tracking.
- Pi gets the `plannotator_inbox` tool (action `send_message`, `read_thread`, ...) only in sessions started after the
  Inbox has run once on this machine. In an older session, run `/reload` first.
- The tool's definition adds about 3,200 tokens to every Pi request. To turn it off, set `"pi": false` under
  `inboxTool` in `~/.plannotator/config.json` (or use the Inbox's Settings) and start a new session.
- If the tab gets closed, `/plannotator-inbox` in Pi opens it again.
- `install/plannotator.sh` installs only the binary (`~/.local/bin/plannotator`), at the same version as Pi's
  extension, and turns on the tool for Pi. The official installer's other parts (skills and commands for other coding
  agents) are left out. The Inbox's note in Pi's instructions is the marked section at the end of
  `pi/AGENTS.md`; `plannotator uninstall` would remove it from `~/.pi/agent/AGENTS.md` but not from the repo.
- The Inbox's settings (`~/.plannotator/config.json`: theme, sounds, notifications, the Pi tool) are part of the
  settings backup; its threads (`~/.plannotator/inbox`) are not.

### File changes in Pi

`pi/extensions/edit-guard.ts` lets the model change files (`edit`, `write`, bash commands that write) only when your latest
message asks for it: words such as create, write, fix, add, update, yes, go ahead, do it, or a file name in a message
that isn't a question (a question is recognised in 35 languages, see "Languages"; the permission words are English
only). Otherwise the change is blocked and the model describes it and asks first. "Your latest message" is
only what you send: typed, `pi -p`, or your reply in the Plannotator Inbox (without its title line, which the model wrote).
The hidden messages extensions add (the Inbox reminder, the end-of-answer checks) never count, either way.

`write` replaces a whole file without checking what was there (`edit` needs the exact old text), so on a file that
already exists it is allowed only after the session read all of it, or wrote it itself. Otherwise the model is told to
use `edit`, or read the whole file first. That way a file is never rewritten from memory or from a subagent's summary.
New files can be written as before. In a resumed session, a file has to be read again before a `write`.

`pi/extensions/name-guard.ts` stops the model from writing made-up file names into documents (.md, .txt, .rst, .adoc,
.org). Each file name an `edit` or `write` adds must be a file in the project (on disk, or that base name in
`git ls-files`), the file being written, a name from your messages or a tool result in the session, or a name the model
proposed just before you replied "yes" / "ok" / "go ahead". Otherwise the change is blocked and the model is told to use a
real name or ask you. Code files aren't checked.

### Todo list, subagents and long sessions in Pi

- **Todo list** (`pi/extensions/todo.ts`, Pi's own example unchanged): a `todo` tool (add, toggle, list, clear) that
  `AGENTS.md` and the tool's own description ask the model to use first for any task that changes more than one file
  or ends with running tests (add the stages, tick each off), and `/todos` to see the list. A softer "3 or more steps"
  hint was ignored even on a five-file task. It is stored in the session, so it is right after a resume or a branch.
  Ticks are checked in `agent-browser-fix.ts`: an item can't be ticked before any work has been done since the list
  was made (the model did that despite the wording). Several ticks after the work are fine (refusing them left items
  unticked); un-ticking and `list` always work. For a rename, `AGENTS.md` asks for a grep for the old name before the
  answer (it twice left a parameter with the old name).
- **Subagents** (`pi/extensions/subagent/`, from Pi's example): the `subagent` tool runs a task in a separate `pi -p`
  with its own clean context; only its answer (at most 12 KB) comes back. Agents in `pi/agents/` (copied to
  `~/.pi/agent/agents`): `code-finder` (finds and summarizes code; Pi's example calls it `scout`), `web-researcher`
  (searches and reads web pages, answers with the links; ours), `planner` (writes a plan), `reviewer` (reviews
  changes), these four without changing files, and `worker` (makes changes); all use `local`. Workflows in
  `pi/prompts/`: `/implement <task>` (code-finder → planner → worker), `/find-and-plan <task>`,
  `/implement-and-review <task>`. `AGENTS.md` tells the model to send the code-finder before reading more than 5 files
  to answer a question about a codebase, and the web-researcher for web research that means reading several pages
  (a single fact or one page it looks up itself). Pages can be 5,000-15,000 tokens each; only the answer and its
  links come back. The web-researcher follows the same web rules (it loads the same `AGENTS.md` and extensions).
  How many run at once is this PC's choice: `SUBAGENTS_AT_ONCE` in `~/.config/localllm/local.env` (1, also when
  unset: one after another; more: side by side, at most the profile's slots; then `install/pi-config.sh` and restart
  Pi). The limit holds for the whole session, however the model splits its calls: the rest wait in one queue, and a
  call may hold up to 8 tasks. `SUBAGENTS_MAX` (8 when unset) is the most one message of yours may start in all; a
  call that would go over it is refused, and the model is told how many are left. A subagent uses the last free slot, so the session's own cache in slot 0 stays (it uses
  Open WebUI's slot, so a chat there waits meanwhile). Each subagent is another Node
  process (~150-300 MB RAM; the model isn't loaded again), and two at once share the GPU at about half speed each.
  A subagent follows the same file rules as Pi: it may change files only when **your** latest message asked for a
  change (the task text the model wrote doesn't count), it can't use the Plannotator Inbox or start subagents itself,
  and only the agents in `~/.pi/agent/agents` run, never ones from the project folder.
- **Compaction** (`install/pi-config.sh` sets it in `~/.pi/agent/settings.json`, sized from the model profile): when
  less than one answer plus room for a tool result is free (12,288 tokens of the 57,344), Pi summarizes the older part
  of the session and keeps the last fifth (11,468 tokens) as it is, instead of stopping. `/compact` does it by hand.
- **Cost:** the todo and subagent tools and their `AGENTS.md` lines add about 650 tokens to every request (5,437 →
  6,093 for a one-word question).

### Sandbox in Pi

pi-sandbox checks Pi's `read`, `write` and `edit` tools and runs bash commands in a sandbox (bubblewrap). Its rules
are `~/.pi/agent/sandbox.json`; `install/pi-config.sh` adds the repo's `pi/sandbox.json` to it (nothing is removed):
- Writes go only to the project folder and `/tmp`. Elsewhere `write`/`edit` ask you first, and bash can't write.
- Never writable, without asking, even when Pi runs in a folder that contains them (such as `~`): `~/.ssh`, `~/.gnupg`,
  `~/.pi`, `~/.config`, `~/.bashrc`, `~/.bash_profile`, `~/.profile`, and `.env`, `.env.*`, `*.pem`, `*.key`. So Pi
  can't edit files in `~/.config` even when you ask: do that yourself, or run `pi --no-sandbox`. (Blocked rather than
  asked on purpose: the model can't change your apps' settings by mistake.)
- While a bash command runs, a protected name that doesn't exist in the folder (such as `.env`) shows there as an empty
  placeholder that can't be written: that is how the sandbox stops it being created. It is removed when the command
  ends, so nothing is left in your projects.
- Without a screen to ask on (`pi -p`, subagents), whatever would be asked is blocked.

### Library docs in Pi

Besides web search, the model can read library and API docs from two MCP servers, through the `pi-mcp-adapter` package:

- **GitMCP** (`https://gitmcp.io/docs`, free, no key): a GitHub project's current docs (llms.txt, README, docs
  folder), read when asked.
- **Context7** (`https://mcp.context7.com/mcp/oauth`): indexed docs for a specific version, such as Next.js 14 or
  Spring Boot 3.2. It needs a one-time sign-in (see "Signing in to Context7" below).

Which comes first depends on the question (`pi/AGENTS.md`, "Library and API docs"):

- **A project that pins a version** (a package.json, *.csproj, pom.xml... in the folder, or a version in your
  message): Context7 with that version's ID, then GitMCP, then web search.
- **Latest docs, or no project:** GitMCP or the official llms.txt first. `agent-browser-fix.ts` refuses a Context7
  call there (twice at most) and hands the model the matching GitMCP call instead. Only GitMCP calls that read docs
  count as "tried"; its name-to-repo lookup doesn't.
- **Releases and changelogs:** the project's releases page or web search, never Context7. GitHub's releases pages lose
  their dates in the page text, so `agent-browser-fix.ts` adds each release's date from GitHub's API (up to
  10 releases, or the one tag) when such a page is read.
- **Issues and pull requests:** a repo's search page (`/issues?q=…`, `/pulls?q=…`) lists newest first, both kinds mixed,
  25 a page, so the one asked about can be pages away. When such a page is opened, `agent-browser-fix.ts` adds GitHub's
  best matches for the same words from its search API: issues and pull requests apart, ten each, most relevant first.

GitHub's API is used without a token, or with one when `~/.config/localllm/github-token` exists (mode 600; in the
settings backup). A token raises the search limit from 10 to 30 a minute. To make one: GitHub, Settings, Developer
settings, Fine-grained tokens, Generate new token, with "Public repositories (read-only)" and no permissions; then
save it without it showing or going into your shell history:

```bash
read -rs T && printf '%s\n' "$T" > ~/.config/localllm/github-token && chmod 600 ~/.config/localllm/github-token && unset T
```

Context7's calls are kept down by `agent-browser-fix.ts`:
- library IDs from `resolve-library-id` are cached for 30 days in `~/.pi/agent/context7-ids.json`, so the next
  question about that library skips that call;
- at most 3 Context7 calls per question;
- the same docs lookup twice in one question is refused.

**Signing in to Context7.** Once, in an interactive Pi session: `/mcp-auth context7`, then approve in the browser.
`pi-mcp-adapter` keeps the sign-in in its own encrypted file (`~/.pi/agent/mcp-oauth-encrypted/`, AES-256), not in a
desktop keyring, so it works the same on any desktop. Its key (`PI_MCP_ADAPTER_OAUTH_FILE_KEY`) is made once by
`install/pi-mcp.sh` and kept in `~/.config/localllm/local.env` (mode 600); Pi loads it from there itself when it
starts (`pi/extensions/mcp-sign-in-key.ts`), so nothing else needs it and no login is required. The encrypted
file and `local.env` are both in the settings backup, so a reinstall comes back signed in. Losing the key only means
signing in again. Non-interactive runs (`pi -p`) use the stored sign-in. `install/pi-mcp.sh` says whether it's there
(it runs `pi-mcp-adapter doctor`, which never starts a sign-in), and `tests/context7-check.sh` does a real lookup
through Pi (Next.js 14 docs, Context7's tools only, judged from Pi's session file). No API key is used.

Tool descriptions go out with every request, and the servers' own ones are long (about 1,700 tokens), so
`pi/extensions/short-tool-docs.ts` swaps them for short ones (about 500) just before each request. It also drops Pi's
`tool_search`, and `disableProxyTool` hides the adapter's `mcp` proxy, since the five docs tools are registered
directly. GitMCP's code search and URL fetcher are left out on purpose: pages are read with agent_browser.

**No single tool result fills the context.** Pi's own limit for bash and read output is 50 KB, which for file listings
is about 30,000 tokens: more than half of the 57,344-token window, so one `find -exec ls -l` over the repo was enough
to set off compaction. `pi/extensions/cap-output.ts` cuts every result the model sees to a fifth of the window in
characters (11,468: between about 3,000 and 7,000 tokens) and saves the full text to `/tmp/pi-cap-output/`, named in
the result. bash keeps its end, read keeps its start and ends with "Use offset=N to continue". Calls made inside a
codemode script are left whole, since the script filters them itself. When a codemode script fails because it used
Node's `require` (the sandbox is QuickJS: tools only), the result says how to call `tools.bash` and `tools.read`
instead, so the model fixes the script rather than falling back to a raw bash call. It also refuses `read` on a
binary file (a NUL byte in its first 8 KB; images excepted, since Pi shows those as pictures) before it runs, and
says what the file is and how to look inside (for an SQLite database: `sqlite3 -readonly FILE .tables`). The same
goes for bash output, inside codemode scripts too: each run of binary lines (`head` on a database) becomes one line
saying what it looks like and how to look inside.
`pi/AGENTS.md` tells the model to do the task in codemode scripts when asked for codemode.

### Web search in Pi (and Open WebUI): the search gateway

Every web search, from Pi, from Open WebUI's chats and from the protected chats' lookup tool, goes through the search
gateway (`services/search_gateway.py`, the `search-gateway` user service on 127.0.0.1:8090). It runs the search engine
itself (`services/search_gateway_engine.py`) in its own Python environment, and **doesn't need Open WebUI**: Pi
searches with Open WebUI stopped, uninstalled, or with its database gone. Everything about searching is kept by the
gateway, nothing in Open WebUI:

| What | Where | Backup |
|---|---|---|
| Settings: the services' API keys, their order, limits, reset days | `~/.config/localllm/search-gateway.json` (only you can read it), edited on http://127.0.0.1:8090/settings | `backup/localllm/` |
| Usage counts (what each free allowance has left), result cache, search log | `~/.local/share/localllm/search-gateway.db` (SQLite) | `backup/search-gateway/` |
| Python with aiohttp, pydantic and Playwright (page reading) | `~/.local/share/localllm/search-gateway-venv` | reinstalled by `install/search-gateway.sh` |

```
web_search (pi/extensions/search-gateway.ts) ─┐
Open WebUI's "Search Gateway" tool (thin) ────┼─► search gateway, 127.0.0.1:8090 (services/search_gateway.py)
Protected Lookup (protected chats) ───────────┘     └─► the services in its settings, in their order;
                                                          SearXNG only as the last fallback
```

- **Searches run side by side.** One event loop runs every request, so a Pi search and an Open WebUI search at the same
  moment don't wait for each other (before, the gateway took one search at a time). Each count, cache or log change
  is one short SQLite transaction (about a millisecond), and the dashboard's reads never wait for it (WAL mode).
  Searches still running count against a service's allowance, so two at once don't both pass its last bit.
- **Open WebUI's tool is a thin client** (`open-webui/tools/search_gateway.py`): the same three functions and
  descriptions as before (`web_search`, `read_page`, `plan_research`, so the model sees the same tool), passed on to
  the gateway, which also keeps each answer's search limits; its status lines and citations show in the chat. Its
  only settings are the gateway's address and a timeout. When the gateway isn't running, it tells the model that web
  search is unavailable and that `./start.sh` starts it.
- **The settings page** lists every setting with its description and default, with a filter. API keys are never
  sent to the page: it shows whether each one is set; a key field left empty keeps the key, and "remove this key"
  clears it. Values are checked before anything is saved, the file keeps only what differs from the defaults, and the
  next search uses it. It answers only this computer (127.0.0.1 or localhost, not another name pointed at it), and
  accepts changes only as a JSON request from its own page, so another website open in your browser can't change
  them.
- **From an older setup:** `install/search-gateway.sh` moves the old tool's Valves (API keys included) out of Open
  WebUI's database into the settings file, and the usage counts, cache and log from `~/.open-webui/` into the
  gateway's database; `install/open-webui-config.sh` then removes the keys from Open WebUI's copy of the tool. A
  backup from before is restored the same way. Before the rename the gateway was "Local Web Research" (tool
  `local_web_research`, service `web-research`, files `web_research_*`); the same scripts move those too.
- `web_search` takes only a query (and a result count). There's no backend to pick, so Pi can't reach SearXNG,
  DuckDuckGo or another engine directly. Each result list names the service that answered (`Service: tavily`).
- `site:<domain>` narrows a search to one site. Pi's search table in `pi/AGENTS.md` uses only `site:` filters. When
  the model writes a SearXNG `!bang` anyway, `agent-browser-fix.ts` turns it into the matching filter (`!gh` →
  `site:github.com`, `!mdn` → `site:developer.mozilla.org`...) or removes it. The server also removes any bang left in
  a query.
- The model can't type into a search engine's home page either (google.com, bing.com...): it gets the results URL
  to open instead (when you named that engine) or the web_search call.
- When web_search says the search gateway isn't running, the browser and curl/wget are refused until your next
  message: the model tells you to start the gateway instead of searching another way.
- Commands that install software on your PC (global npm, pip into the system, `pi`/`cargo`/`brew install`, `sudo`,
  `pacman -S`, `curl … | sh`) are refused unless your message asks for an install; installs inside a project
  (`npm install`, `pip install -r requirements.txt`) still run.
- A plan you ask for that ends up only in the chat gets one reminder: write it to PLAN.md and send it to the Inbox
  (just PLAN.md where the Inbox tool is off, as in `pi -p`).
- A file the system protects stays protected: when `rm` or another change fails as busy, read-only or not allowed,
  the model is told to say so and stop, and `umount`/`mount`/`chattr`/`setfacl` are refused unless your message asks.
- When tests it runs fail, the model is told to fix the code, not to change what you asked for or weaken the test.
- An answer that only repeats a tool's instructions (for example the "search blocked, use web_search" message) is sent
  back once: the model makes the call it names, then answers.
- The model can't search in the browser: `agent-browser-fix.ts` blocks search engines' results pages (Google, Bing, Brave,
  DuckDuckGo, the local SearXNG...) and hands the model the same words as a `web_search` call. That holds when your message names
  an engine too ("search on Google for..."): search engines refuse automated browsers (Google and Mojeek answered with a
  CAPTCHA or an error every time in the test runs), so the model uses `web_search` and begins its answer by saying so.
- Answers come in the language of your message: `AGENTS.md` says so, and when the answer is in another writing system
  (Latin, Chinese/Japanese, Korean, Arabic, Cyrillic, Hebrew, Greek, Devanagari, Bengali, Thai) `agent-browser-fix.ts`
  asks once for it again (it once answered an English message in Chinese). Languages with the same script (English,
  Spanish) can't be told apart this way.
- Grep on a saved page is run by `agent-browser-fix.ts` (bash can't see it): grep's basic syntax is translated
  (`\(…\)` groups, `\|` or; bare `( ) | + ?` literal; `-E` as given). Placeholder URLs in code examples
  (`example.com`, `localhost`) don't count as unread sources.
- Answers don't say when a page was "accessed": the model can't know it and the date was wrong every time, so
  `agent-browser-fix.ts` asks once to remove "read/accessed (on) <date>" (English phrasing). Dates a page shows stay.
- A loop ends: after 3 refused repeats of the same call in one task, every further tool call is refused until the model
  answers (it once sent the same `ls` 26 times).
- `AGENTS.md` leaves out what the guards already enforce (curl/wget, Context7 limits, search engine pages, the search
  budget, page access dates), about 2,300 tokens instead of 2,410. The link-finding eval and the named-engine line
  stay: without them the model clicked links and typed into Google's search box.
- The model can't guess where an opened page was saved: right after an `open`, before any `get text`, a read of a
  file that doesn't exist (or of a folder) is refused and the model is told to get the page's text first.
- The model can't clone a repository to look something up (issues and pull requests aren't in a clone; it was used to
  get around the search budget): `agent-browser-fix.ts` refuses `git clone`/`ls-remote` of a remote repo unless your
  message asks for a clone, and points to the repo's issue and pull request pages.
- The model can't click links from a snapshot, also not inside a `batch`: `agent-browser-fix.ts` blocks clicks on link refs (e.g. `@e3` pointing to
  `link "blog" [...]`) and tells the model to find the URL with an `eval` command instead, then open it. Buttons, tabs
  and form fields are clicked as before.
- When pages were read but the answer names none of them: `agent-browser-fix.ts` reminds the model to name the pages you
  used, with full URLs copied from what it read (`[title](https://...)`). For releases and news, include dates. It
  asks only once per message (max 3 finish checks, shared with other checks). The checks against what was read
  (claims not in any source, "from memory", re-read the question) run only when the answer used the web (`web_search`,
  `agent_browser`, the docs tools): for an answer from files, such as a code-finder's line numbers, they made the model
  write its answer up to three times.
- When the server isn't running, `web_search` says so, and the model tells you to run `./start.sh` instead of searching
  another way.
- **A repeated search gets a second opinion.** When the same words are searched again (in any order) for one question,
  the results weren't useful, so the repeat skips the cache, the service that answered, and every service in the same
  group of the `retry_groups` setting. The default group is the services that return Google's results (Bright
  Data, SerpApi, HasData, Geekflare), so a retry after one of them goes to a different index (Parallel, Exa,
  Tavily...). Pi allows 2 retries, Open WebUI chats 1; after that the same words are refused. Edit the groups
  (`retry_groups`) on the settings page; both apps use them.
- **Time-limited plans.** `temporary_service_order` replaces `service_order` through `temporary_order_until`, then the
  normal order applies again by itself (used while Geekflare's paid year lasts: Geekflare first until 2027-11-12).
  `geekflare_paid_monthly` and `geekflare_paid_until` do the same for Geekflare's allowance: 10,000 credits a month
  until that date, then `geekflare_monthly_free` (500). Geekflare has no API that reports credits left, so the tool
  counts its own searches (2 credits each); compare with Geekflare's dashboard now and then.
- **Long pages come in parts.** `read_page` (Open WebUI's chats) returns up to `max_page_chars` (20,000 characters)
  at a time. A longer page ends with a note: which part this is, that `part=2` reads on, and the page's later sections.
  An address with a `#section` starts at that section's heading: the local browser notes where each section (an
  element with an `id`) starts; for a page from a fallback service, the heading is found by its words. A page read
  once is kept in memory for 15 minutes, so its next parts and other sections cost no new fetch (each part still
  counts as a page read for the answer's limit). Example: Python 3.14's "What's New" page has 134,000 characters, 7
  parts; `...3.14.html#uuid` starts right at its uuid section. The protected chats' lookup gets the text without the
  note. Pi reads pages with its browser (`agent_browser`), not with `read_page`, so this doesn't change Pi.
- A changed setting, or a new version of the engine in the repo, applies to the next search, without a restart.

To also log what each search did, set
`SEARCH_GATEWAY_DEBUG=1` in `services/search-gateway.service`, run `install/search-gateway.sh`, then `stop/search-gateway.sh`
and `start/search-gateway.sh`. The log (`journalctl --user -u search-gateway -f`) then shows, per search, the query, which
service answered, how many results came back and how long it took. It also shows the services that failed or answered
"too many requests", and each usage check against a service's API (that it ran, not the numbers).

### Long research chats in Open WebUI

Open WebUI sends every earlier tool result to the model again with each new message, word for word. A page read
returns up to 20,000 characters (`max_page_chars`, about 5,000 tokens), so a research chat used to reach the
compaction limit (50,176 tokens, see "Model") after two or three messages. Compaction then summarizes the whole
earlier chat, your messages included.

The **Old Pages** filter (`open-webui/functions/old_pages.py`, on for every model; `install/open-webui-config.sh`
switches it on) leaves those old pages out once a chat gets long:

- **What changes:** the text of each page read for an *earlier* answer becomes a one-line note, with the page's
  address (with its `#section`), its length, and that `read_page` reads it again (with the same `part`). Only what goes to the model changes; the chat as saved
  and shown stays complete.
- **What stays:** the answer being written keeps its pages in full, and search results (short), your messages and the
  answers stay as they are. So do other tools' results, such as the protected chats' lookup, and pages under 1,500
  characters.
- **When:** only once the chat, counted with every page in full, passes 40% of the compaction limit (20,070 tokens,
  `start_at_tokens` in its Valves). Below that nothing changes: llama-server's cache of the chat is reused as it is,
  and a follow-up question still has the pages. Changing earlier messages costs llama-server a re-read of the last
  answer, about 10–15 s, so it isn't done when it isn't needed.
- **Follow-ups:** a question about something the earlier answer left out makes the model read the page again, which is
  free with the local browser. A detail the model doesn't realize was on a page can still be missed; "check that page
  again" helps.
- **Measured** on a real three-message research chat that compacted during the third answer: the chat sent with the
  third message goes from about 24,000 to 7,800 tokens (without the instructions), and with a fourth from about 47,000
  to 12,000.
- It doesn't limit one answer that reads very many long pages; `max_page_chars` on the gateway's settings page does.

`tests/old-pages-check.py` (run by the guard tests) checks it; the `debug` Valve prints what it shortened to the Open
WebUI log.

### Search usage dashboard

http://127.0.0.1:8090/dashboard shows each search service's usage for Pi and Open WebUI together. The search gateway
serves it, so there's no extra service. It listens on 127.0.0.1 only, and the page refreshes itself every
30 s. In split mode it is on the helper, at `http://<helper's Tailscale name>:8090/dashboard` (see "Pages in split
mode"). It uses no external scripts or fonts (so it works offline) and follows the system's light or dark mode.
`/usage.json` returns the same data as JSON.

- **Per service, in the order they are tried.** The temporary order is used while it applies. Each service has its
  own color (the same in the charts below) and a card with its status (available / near the reserve / used up / no
  key) and a donut: what is left is in the middle (credits, requests, dollars or searches), the allowance under it,
  and the grey part of the ring is the 50-search reserve the tool keeps back. Hovering over (or tabbing to) the small
  chevron at the card's top left shows the details: used this cycle, what is left in searches, when the cycle
  resets, whether the numbers were confirmed by the service or counted by the tool, the retry group, and extras: Parallel's monthly budget and one-time credit, Geekflare's paid plan and its
  end date, Exa's and Firecrawl's one-time credits, Browserbase's page fetches. SearXNG is last, as the fallback.
- **The numbers come from the engine's own functions** (`_usage`, `_simple_free`, `_limit`, `_has_credit`...), so the
  dashboard shows the limits the searches actually use.
- **History.** The gateway logs each search in its database: the time, the app (Pi, Open WebUI or Protected Lookup),
  the service that answered, the result count, whether it came from the cache or was a retry, and which services
  failed first. **It stores no search words**, so private searches are never kept. Turn on `log_search_words` on the
  settings page to store them too (`/usage.json` then includes them with the latest 25 searches). The log keeps 90
  days. The page shows searches per day for the last 30 days (per service, with cache hits, and per app). The log is in
  the settings backup, with the usage counts.
- `SEARCH_GATEWAY_BIND` in `services/search-gateway.service` changes the address the server listens on. Only ever use a
  private address, because `/search` is served there too.

### Protected chats in Open WebUI

Chats where the documents and messages never leave the computer. The only thing that can go out is a lookup on the
websites chosen for that kind of chat, checked for personal data and approved by you in a confirmation box before it
is sent. Each kind of chat is a **chat type**: one JSON file in `~/.config/localllm/chat-types/` (kept out of the
repo, saved by the settings backup), turned into a preset in Workspace → Models by `install/chat-types.sh`. The repo
has only a neutral example, `open-webui/chat-types/example.json`, which lists every key.

- **Making one in the browser:** http://127.0.0.1:8090/chat-types (also linked from the search usage dashboard;
  the search gateway must be running; in split mode run `./localllm.sh chat-types` first, see "Pages in split
  mode"). "New chat type" starts from the example; fill in the form and press
  "Create": the page writes the file and builds the preset. Then "Download sources" fills its Knowledge base, and
  "Delete" removes its preset and Knowledge base from Open WebUI (the file is kept in `chat-types/.deleted/`; chats
  already made with it stay). Each job's output shows at the bottom. The page only answers this computer, and only
  changes made from the page itself (not from other websites). It runs the same builder as below, so every protection
  setting is still set by code, never by the form.
- **Making one by hand:** `mkdir -p ~/.config/localllm/chat-types`, copy the example there as `<name>.json` (lower
  case, digits, dashes), edit it, then `install/chat-types.sh` (after `install/open-webui-config.sh`). It sets: the name,
  description, system prompt and starter prompts; `allowed_sites` (domains, subdomains included: `example.org`
  covers every `*.example.org` site); `sources` (official pages for the chat type's own Knowledge base); `patterns` (extra
  personal-data formats, e.g. ID or member numbers), `reference_words` (a number right after one of these words is
  a public reference and may be sent, e.g. "rule 12"), `allowed_patterns` (public reference codes whose numbers may be
  sent) and `allowed_terms` (capitalised words that are never names, e.g. institutions); `users` (the Open WebUI accounts, by email, that see
  it; admins always do). Run it again after any edit: **the files are the source of truth**, so a preset edited by
  hand in Open WebUI is put back on the next run, and a run with nothing changed changes nothing.
  `install/chat-types.sh --dry-run [folder]` checks the files and prints the presets without touching Open WebUI;
  `--only <id>` builds (or with `--refresh-sources`, downloads) just one; `--remove <id>` deletes one's preset and
  Knowledge base.
- **What the preset gets:** the model `local`, only the "Protected Lookup" tool, the "Protected Chat" filter, file
  upload, vision and citations on; web search, image generation, code interpreter, terminals and memory off, and
  Open WebUI's built-in tools that reach other chats, notes, schedules, webhooks or other models off.
- **The filter** (`open-webui/functions/protected_chat.py`) runs on every message of those presets and can't be
  switched off in a chat: it leaves only the lookup tool and switches web search, images, the code interpreter,
  memory and tool servers off, whatever was turned on in the chat. `install/chat-types.sh` switches it on, for those
  presets only. **Limit:** switching a chat to another model leaves the protection for the rest of that chat (each
  chat type's prompt and description say so).
- **The lookup** (`open-webui/tools/protected_lookup.py`) reads the allowed sites from the chat's preset; with none
  (a hand edit dropped them) it refuses every lookup. It removes from the query: emails, phone numbers, addresses,
  long numbers, the chat type's patterns, and any name or number that appears in this chat's messages or documents
  (uploaded files and Knowledge added with `#`), while public references (a number after one of the chat type's
  `reference_words`, e.g. "rule 12", or a match of its `allowed_patterns`) stay. Then it shows the exact final query
  in a confirmation box; **nothing is sent without your click** (the click is the real guarantee; the automatic check catches the usual patterns). It searches through
  the search gateway (same services, cache and usage counts as Search Gateway, logged as "Protected
  Lookup"), drops every result outside the allowed sites, and reads the best allowed page (the server's `/read`), so
  the answer quotes the actual text with its link.
- **Knowledge base:** a chat type with `sources` gets its own Knowledge base ("<name> - sources").
  `install/chat-types.sh --refresh-sources` downloads the sources' official pages into it as text (laws get amended;
  a changed text replaces the old one, an unchanged one is left alone). A url with `{n:03}` is a numbered series of
  pages, read until the first missing one. **Case documents never go into a preset's Knowledge** (it is searched in
  every chat using the preset): upload them into the chat, or keep them in your own Knowledge bases and add them
  with `#`.
- **Accounts:** each person gets their own Open WebUI account (Admin Panel → Users), so their chats, files and
  Knowledge stay theirs; add the account's email to the chat type's `users` and run `install/chat-types.sh`. Offer
  Memory off for that account (Settings → Personalization), or facts are remembered across their chats.
- **Scanned PDFs** have no text for Open WebUI to read: run `tools/ocr/ocr-pdfs.sh <folder>` first. It writes a
  copy with a text layer of every PDF in the folder to `<folder>/ocr/` (Spanish and English by default; another
  language: `tools/ocr/ocr-pdfs.sh <folder> eng+fra`), skips pages that already have text and never changes the
  originals; upload the copies. It runs where Open WebUI runs and uses RAM only while it runs (about 0.3-1 GB).
- **Document search, for every chat:** `install/open-webui-config.sh` sets the multilingual embedding model
  `intfloat/multilingual-e5-small` (better for Spanish, about the same for English; about 0.3-0.4 GB more RAM inside
  Open WebUI than the previous English-only model) and hybrid search (keyword matching next to the embeddings, for
  article numbers, names and case numbers).
- **Other chats and Pi are unchanged:** the presets, filter, tool and Knowledge bases apply only to chats started
  with a chat type. Lookups share the search services' free allowances with Pi and Open WebUI.

### Languages

The rules that react to words in your message (or in the model's answer) understand 35 languages: English, Spanish,
French, German, Italian, Portuguese, Dutch, Polish, Russian, Ukrainian, Swedish, Norwegian, Danish, Finnish, Greek,
Czech, Romanian, Hungarian, Turkish, Arabic, Hebrew, Persian, Hindi, Bengali, Urdu, Chinese, Japanese, Korean,
Indonesian, Malay, Vietnamese, Thai, Filipino, Swahili and Afrikaans. They are:

- Pi, `thinking-switch.ts`: a message that asks for the latest version, is about an error or starts with an action
  ("busca...", "найди...") gets reasoning from the start; "current directory" in any language doesn't count as
  "current".
- Pi, `edit-guard.ts`: whether a message that names a file is a question (`?`, `¿`, `؟`, `？`, or a question word).
- Pi, `agent-browser-fix.ts`: "latest" (the free docs sources before Context7), "search on Google" (the answer says
  why the search went through Search Gateway instead), and the end-of-answer checks (a source you named, a fix, "look it up", an offer at the end
  of the answer, "from memory"). Bot-check pages are recognised in every language.
- Open WebUI's "Search Gateway": current-information questions ("precio actual", "最新版本") get recent results,
  years you didn't ask for are dropped with their month in any language, and bot-check pages are recognised.

Every word list is in `lang/phrases.json`, one block per language. Rules match phrases, not loose words ("current
price", not "rates"), lower-cased and without accents on both sides ("ultima" matches "última"), as whole words; in
Chinese, Japanese and Thai, which have no spaces, the phrase itself. `python3 lang/build.py` turns the file into
`pi/extensions/lang-words.ts` (which the Pi extensions import) and the marked block at the top of
`services/search_gateway_engine.py` (the search gateway's engine). After editing `lang/phrases.json`, run it, then
`install/pi-config.sh` (the gateway uses the new rules with its next search); it refuses to install a stale copy
(`python3 lang/build.py --check`). The messages Pi's extensions send to the model stay in English, and the word lists are
in code only: nothing is added to the model's context.

`lang/tests.json` holds, for each language, sentences that must match each rule and ordinary sentences that must match
none. The guard checks (see "Testing Pi") run them against both copies and check each language's phrases against the
other languages' ordinary sentences, since accents are removed: Czech "padá" (crashes) would otherwise fire on
Malay "pada" (at), Romanian "ce" (what) on French "Ce matin". When you add or change phrases, add a sentence there
too; a language without a sentence for one of its rules fails the check.

### SearXNG after a network change

A Docker container keeps the DNS servers of the network it started on. After moving to another house, router or
Wi-Fi, SearXNG then fails every search with `HTTP connection error` (`DNSError` in its log) while the machine itself
is online. Two things prevent and repair that:

- `services/searxng/docker-compose.override.yml` gives the container fixed public DNS servers (1.1.1.1 Cloudflare,
  9.9.9.9 Quad9), so the current network's router does not matter. `install/searxng.sh` and `start/searxng.sh` copy
  it next to SearXNG's `docker-compose.yml`, where Docker Compose picks it up. The trade-off: SearXNG's lookups go to
  those providers, not your router. On a network that blocks outside DNS, delete the override from
  `~/LocalLLM/searxng` and from `services/searxng/`.
- `start/searxng.sh` checks the internet from inside the container. If the container is offline while this machine
  is online, it recreates the containers and checks again. If the machine itself is offline, it only warns.

`stop/searxng.sh` removes the containers (the data volumes stay), so the next start always builds fresh ones with
the current network settings. Because of that, a SearXNG stopped this way does not come back at boot: start it with
`start/searxng.sh` or `./start.sh`.

## Two-PC setup

The setup can run on one PC (**single mode**, everything above) or on two: the **main PC** (Linux) keeps llama-server,
Pi (with agent-browser) and SearXNG, and a **helper PC** (Windows, no Docker and no WSL; an NVIDIA GPU of about 6 GB,
Pascal or newer, and 16 GB of RAM) runs Open WebUI with the search gateway, Kokoro and the voice router, a Whisper
server, document search models and image generation (**split mode**). That frees about 3 GB of RAM on the main PC and
adds what it can't run next to the model. The two PCs and your other devices talk over Tailscale (a private network;
its free Personal plan is enough).

Run `./localllm.sh status` to see where everything is and whether it answers. Single mode stays the default (and is
what `install.sh` sets up); the helper is added with the steps below, and `./localllm.sh switch single` goes back at
any time.

**Addresses are never written into the repo:** Open WebUI's is `https://<the helper's Tailscale name>`, from
`HELPER_HOST` in `local.env` (set by `setup-helper`), so another helper PC simply has another address.
`./localllm.sh status` prints the current ones; the browser autoplay policy follows them (`install/browser-address.sh`, when `BROWSER_POLICY_DIR` is set).

**Where each service runs** comes from `config/services.json` and `LOCALLLM_MODE` in `local.env`; `lib/endpoints.py`
writes it into `~/.config/localllm/endpoints.json` (Pi's `web_search` reads it at each search) and `endpoints.env`
(the scripts), again whenever `local.env` changes. `./localllm.sh endpoints` rewrites them and prints the table.
In split mode:

| Service | Runs on | Address from the main PC |
|---------|---------|---------------------------|
| llama-server, SearXNG, Plannotator Inbox, Pi | main PC | `http://127.0.0.1:<port>` (the helper reaches llama-server and SearXNG by the main PC's Tailscale name) |
| Search gateway, Open WebUI | helper PC (always together: the gateway reads Open WebUI's database) | `http://<helper>:8090`; Open WebUI at `https://<helper>` (`tailscale serve`, a real certificate: browsers allow the microphone only on secure pages) |
| Kokoro and the voice router | helper PC (`PLACE_KOKORO=main` keeps them here) | `http://<helper>:8880`, `:8881` |
| Whisper, Docling, the small model, ComfyUI | helper PC only | used by Open WebUI there (the document search models run inside Open WebUI) |

llama-server keeps listening on 127.0.0.1 only. In split mode `./localllm.sh start` forwards its port (and SearXNG's)
to the Tailscale address with `tailscale serve --tcp` (tailnet only, nothing opened on the LAN), and llama-server asks
for an API key (`~/.config/localllm/llama-api-key`, made once, mode 600), which Pi sends too (`install/pi-models.sh`
writes it into `models.json`, then mode 600). There is no fallback: with the helper off, `web_search` and
`./localllm.sh` say so ("start it on the helper, or run ./localllm.sh switch single").

`./localllm.sh start|stop|restart|status` cover both PCs: start runs the main PC's services first (llama-server
first), then the helper's; stop the helper's first, llama-server last. `start|stop|restart|status helper` acts on the
helper only, `... main` on this PC only. If the helper can't be reached, this PC's part still runs and the helper is
reported as unreachable.

### Connecting the helper PC

The helper is reached by SSH with a key (no passwords), over Tailscale: the name `localllm-helper` in `~/.ssh/config`
points at the helper's Tailscale name (`HELPER_HOST`) and Windows user (`HELPER_USER`), with the key
`~/.ssh/localllm-helper`.

1. **By hand, on the helper PC:** x64 Windows 10/11 (not ARM, not S mode), Windows Update until nothing is left (and
   reachable so OpenSSH can install), the NVIDIA driver (version 560 or newer: the helper's PyTorch is a CUDA 12.6
   build), and Tailscale (https://tailscale.com/download/windows), signed in with the same account as the main PC. On a
   fresh Windows 11 install, **Smart App Control** may block the unsigned WinSW or `llama-server.exe` binaries (turn it
   off if Windows blocks them during `install-helper`). The Windows user you give `setup-helper` (step 4) must be an
   **administrator** account with **no spaces** in its name: Windows' SSH server accepts the main PC's key for
   administrators only (`administrators_authorized_keys`), so a standard user can't be reached.
2. **By hand, in the Tailscale admin console:**
   - **DNS** (https://login.tailscale.com/admin/dns): turn on **MagicDNS** and enable **HTTPS Certificates** (for Open
     WebUI's `https://` address).
   - **Machines** (https://login.tailscale.com/admin/machines): open the menu for both the main PC and the helper PC and
     choose **Disable key expiry** (otherwise keys expire after 180 days and the headless helper quietly drops off the
     tailnet).
   - On a tailnet where Tailscale Serve has never been used, the first `install-helper` (or `switch split`) may stop and
     print a one-time browser link to enable Serve for the tailnet: open it, approve, and run the command again. If you
     use custom Tailscale ACLs, allow main → helper on TCP `22, 443, 8090` and helper → main on TCP `8080, 8081`.
3. On the main PC: `install/tailscale.sh` (also part of `install.sh`; **by hand**: scan the QR code on your phone or open the sign-in link it prints on any device).
4. On the main PC: `./localllm.sh setup-helper`. It lists the Windows PCs in your tailnet and asks for the helper's
   name and its Windows user name (on the helper, `whoami` in PowerShell prints `<pc>\<user>`; give the part after
   the `\`), saves both in `local.env`, makes the SSH key and the `~/.ssh/config` block, and writes
   `~/.cache/localllm/bootstrap.local.ps1` (this PC's public key filled in; outside the repo, deleted once `setup-helper` connects).
5. **By hand, on the helper PC:** copy `bootstrap.local.ps1` there (it holds only a public key), open PowerShell as
   Administrator and run `powershell -ExecutionPolicy Bypass -File <path>\bootstrap.local.ps1`. It turns on
   Tailscale's "Run unattended" (the connection stays up with nobody signed in), installs Windows' OpenSSH server
   (keys only, PowerShell as its shell, the firewall letting SSH in only from Tailscale and the local network) and
   prints the user name, the Tailscale name and the SSH fingerprint.
6. On the main PC: `./localllm.sh setup-helper` again. **By hand:** answer `yes` when ssh shows the fingerprint
   the helper printed (only then). It ends with "OK: localllm-helper answers (PowerShell ...)".

`setup-helper` is safe to run again: after a rebuild of the main PC, the key, the fingerprint and `local.env` come back
from the backup, so it only writes the `~/.ssh/config` block and tests the connection.

### Installing the helper PC

Once `setup-helper` answers OK:

```bash
./localllm.sh install-helper   # everything on the helper, over SSH (an hour or more the first time: ~15 GB of downloads)
./localllm.sh switch split     # moves Open WebUI's data there and starts everything in its place
```

`install-helper` sends the repo's helper files and the helper's settings (the deploy, below), then runs
`windows\setup.ps1` there. Everything large goes into one folder on the helper, `HELPER_DATA_DIR` in
`~/.config/localllm/helper.env` on this PC (template `config/helper.example.env`): when it isn't set, install-helper
picks the helper's internal NTFS drive with the most free space and writes it there, so every later run, and a
rebuild from the backup, uses the same place. If that drive is ever missing (another disk, a changed letter), setup
stops and says so instead of starting over somewhere else. Small settings and the keys go into
`C:\ProgramData\LocalLLM` (Administrators and SYSTEM only); both are only copies of what this PC sends.

**Looking inside the helper's folders.** The data folder holds your chats and the API keys, so it is Administrators
and SYSTEM only too, and Explorer on the helper can't open it: Windows always runs Explorer as a normal user, even
started from an administrator window. Don't click its "Continue" button: that adds your Windows user to the folder's
permissions for good (the next `install-helper` takes it out again). Instead:

- From this PC (the easy way): SSH logs in to the helper as an administrator, so any file manager that speaks SFTP
  opens the folder. Type `sftp://localllm-helper/<HELPER_DATA_DIR>` in its address bar (Ctrl+L), with the folder
  written with forward slashes, for example `sftp://localllm-helper/D:/LocalLLM`. If the file manager doesn't know
  the `localllm-helper` name (it comes from `~/.ssh/config`), use the helper's Tailscale name.
- On the helper: an administrator PowerShell (`Get-ChildItem`, `Remove-Item`, `notepad <file>`), or a file manager
  started with "Run as administrator" (for example 7-Zip File Manager). Without one, Notepad's File → Open dialog,
  with the file type set to "All files", browses, renames and deletes with administrator rights.

What setup installs (pinned in `windows/versions.json`; the small model in `models/helper-small.json`), each in its own
Python environment made with uv, all sharing one PyTorch build with CUDA 12.6 (the newest that still supports GTX 10
cards):

| Part | What it is for | On the GPU |
|------|----------------|-----------|
| Open WebUI (the main PC's version, so the data can move) | chat; the document search model bge-m3 runs inside it | yes (CPU in gaming mode) |
| Whisper (`services/whisper_server.py`, faster-whisper with Open WebUI's Python) | dictation and voice mode: large-v3-turbo, loaded when the service starts, so even the first recording takes about 1 s | yes (CPU in gaming mode) |
| Search gateway | every web search of Pi and Open WebUI, its dashboard and settings page | no |
| Kokoro (Kokoro-FastAPI, no Docker) and the voice router | reading answers aloud | yes (`HELPER_KOKORO_DEVICE=cpu` to change) |
| Docling | reads PDFs and scans (with OCR) for Open WebUI's documents | yes |
| Small model (llama.cpp for Windows) | Open WebUI's chat titles, tags and follow-up questions, so they never disturb the main model's cache | no (CPU), stopped in gaming mode |
| ComfyUI with DreamShaper 8 (a Stable Diffusion 1.5 fine-tune, FP32) | images in Open WebUI | yes, in low-VRAM mode (about 2 minutes per 768×1152 image); stopped in gaming mode |

**Images in a chat:** the model doesn't draw on its own (asked without it, it says it has no image tool). Turn on
"Image" in the message box's menu (the "+" / integrations button next to it), then write what to draw: ComfyUI makes a
512×512 picture in about 35 s. `topology.py` sets which fields of Open WebUI's ComfyUI workflow get the prompt, the
model and the size (Admin Panel → Settings → Images).

The GPU's 6 GB (measured 2026-11-18): Open WebUI with bge-m3 about 2.25 GB (FP32: GTX 10 cards are slow in FP16),
Whisper 1.1 GB, Kokoro 1.4 GB, Docling 1 GB while it reads a document. A reranker (bge-reranker-v2-m3, another 2.2 GB) doesn't fit next to them, so there is none
(hybrid search stays on); the small model runs on the CPU. `HELPER_SMALL_MODEL`, `HELPER_IMAGES` and `HELPER_DOCLING`
(on/off in helper.env) leave a part out. Each runs as a
Windows service (WinSW, started by hand, not at boot) through `windows\run-service.ps1`, which reads the keys when it
starts, so none is written into a service definition. Setup also allows long paths, excludes the data folder from
Defender's scans, sets the Balanced power plan with no sleep on mains power and lid-close doing nothing (the plan you
had before is kept for gaming mode), and puts five shortcuts on the desktop. Only Tailscale reaches the helper's
services: Open WebUI at `https://<helper>` and the search gateway at `<helper>:8090` (`tailscale serve`); everything
else listens on 127.0.0.1 there.

Setup is safe to run again: each step checks its result (the program answers with the right version, the file is
there, the service is registered) and skips what's done. Its first step installs Microsoft's Visual C++ runtime
(2015-2022, x64; PyTorch and `llama-server.exe` need it) when Windows doesn't have it yet, the newest from Microsoft's
own link. `./localllm.sh install-helper base,tools` runs only some
steps (the list is at the top of `windows/setup.ps1`). When Windows needs a restart first, the main PC restarts the
helper and waits in stages (Tailscale, the SSH port, a login), each with a time limit; if it isn't back in about 10
minutes, look at its screen.

**On the helper itself** (works with the main PC off): the desktop shortcuts "Start AI services", "Stop AI services",
"AI services status", "Gaming mode on" and "Gaming mode off" (they ask for administrator rights), or in PowerShell
`windows\services.ps1 start|stop|restart|status [service]` in `C:\ProgramData\LocalLLM\repo`. `./localllm.sh
start|stop|status helper` runs the same script over SSH. Logs: `<data folder>\logs\localllm-<service>.out.log` and
`.err.log`.

**Keeping it up to date:** after changing anything the helper runs (its scripts, the search gateway, Open WebUI's
tools, a chat type, helper.env), run `./localllm.sh deploy`. It runs the guard checks first and stops if they fail,
sends only the files that changed (a tar stream over SSH, compared with the list of the last deploy), and applies them:
the search gateway or voice router restarted, Open WebUI's tools and chat types pushed. A changed setting
(helper.env, the keys) needs `./localllm.sh restart helper`; a new version in `windows/versions.json` needs
`install-helper`.

**Images in a chat:** turn on the image switch in the message box first (the "+" or integrations menu → Image), then
describe the picture; Open WebUI only draws when that switch is on (otherwise the model answers that it can't). The
helper's small model rewrites your words into a short English image prompt (tags, the subject first: Stable
Diffusion reads English and only about the first 75 words well), in whatever language you asked. `open-webui/topology.py`
sets the generation up the way DreamShaper 8's own examples are made: a 512×768 portrait in 30 steps, then the same
picture enlarged 1.5× and drawn again in 15 steps ("hires fix"; that second pass makes the fine detail), with the
DPM++ 2M Karras sampler, CFG 7 and a negative prompt that uses DreamShaper's negative embeddings (BadDream and
UnrealisticDream, pinned in `windows/versions.json` with their SHA-256). The result is 768×1152, in about 2 minutes.
This is Open WebUI's ComfyUI workflow: topology.py writes it over Open WebUI's default one or its own earlier version,
and leaves one you made yourself alone. The image size (Admin Panel → Settings → Images) can be changed there, for
example to 768×512 for landscapes; a switch sets it back. SD 1.5-sized models are what a 6 GB GTX 10 card runs at a
usable speed: they do single subjects well, and get two characters interacting, or characters they barely know, wrong
often. Larger models (SDXL, Flux) would take many minutes per image there.

### Switching between single and split mode

`./localllm.sh switch split` moves Open WebUI's data (its database, uploads and vector store) and the search gateway's
(its usage counts, cache and search settings) to the helper; `switch single` brings them back. Each move:

1. stops Open WebUI and the search gateway on both PCs;
2. makes a consistent copy (SQLite's backup) and counts what's in it (chats, users, files, Knowledge bases, models,
   uploads) with an integrity check;
3. copies it over SSH and checks it again on the other side: the same counts, integrity ok. If anything differs,
   nothing moves, and the old side's data is untouched;
4. keeps the other side's previous copy as `...-before-<date>`, puts the new one in place, and renames the old side's
   to `...-moved-<date>`, so it can't be used by mistake;
5. switches this PC's mode (llama-server restarts with or without its API key; Pi's `models.json` follows),
   starts everything where it belongs, and sets Open WebUI's settings for that PC (`open-webui/topology.py`: the chat
   model's address and key, the small model for titles, speech-to-text, Docling, the reranker (none), ComfyUI; `sync.py`
   switches document search between multilingual-e5-small and bge-m3 and indexes the Knowledge bases again).

Re-indexing on every switch is on purpose: the vectors only fit the model that made them, and bge-m3 (about 2 GB)
would crowd the main model on a single PC. A switch is rare (the helper away or broken), so the re-indexing is the
price: measured 2026-11-18 with about 12,800 pieces of text, a few minutes for `switch single` (e5-small) and about
20 minutes for `switch split` (bge-m3 on a 6 GB Pascal GPU). The switch waits for it to end; until then, document searches
can come back incomplete. `sync.py` also indexes the Knowledge bases' names and descriptions again (what Open WebUI's
"search knowledge bases" looks in), which Open WebUI's own re-indexing leaves on the old model.

It refuses to move when the two Open WebUI versions differ (an older one can't open a newer database): run
`./localllm.sh install-helper` first, which installs this PC's version there. In split mode this PC's own Open WebUI,
search gateway and Kokoro refuse to start (`start/*.sh`), so a stray start can't open an empty database here.

### Pages in split mode (dashboard, search settings, chat types)

The search gateway has three pages. In single mode all three are at `http://127.0.0.1:8090/...` on this PC. In split
mode the gateway runs on the helper, and each page works a little differently:

| Page | In split mode | Who can open it |
|---|---|---|
| Search usage dashboard | `http://<helper's Tailscale name>:8090/dashboard` (`./localllm.sh status` prints the link) | any device on your tailnet: it shows counts, never keys or search words |
| Search settings (API keys, order, limits) | `./localllm.sh settings`, then http://127.0.0.1:8090/settings | only this PC, through SSH |
| Chat types | `./localllm.sh chat-types`, then http://127.0.0.1:8090/chat-types | only this PC |

**Search settings.** The page holds the search API keys, so the gateway serves it only to its own PC: the client must
be on 127.0.0.1 and the address typed must be `127.0.0.1` or `localhost`, whatever the network. Through the helper's
name it answers 403, also from this PC. `./localllm.sh settings` opens an SSH tunnel that makes the helper's
127.0.0.1:8090 this PC's 127.0.0.1:8090, prints the link, and waits:

```bash
./localllm.sh settings      # leave it running while the page is open; Ctrl+C closes the tunnel
```

The settings you save there are the helper's (`search-gateway.json` in the helper's data folder, which moves with
the switch and is in the backup). The same works on the helper itself, at http://127.0.0.1:8090/settings in its
browser.

**Chat types.** The chat type files stay on this PC (`~/.config/localllm/chat-types/`, in the settings backup); Open
WebUI, where their presets and Knowledge bases live, runs on the helper. So the page runs here:

```bash
./localllm.sh chat-types    # serves the page on this PC; Ctrl+C ends it
```

It is the same page as in single mode (`services/chat_types_web.py`, served by `services/chat_types_server.py`
instead of the gateway): "Create" or "Save" writes the file here, then `install/chat-types.sh` sends it to the helper
(with any other changed helper files, as `./localllm.sh deploy` does) and builds the preset there; "Download
sources" and "Delete" also run on the helper. The list shows each chat type's preset and Knowledge base as the
helper's Open WebUI has them (read over SSH); when the helper can't be reached, the files still show, with a warning,
and saving waits until it can. On the command line, `install/chat-types.sh` does the same in split mode, so editing a
file by hand and running it (or `./localllm.sh deploy`) works too. The helper's own copy of the page (through its
name or the settings tunnel) only points here: a file saved on the helper would be overwritten by the next deploy.

Both commands use port 8090 on this PC, so run one at a time (each refuses while something else answers there, such
as a tunnel opened by hand with `ssh -L 8090:...`). In single mode they only print the link, since the gateway here
serves both pages.

### Gaming mode on the helper

`./localllm.sh gaming on|off` (or the helper's "Gaming mode on/off" shortcuts) frees the helper's 6 GB of GPU memory
for a game: `on` stops the small model and ComfyUI and restarts Open WebUI (the document search models), Whisper,
Kokoro and Docling on the CPU, so chats, document chats and voice keep working, slower; it also switches to the power
plan you had before setup. `off` puts everything back on the GPU, with the Balanced plan. Open WebUI and the search
gateway keep running throughout (a short restart). Each switch takes about 1.5 minutes. Measured on a 6 GB Pascal
laptop GPU (2026-11-18): GPU memory in use 0 MiB in gaming mode; a 5-second recording takes about 14 s to transcribe on
the CPU instead of 1.2 s (same text), and more with a game using the CPU.

**The GPU runs at its stock settings**: the setup changes no clocks or voltages. An undervolt (a GPU tuning tool
capping the clock at a lower voltage) was tried on a 6 GB Pascal laptop GPU and didn't help: such a laptop GPU spends most of
an image at its power limit, so the cap saves little, and a curve lowered below the cap made images about 50% slower
(181 s instead of 120 s). At stock it stayed at 66–68 °C through a two-pass image.

### Backups in split mode

`backup.sh` (and the scheduled backup) takes Open WebUI's and the search gateway's data from the helper: a live,
consistent snapshot there, fetched over SSH into the same places in `backup/` as in single mode, so a restore works the
same whichever mode the backup came from. If the helper can't be reached, the backup says so and `backup/` keeps the
copy from the last backup that reached it. The helper itself holds nothing else worth backing up: its programs come
back from `install-helper`, its settings from this PC. After a reinstall of the helper alone, `./localllm.sh
restore-helper-data` puts that data back on it (see "Rebuilding only the helper PC").

### Rebuilding both PCs

Everything comes back from the encrypted backup (which holds this repo too) and its passphrase. **The passphrase must be kept
outside both PCs** (a password manager, or on paper): without it no backup can be opened. In order:

1. **The main PC:** "Reinstalling from scratch" above, steps 1-11 (leave step 12, turning on the scheduled backup,
   until after step 4 below so its first test upload is already in split mode). A backup made in split mode comes back
   in single mode: `restore.sh` puts Open WebUI's data (fetched from the helper by the last backup) on this PC, sets
   `LOCALLLM_MODE=single`, and re-enables `owui` in `LOCALLLM_APPS` if the backup was taken in `pi`-only mode, so
   `./start.sh` runs everything here and nothing is lost if the helper is gone too.
   Open WebUI's settings in that data are still the helper's (the chat model's address, its Whisper, Docling,
   ComfyUI and bge-m3), and its uploads are listed under the helper's data folder: `restore.sh` points the uploads
   at this PC's folder, and the first `./start.sh` sets Open WebUI for this PC, as `switch single` does (the Knowledge
   bases are indexed again with multilingual-e5-small: a few minutes, once). `switch split` later sets the helper's
   settings again.
   `install/tailscale.sh` (part of `install.sh`) signs this PC in to Tailscale (**by hand**: the QR code or link it
   prints). In Tailscale's admin console (**Machines**, https://login.tailscale.com/admin/machines), remove the old
   main PC and choose **Disable key expiry** on the rebuilt one (otherwise its key expires after 180 days).
2. **The helper PC, if it was reinstalled too, by hand:** "Connecting the helper PC" step 1 (Windows Update, the
   NVIDIA driver, Tailscale signed in). `HELPER_HOST`, `HELPER_USER`, the SSH key and `helper.env` (with
   `HELPER_DATA_DIR`) come back from the backup, and `setup-helper` doesn't ask for them again, so check two things:
   - **Its Tailscale name and key expiry.** A fresh Windows gets a new random PC name, and Tailscale names the machine
     after it, so the restored `HELPER_HOST` no longer matches (removing the old machine from the tailnet doesn't
     change that). In Tailscale's admin console (Machines), remove the old helper PC, rename the new one to the old
     name (the part of `HELPER_HOST` before the first dot) — or set the new name in `HELPER_HOST` in `local.env`
     instead — and choose **Disable key expiry** on it too.
   - **Its Windows user.** If it isn't the one in `HELPER_USER`, set the new one there (an administrator account).
3. On the main PC: `./localllm.sh setup-helper`. With a reinstalled helper it can't connect yet: it writes
   `~/.cache/localllm/bootstrap.local.ps1` (with the restored key; outside the repo, deleted once `setup-helper` connects) and stops. **By
   hand:** copy that file to the helper and run it there ("Connecting the helper PC" step 5), then run
   `./localllm.sh setup-helper` again. The helper's SSH fingerprint is new while the restored `known_hosts` holds the
   old one: setup-helper says so and, only when you answer `yes`, forgets the old one and asks you to confirm the new
   one (compare it with what `bootstrap.ps1` printed).
4. `./localllm.sh install-helper` (only when the helper was reinstalled), then `./localllm.sh switch split`. When
   only the main PC was rebuilt, the helper still has its own data, newer than the backup's: `switch split` shows
   both (chats, users, Knowledge bases, the newest chat's date) and asks which to keep (`h`: the helper's, nothing
   is sent; `m`: this PC's, the helper's kept as `...-before-<date>`).
5. Turn on the scheduled backup ("Reinstalling from scratch" step 12): `rclone config` if you haven't yet (**by hand**:
   your storage's login), then `install/backup-timer.sh --enable` (one test upload, now in split mode, then the timer
   is on). Optionally enable the helper Tailscale keepalive timer: `install/helper-keepalive.sh --enable`.
6. `./localllm.sh status`: every service, where it runs and whether it answers, and the last backup's age.
7. **By hand, if the helper is a laptop:** its maker's battery charge limit (a laptop always on mains power lasts
   longer at 60-80 %).

What only a real rebuild proves (it can't be tested on a running setup): that a fresh install installs exactly the
versions in `versions.env`, that the restored Context7 sign-in works on a new system (a copy in a temporary HOME
does), that the restored `.git/info/exclude` replaces a fresh clone's, and that `install/backup-timer.sh --enable`
makes its test upload. A drill on a spare disk once, before relying on it, is worth it.

### Rebuilding only the helper PC

When Windows was reinstalled on the helper (or its disk replaced) and the main PC is fine, the main PC is still in
split mode, but Open WebUI's data was only on the helper: the newest copy is the last backup's, which `backup.sh`
keeps in `backup/` on this PC (the timer refreshes it every 6 hours). In order:

1. **By hand, on the helper:** "Connecting the helper PC" step 1 (Windows Update, the NVIDIA driver, Tailscale signed
   in), and its Tailscale name and Windows user as in "Rebuilding both PCs" step 2.
2. On the main PC: `./localllm.sh setup-helper`, then **by hand** `bootstrap.local.ps1` on the helper, then
   `./localllm.sh setup-helper` again ("Rebuilding both PCs" step 3).
3. `./localllm.sh install-helper`.
4. `./localllm.sh restore-helper-data`. It shows what `backup/` holds (chats, users, Knowledge bases, the newest
   chat's date, when it was saved) and what the helper has, asks before changing anything, then puts the backup's
   Open WebUI and search gateway data on this PC, with the uploads pointed here, and moves it to the helper exactly
   as `switch split` does: the same checks on both sides, the helper's settings, this PC's copy renamed
   `...-moved-<date>`. If the helper still has Open WebUI data, it says so, and the move asks again whether to
   replace it. If the move stops halfway, this PC is left in single mode with the data (`./start.sh` works here);
   run `./localllm.sh switch split` once the helper answers.
5. `./localllm.sh status`.

What was added on the helper since the last backup (at most 6 hours, while the timer runs) is lost with its disk. To
restore an older archive instead, unpack it into the repo first ("Opening a backup", "On a machine that has the repo";
it replaces `backup/`), then run step 4. Don't run `./restore.sh` for this: in split mode it would put the data on this
PC, where Open WebUI doesn't run (it says so and points here).

**Moving the helper's data folder** (a new disk): `./localllm.sh switch single` (the data comes back here), set
`HELPER_DATA_DIR` in helper.env to the new place, `./localllm.sh install-helper`, then `./localllm.sh switch split`.

### Keeping the helper Tailscale tunnel warm

If the helper PC sits idle, Tailscale's direct WireGuard path can pause and make the first SSH command time out. `helper_reachable()` in `lib/common.sh` automatically retries once, and `install/helper-keepalive.sh --enable` turns on a lightweight `systemd --user` timer (`localllm-helper-keepalive.timer`) that pings `HELPER_HOST` over Tailscale every 60 seconds.

## Manual steps

Everything the scripts can't do for you, in one list: each one is a login, a password, a driver, a choice, or a
Windows screen. The sections linked here say how.

**The main PC (Linux), first setup** ("Reinstalling from scratch"):
- Install Arch with a systemd desktop and sudo for your user, the NVIDIA driver, Docker and git (steps 1-4). Your
  `sudo` password whenever a script asks.
- Open the latest backup archive with its passphrase and clone the repo from the bundle in it (step 5). **Keep the passphrase outside both PCs** (a password
  manager, or on paper); put it in the passphrase file for new backups (step 11).
- Log out and back in once after `./install.sh` (step 7).
- Tailscale's sign-in link / QR code, which `install/tailscale.sh` prints (open on your phone or any device — no browser needed on this PC).
- Context7's sign-in in Pi (`/mcp-auth context7`), only if step 9's check says "not signed in".
- `rclone config` with your storage's login (step 12), if you didn't already do it to download the archive.

**The helper PC (Windows), first setup** ("Connecting the helper PC", "Installing the helper PC"):
- x64 Windows (not ARM, not S mode), Windows Update until nothing is left, the NVIDIA driver (560 or newer), Tailscale
  installed and signed in with the same account, and an administrator Windows user with no spaces in its name (turn off
  Smart App Control if a fresh Windows 11 blocks unsigned binaries during `install-helper`).
- In Tailscale's admin console: MagicDNS on, HTTPS certificates enabled, **Disable key expiry** on both PCs, and
  approve the one-time Tailscale Serve link if prompted on a fresh tailnet.
- On the main PC, `./localllm.sh setup-helper`'s two questions: the helper's Tailscale name and its Windows user
  name (`whoami` on the helper prints it).
- Carry `~/.cache/localllm/bootstrap.local.ps1` to the helper and run it in PowerShell as Administrator (confirm Windows'
  administrator prompt); then, on the main PC, answer `yes` to the SSH fingerprint only when it matches the one the
  helper printed.
- During `install-helper`: if the helper isn't back about 10 minutes after a restart, look at its screen.
- If the helper is a laptop: your laptop vendor's battery tool, its charge limit (60-80 %).

**After a rebuild:** the checklist in "Rebuilding both PCs" (the same steps as above, plus, after a Windows reinstall,
the helper's new Tailscale name and Windows user, and answering `switch split`'s question about whose Open WebUI data
to keep). After rebuilding only the helper: "Rebuilding only the helper PC" (the same helper steps, then answering
`restore-helper-data`'s question).

**Ongoing, when you want:**
- `./localllm.sh deploy` after changing helper files or `helper.env`; `./localllm.sh switch single|split`;
  `./localllm.sh start|stop helper`; `./localllm.sh gaming on` before playing on the helper and `off` after.
- New or changed search API keys on the search gateway's settings page (see "Web search in Pi").
- An Open WebUI account for each person (Admin Panel → Users), with a password they choose or change after the first
  sign-in, and their choice about Open WebUI's Memory (see "Protected chats in Open WebUI"). Each of their devices
  joins your tailnet (Tailscale's invite and sign-in) and allows the microphone for voice.
- Judging the reports of `tests/run-pi-tests.sh` from their traces (see "Testing Pi").

## Backing up and restoring settings

```bash
./backup.sh            # copies the live settings into backup/
./archive-backup.sh    # backup.sh, then an encrypted archive in your archive folder
./upload-backup.sh     # archive-backup.sh, then uploads it to your rclone remote
```

`backup/` holds secrets (the search services' API keys, Pi's `auth.json`, Open WebUI's secret key, chats, the encrypted
Context7 sign-in and its key), so it is
**not in git** (`.gitignore`), and only you can open it (mode 700). Open WebUI's signing key
(`backup/open-webui/.webui_secret_key`: whoever reads it can log in as any user) and its database
(`~/.open-webui/webui.db`, your chats) are mode 600 and `~/.open-webui` 700: `backup.sh`, `restore.sh` and
`services/open-webui.sh` (which also starts Open WebUI with `umask 077`, so files it makes later are yours only) set
it each time, and `tests/restorability-check.sh` reports a file others can read. It leaves the machine only as `LocalLLM-backup-<date>.tar.gz.gpg`, encrypted with a
passphrase (gpg, AES-256). The archives on your remote are the only history of your settings.

Where things go is set in `~/.config/localllm/local.env`, this machine's own settings file, which stays out of the
repo (`restore.sh`, run by `install.sh`, puts it back from the backup, mode 600; `config/local.example.env` lists every
setting):
- `BACKUP_ARCHIVE_DIR`: the folder for the encrypted archives;
- `BACKUP_REMOTE`: where `upload-backup.sh` uploads them, as an rclone `remote:folder`;
- `BACKUP_KEEP_COUNT`: how many archives to keep there and in the archive folder (the newest ones);
- `BACKUP_PASSPHRASE_FILE`: a file (mode 600) with the passphrase, so archives can be made unattended (the scheduled
  backup);
- `EXTRA_BACKUP_FILES`: files of your own to keep in the backup too (see the table below);
- `PI_MCP_ADAPTER_OAUTH_FILE_KEY`: the key of Pi's encrypted Context7 sign-in, added by `install/pi-mcp.sh` (see
  "Library docs in Pi");
- `LOCALLLM_MODE`, `HELPER_HOST`, `HELPER_USER` (and optionally `MAIN_HOST`, `PLACE_<SERVICE>`): the two-PC setup
  (see "Two-PC setup").

The backup also keeps the GitHub token (`~/.config/localllm/github-token`, see "Library docs in Pi") when there is one.
For the two-PC setup the backup also keeps, when they exist: llama-server's API key
(`~/.config/localllm/llama-api-key`), the helper PC's own values (`~/.config/localllm/helper.env`), the SSH key to the
helper (`~/.ssh/localllm-helper` and `.pub`) and the helper's fingerprint (its lines of `~/.ssh/known_hosts`), under
`backup/localllm/`. `restore.sh` puts them back (the secrets mode 600); `./localllm.sh setup-helper` then writes the
`Host localllm-helper` block in `~/.ssh/config` again and tests the connection.

The settings backup keeps `local.env`, never the passphrase file. **Keep the same passphrase in your password
manager**: a fresh install cannot open the archives without it. With no passphrase file
set, `archive-backup.sh` asks for the passphrase instead.

### Versions

Every backup records the versions that are running at that moment in `~/.config/localllm/versions.env`: Pi and its
packages, agent-browser, Open WebUI, the llama.cpp commit, and the SearXNG and Valkey images (Kokoro's image is fixed
in `services/kokoro/docker-compose.yml`). The file is part of the encrypted backup, not of the repo. On a fresh
install, `restore.sh` runs first, so each installer installs exactly those versions: the set that last worked
together. They're used only for what is installed new, never to change what's already here. `./install.sh --update`
ignores them and takes the newest; the next backup records the new set. If you configure a personal `BROWSER_INSTALLER` in `local.env`, `./install.sh --update` passes `--update` to it as well.

CUDA and the compiler always come from pacman at their newest, so the recorded llama.cpp commit may not build on a
fresh system. `install/llama.sh` then stops and says so: run `install/llama.sh --update` (the newest llama.cpp), then
`./install.sh` again.

### Opening a backup

Opening an archive needs only the archive and the passphrase; no settings, so it works on a fresh install. Arch
already has `gpg`, `tar` and `gzip`.

1. Get the archive `LocalLLM-backup-<date>.tar.gz.gpg`: download it from your storage's web page, or, after
   `rclone config`, with `rclone copy <remote:folder>/LocalLLM-backup-<date>.tar.gz.gpg .` (on a machine that still
   has its settings, the remote is `BACKUP_REMOTE` in `local.env`, and the newest archives are also in
   `BACKUP_ARCHIVE_DIR`). Your password manager is a good place to note where they are.
2. Decrypt and unpack it. gpg asks for the passphrase (take it from your password manager), and `backup/` appears.
   - **On a fresh install** (no repo yet): unpack it in an empty folder, clone the repo from the bundle in it (the
     last backup's commits, every branch, with `master` checked out), and move `backup/` into the clone.
     `backup/repo-path` holds the exact folder inside `$HOME` where the repo lived before, so `EXTRA_BACKUP_FILES`
     inside the repo land in the right place (don't clone into `~/LocalLLM`, which holds the models and SearXNG; with
     an older archive without `backup/repo-path`, check `ls backup/extra/` to see the path):
     ```bash
     mkdir ~/llm-restore && cd ~/llm-restore
     gpg -d /path/to/LocalLLM-backup-<date>.tar.gz.gpg | tar -xzf -
     REPO="$HOME/$(cat backup/repo-path)"
     git clone --branch master backup/repo.bundle "$REPO"
     cd "$REPO"
     git fetch origin '+refs/heads/*:refs/heads/*' --update-head-ok   # every branch, not only master
     git remote remove origin   # it pointed at the bundle; add your own remote here if you have one
     mv ~/llm-restore/backup . && rmdir ~/llm-restore
     ```
     Skipping the `git fetch` line loses every branch but `master` when origin is removed. The setup runs from
     `master`: work on another branch comes back as that branch, not in what gets installed.
   - **On a machine that has the repo** (to put settings back): in the repo folder,
     `gpg -d LocalLLM-backup-<date>.tar.gz.gpg | tar -xzf - -C .`
   To only look inside first: `gpg -d LocalLLM-backup-<date>.tar.gz.gpg | tar -tzf -`.
3. Put the settings in place: `./install.sh` on a fresh install (it runs `restore.sh --missing-only`), or
   `./stop.sh`, `./restore.sh` (it keeps a `.bak` of anything it replaces), `./start.sh` on a running setup.

Archives made before 2026-11-19 14:37 (commit `d0d3689`) have no `repo.bundle`: with one of those, clone the repo
from wherever else you keep it.
The bundle holds commits only: `backup.sh` warns when the repo has uncommitted changes or untracked files (they
wouldn't come back unless they are in `EXTRA_BACKUP_FILES`), and when the branch you are on isn't `master` (a fresh
install runs `master`, so merge what it should have). `tests/restorability-check.sh` clones the bundle as above and
checks that every branch comes back.

Backups are safe while Open WebUI runs: both of its databases are copied as SQLite snapshots. Only Chroma's index
folders (in `vector_db/`, once Knowledge collections exist) are plain files; `backup.sh` warns when it copies them
live.

### Automatic off-site backup

`install/backup-timer.sh` (part of `install.sh`) installs rclone and the `localllm-backup` user timer, which runs
`upload-backup.sh` every 6 hours (00:00, 06:00, 12:00, 18:00) and shows a desktop notification if it fails. Any
storage rclone supports works. The timer is not enabled until you have done these once:

```bash
rclone config        # create the remote that BACKUP_REMOTE in local.env names (your storage's login)
# the passphrase file named in ~/.config/localllm/local.env (reinstall step 11), mode 600
install/backup-timer.sh --enable   # one test upload (it should appear in BACKUP_REMOTE), then turns the timer on
```

rclone keeps its login in `~/.config/rclone/rclone.conf`, outside the repo and the backup (a login step after a
reinstall). If uploads start failing (the desktop notification says so), updating rclone (`sudo pacman -Syu rclone`)
or `rclone config reconnect <remote>:` usually fixes it. The archives keep their own gpg encryption, so a leaked
storage login still exposes nothing. Runs missed while the laptop was off or asleep are made up by a single run at
the next boot or wake, however many were missed. The timer only runs while you are logged in, unless you run
`loginctl enable-linger`. To change the times, edit `OnCalendar` in `services/localllm-backup.timer` and run
`install/backup-timer.sh` again.

Both the remote and the archive folder keep only the newest archives, as many as `BACKUP_KEEP_COUNT` in `local.env`
says (8 here: about 2 days of PC use); older ones are deleted after each new backup is made and uploaded. Age doesn't
matter: after days with the PC off, the last ones from before are still there.

```bash
systemctl --user list-timers localllm-backup.timer   # next run
journalctl --user -u localllm-backup                 # what the last runs did
rclone ls <remote:folder>                            # archives on the remote (BACKUP_REMOTE)
rclone copy <remote:folder>/<file> .                 # download one (e.g. on a fresh install, after rclone config)
```

| What | From | To |
|------|------|----|
| Pi settings, models, sandbox, auth, tool view, MCP onboarding, the encrypted Context7 sign-in | `~/.pi/agent` | `backup/pi/` |
| This machine's own settings: `local.env` (where the passphrase file, archives and remote are; the key of Pi's encrypted Context7 sign-in), `restorability-ignore`, and `versions.env` (see "Versions"); never the passphrase file itself | `~/.config/localllm` | `backup/localllm/` |
| Your own extra files, listed in `EXTRA_BACKUP_FILES` in `local.env` (anywhere in your home folder; on a fresh install a file there that holds only comments, like a new clone's `.git/info/exclude`, is replaced by the backed-up one) | their place in your home folder | `backup/extra/` |
| Open WebUI database (users, chats, admin settings, tools and functions with their Valves), uploads, vector DB | `~/.open-webui` | `backup/open-webui/` |
| The search gateway's settings: the search services' API keys, order and limits (`search-gateway.json`) | `~/.config/localllm` | `backup/localllm/` |
| The search gateway's database: usage counts shared by Pi and Open WebUI, result cache, the search log the dashboard shows (a consistent snapshot) | `~/.local/share/localllm/search-gateway.db` | `backup/search-gateway/` |
| The chat types of the protected chats (`chat-types/*.json`) | `~/.config/localllm/chat-types` | `backup/localllm/chat-types/` |
| Open WebUI secret key | written by Open WebUI directly into `backup/open-webui/.webui_secret_key` | saved where it is |
| SearXNG `settings.yml`, `.env`, `docker-compose.yml` | `~/LocalLLM/searxng` | `backup/searxng/` |
| Plannotator's settings (`config.json`) | `~/.plannotator` | `backup/plannotator/` |
| This repo: every branch and commit, as a git bundle (not uncommitted changes), plus its folder path (`repo-path`); a fresh install clones it (see "Opening a backup") | the repo folder | `backup/repo.bundle`, `backup/repo-path` |

Not backed up, on purpose: the backup passphrase file (README step 11), rclone's login (step 12), Pi's session
history, the Plannotator Inbox's threads, the test reports in `~/LocalLLM/test-runs`, your browser's own profile
(bookmarks, history), the model file and its vision projector (`install/llama.sh`
downloads them again), llama.cpp (built again), the web search cache (it refills by itself), the Whisper model (Open
WebUI downloads it again; on the helper PC, setup), the Kokoro, SearXNG and Valkey images (downloaded again, at the recorded versions) and
SearXNG's cache volumes.

`./restore.sh` copies everything back and overwrites the live settings. Whatever it replaces is first kept next to
the original as `<name>.bak.<timestamp>`. Stop Open WebUI first (`./stop.sh`), and restart SearXNG afterwards
(`docker compose restart` in `~/LocalLLM/searxng`) if its settings changed.

## System monitor

A self-contained local web page (`tools/monitor/monitor.py`, `tools/monitor/monitor.html`) shows live CPU, RAM, and GPU
usage for both the main PC (Linux) and the helper PC (Windows, queried over SSH in the background), along with the
active setup mode (`single` or `split`) and apps mode (`pi` or `pi,owui`):

```bash
./localllm.sh monitor                  # opens on http://127.0.0.1:8095/ (Ctrl+C stops it)
./localllm.sh monitor 8096             # custom port
./localllm.sh monitor --detect-hw      # one-time sudo dmidecode to cache main PC RAM brand/model
./tools/monitor/monitor.sh --once      # print one JSON snapshot and exit
```

Each component is drawn as a filled pie chart (cool steel-teal = full capacity/memory, warm terracotta = occupied) with the dynamically
detected commercial device name and full capacity above the pie (and normal / performant max GHz for the CPU), and the
occupied numeric value below it (`%` for CPU and GPU load, `MB`/`GB` for RAM and GPU VRAM). Nothing is hardcoded:
hardware names and capacities are read live from the OS (`/proc/stat`, `/proc/meminfo`, `cpufreq`, `nvidia-smi` on
Linux; CIM/WMI and `nvidia-smi` on Windows). On Linux, BIOS RAM module names require root (`dmidecode -t memory`), so
`--detect-hw` caches them once in `~/.config/localllm/hardware.json` (and automatically ignores the cache if installed
RAM capacity changes).

## Tuning llama-server

`services/llm-server.sh` starts `llama-server` with the profile's model under the alias `local`, port 8081 (8080 is
SearXNG), and `-c` = `CTX_PER_SLOT` × `SLOTS` with `--parallel SLOTS` (with the default profile, two 64K slots). The
flags are built by `llm_server_args` in `lib/model.sh`.

The values that depend on this PC's hardware are set in `~/.config/localllm/local.env` (examples in
`config/local.example.env`), so the scripts stay the same on any PC and the settings backup keeps them:

- `LLAMA_N_CPU_MOE` (default: the profile's `N_CPU_MOE`, 33 of 40 for the default profile, which fits an 8 GB GPU):
  the number of layers whose experts stay on the CPU. Lower it while `nvidia-smi` shows about 0.8 GB or more of VRAM free, and raise it if loading fails with out of
  memory.
- `LLAMA_THREADS` (default: the number of physical CPU cores).
- `LLAMA_CUDA_ARCH` (default: read from the GPU): the GPU generation `install/llama.sh` builds llama.cpp for (89 for
  RTX 40, 86 for RTX 30...). When it changes, the next `install/llama.sh` builds llama.cpp again.

To tune RAM, two more settings in `local.env`:

- `LLAMA_CACHE_RAM_MIB` (default: the profile's `CACHE_RAM_MIB`, 2048): RAM for the prompt cache. Each of the two slots (Pi, Open WebUI) keeps its current
  conversation in VRAM either way. The cache holds conversations that were pushed out of a slot, so going back to one
  doesn't re-read it from the start. With less cache, the first answer after returning to an older chat or Pi session
  can take longer (up to a minute for a long session). Raise it to 3072–4096 if that happens often, or after adding RAM.
- `LLAMA_VISION` (`on` or `off`, default `on`): `off` leaves out the vision projector, which frees ~0.9 GB, and image input stops working.

After changing either one, run `stop/llama.sh && start/llama.sh`.

To keep the GPU cooler during long runs (tests, long sessions), two GPU limits in `local.env`, set by
`services/gpu-limits.sh` as root each time llama-server starts (`ExecStartPre=+` in `localllm-llm.service`; it never
blocks the start, and unset puts the GPU's defaults back):

- `GPU_MAX_CLOCK_MHZ`: a cap on the GPU's clock, which laptop GPUs allow. Text generation is limited mostly by memory
  speed, so about 70% of the top clock (`nvidia-smi -q -d CLOCK`, "Max Clocks") costs little speed and a lot of heat.
- `GPU_POWER_LIMIT_W`: a power limit in watts. Most laptop GPUs refuse it (the driver manages their power, and
  `nvidia-smi` shows the limit as N/A); the script says so.

The first time, `install/llama.sh` installs the service with this step; after that, `stop/llama.sh && start/llama.sh`
applies a change. `services/gpu-limits.sh "$USER" --dry-run` shows what it would do. Check with
`nvidia-smi --query-gpu=clocks.sm,temperature.gpu,power.draw --format=csv`.

The model files and everything else about the model come from its profile (see "Model").

The projector runs on the CPU (`--no-mmproj-offload`), because the model already fills the GPU. To run it on the GPU,
which encodes images faster, remove that flag in `lib/model.sh` and raise `LLAMA_N_CPU_MOE` by about 2.

One of the Pi extensions, `thinking-switch`, pins Pi's requests to slot 0 and switches reasoning off for quick
questions the way the profile's `REASONING` says (the chat template's switch, the lowest reasoning effort, or nothing
for a model without reasoning). It only touches requests for `local`.

## Testing Pi

After changing Pi's extensions, `pi/AGENTS.md`, the search setup or the services, run the tests in `tests/` (the full
list, what each one expects and the checks done by hand: `tests/README.md`):

```bash
node --experimental-strip-types tests/guard-tests.mjs   # the extensions' and word rules, no model: seconds, no credits
tests/run-pi-tests.sh                                   # real questions to the model (about 40 tests, 40-80 minutes)
tests/run-pi-tests.sh E I1 H3                           # some tests, or whole groups by letter
tests/system-checks.sh --helper                         # the running setup on both PCs, read-only, no model (about a minute)
tests/restorability-check.sh                            # is everything here brought back by the repo? no model
tests/context7-check.sh                                 # a real Context7 docs lookup through Pi (about a minute)
shellcheck -x $(git ls-files '*.sh')                    # the scripts' common mistakes (settings: .shellcheckrc)
```

**The name check** (part of `guard-tests.mjs`): no tracked file and no commit message may contain a word that
identifies this PC or its people. The words come from this PC only, never from the repo: `local.env`'s values (where
the backups go, the helper's names, `EXTRA_PACKAGES`, the fan commands) and its `NAME_CHECK_WORDS` (brands and models
of your PCs, your storage provider, names; see `config/local.example.env`), plus the user, host, Tailscale and model
names it reads itself. A match is reported by file, line and where the word came from, never the word. Without a
`local.env` (a fresh clone) it checks only the names it reads itself.

The model tests cover search filters, page reading, links and PDFs, search limits, the reasoning switch, fallbacks,
library docs, editing and plans, the todo list, subagents (no more at once than `SUBAGENTS_AT_ONCE`) and the sandbox (deleting `~/.bashrc`,
with a stand-in HOME), and a few of them asked in Spanish, Chinese and Arabic. Each test writes a report to `~/LocalLLM/test-runs/<date-time>/` with every tool call
the model made and its answer. Judge each test from that trace, not from the answer alone: a right answer reached the wrong
way is a failure. Web tests use real search credits. `--with-f1` stops the search gateway for one test (and
starts it again), and `--with-j1` needs the Inbox running.

## Layout

| Path | What is in it |
|------|---------------|
| `install.sh`, `localllm.sh`, `start.sh`, `stop.sh`, `backup.sh`, `archive-backup.sh`, `upload-backup.sh`, `restore.sh` | The commands you run (`start.sh` and `stop.sh` run `localllm.sh start` and `stop`) |
| `start/`, `stop/` | One start and one stop script per tool, called by `localllm.sh` |
| `windows/` | The helper PC's scripts (see "Two-PC setup"): `bootstrap.ps1`, the one you run there by hand (written to `~/.cache/localllm/bootstrap.local.ps1` with this PC's public key filled in by `./localllm.sh setup-helper`, and deleted once it connects); `setup.ps1` (the install, run by `install-helper`), `services.ps1` (start, stop, status; the desktop shortcuts), `gaming.ps1`, `tasks.ps1` (the jobs `localllm.sh` asks for: Open WebUI's settings, the data snapshot and move), `run-service.ps1` (what each Windows service runs), `common.ps1`, and `versions.json` (what setup installs, pinned) |
| `open-webui/` | Open WebUI's `tools/` and `functions/` (one file each, named after its id), and `sync.py`, which `install/open-webui-config.sh` runs to push them. Tool `search_gateway.py`: "Search Gateway" (see "Web search in Pi"). Function `answer_timer.py`: "Answer Timer", a filter that adds how long each answer took as a small line at the end of the reply and removes it again before the chat goes back to the model, so it uses no context. Function `old_pages.py`: "Old Pages", the filter that leaves earlier answers' pages out of long chats (see "Long research chats in Open WebUI"). Tool `protected_lookup.py` and function `protected_chat.py`, `chat_types.py` (run by `install/chat-types.sh`) and `chat-types/example.json`: the protected chats (see "Protected chats in Open WebUI"). `topology.py`: the settings that depend on which PC Open WebUI runs on (see "Two-PC setup") |
| `install/` | One installer per tool, called by `install.sh` |
| `services/` | The launch scripts and systemd units of llama-server, Open WebUI, the search gateway (`search_gateway.py` with its engine `search_gateway_engine.py`, pinned Python packages `search-gateway-requirements.txt`, the move out of Open WebUI `search_gateway_migrate.py`, its dashboard and settings pages, and the chat types page, `chat_types_web.py` and `chat_types_page.html`, and `chat_types_server.py`, which serves it on the main PC in split mode) and the voice router, the helper's Whisper service (`whisper_server.py`), the backup timer, Kokoro's `docker-compose.yml` (`kokoro/`), and SearXNG's DNS override |
| `pi/` | What gets copied into `~/.pi/agent`: extensions, `AGENTS.md`, `plannotator.json` (plan mode's instructions), `mcp-adapter.json` (the docs servers), skills, `agents/` and `prompts/` (subagents and their workflows), `sandbox.json` (added to pi-sandbox's rules). `upstream-reports.md` holds notes on issues to report upstream |
| `backup/` | The settings backup: `pi/`, `plannotator/`, `open-webui/`, `searxng/`, `localllm/`, `extra/`, and `repo.bundle` (this repo itself). Not in git: it leaves the machine as an encrypted archive |
| `config/` | `helper.example.env`: the template of `~/.config/localllm/helper.env` (the helper PC's own values). `services.json`: every service's port, health address and where it runs in each mode (see "Two-PC setup"). `local.example.env`: the template for this machine's own settings, `~/.config/localllm/local.env` (backup passphrase file, archive folder, rclone remote, how many archives to keep, extra backup files, the Context7 sign-in key, the model profile, llama-server's GPU, CPU and RAM settings, extra packages, the fan profile commands, the name check's own words), which stay out of the repo |
| `lang/` | `phrases.json`: the word rules in 35 languages, `tests.json`: their test sentences; `build.py` generates Pi's `extensions/lang-words.ts` and the Open WebUI tool's word block from it (see "Languages") |
| `lib/common.sh` | Paths and helpers shared by the scripts |
| `lib/helper.sh`, `lib/helper_config.py` | The main PC's side of the helper: the deploy, `install-helper`, `switch`; the helper's `config.json` made from `helper.env`, the endpoints and the pins |
| `lib/endpoints.py` | Turns `config/services.json` and `local.env`'s mode into `~/.config/localllm/endpoints.json` (read by Pi's `web_search`) and `endpoints.env` (read by `lib/common.sh`): where each service runs and its address |
| `lib/model.sh` | Reads the active model profile: its values, llama-server's flags, `model.json` (see "Model") |
| `models/` | The model profiles (`<name>.env`) and `default`, the profile used when `MODEL_PROFILE` isn't set; `helper-small.json`, the helper PC's small model |
| `tests/` | The tests: `guard-tests.mjs` (extension rules and model profiles, no model; runs `lang-check.py`: the search gateway's word rules and collisions between languages, `dashboard-check.py`: the usage dashboard and search log, `search-gateway-check.py` (with the gateway's own Python): searches side by side, the settings page, Open WebUI's thin client and the move out of Open WebUI, and `protected-chat-check.py`: the protected chats' filter, lookup and chat type files, and `old-pages-check.py`: the Old Pages filter, and `two-pc-check.sh`: the two-PC setup's addresses per mode, `localllm.sh`, `setup-helper`'s edits and split mode's API key; and the name check, see "Testing Pi"), `run-pi-tests.sh` with `pi-tests.json` (real runs), `trace.py` (session → report), `README.md` (every test and check, and the ones done by hand); `system-checks.sh` (the running setup on this PC and, with `--helper`, the helper PC; read-only); `restorability-check.sh` (is everything on this machine brought back by the repo, see "Reinstalling from scratch"); `context7-check.sh` (a real Context7 docs lookup through Pi) |
| `tools/ocr/` | `ocr-pdfs.sh`: a text layer for scanned PDFs before uploading them (see "Protected chats in Open WebUI") |
| `tools/fans/` | `balanced.sh`, `performance.sh`: switch the laptop's fan profile (the commands come from your local config: `FAN_PROFILE_BALANCED` and `FAN_PROFILE_PERFORMANCE` in `local.env`, your laptop vendor's fan tool, whose package goes in `EXTRA_PACKAGES`) |
| `tools/monitor/` | `monitor.sh`, `monitor.py`, `monitor.html`: local web monitor for CPU, GPU and RAM on the main and helper PCs, plus `single`/`split` and `pi`/`pi,owui` mode (`./localllm.sh monitor`; see "System monitor") |
