#!/usr/bin/env python3
"""
LocalLLM System Monitor — web dashboard for CPU, GPU, and RAM usage on the main
and helper PCs, plus the current setup mode (single/split) and apps (pi/pi,owui).

Usage:
    python3 tools/monitor/monitor.py [--host 127.0.0.1] [--port 8095] [--interval 5]
    python3 tools/monitor/monitor.py --once        # print one JSON snapshot and exit
    python3 tools/monitor/monitor.py --detect-hw   # detect main PC RAM brand/model via sudo dmidecode and cache it
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
HTML_FILE = HERE / "monitor.html"
CSS_FILE = HERE / "monitor.css"
JS_FILE = HERE / "monitor.js"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8095
DEFAULT_INTERVAL = 5.0
HELPER_SSH_ALIAS = "localllm-helper"
MAX_PROCESSES = 30

# Tiny launcher sent on the SSH command line; the full PowerShell script is piped via stdin
# so it never hits Windows's 8191-character command-line limit.
HELPER_PS_LAUNCHER = (
    'powershell -NoProfile -Command '
    '"[Console]::InputEncoding=[Text.Encoding]::UTF8;'
    '[Console]::OutputEncoding=[Text.Encoding]::UTF8;'
    'Invoke-Expression ([Console]::In.ReadToEnd())"'
)

# PowerShell script piped over stdin to the Windows helper PC.
# Uses locale-independent CIM/WMI classes and nvidia-smi so it works on any Windows language.
HELPER_PS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'

$cpus = @(Get-CimInstance Win32_Processor)
$cpuThreads = ($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
$cpuName = if ($cpus.Count -gt 0 -and $cpus[0].Name) { ($cpus[0].Name -replace '\s+', ' ').Trim() } else { '' }
$cpuMaxMhz = ($cpus | Where-Object { $_.MaxClockSpeed } | Measure-Object -Property MaxClockSpeed -Maximum).Maximum
$cpuCurMhz = ($cpus | Where-Object { $_.CurrentClockSpeed } | Measure-Object -Property CurrentClockSpeed -Maximum).Maximum

# Locale-independent live CPU usage: Win32_PerfFormattedData_PerfOS_Processor is much more reliable than LoadPercentage.
$perfTotal = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
$cpuPct = $null
if ($perfTotal -and $null -ne $perfTotal.PercentProcessorTime) {
    $cpuPct = [math]::Round([double]$perfTotal.PercentProcessorTime, 1)
} else {
    $loads = @($cpus | Where-Object { $null -ne $_.LoadPercentage } | ForEach-Object { [double]$_.LoadPercentage })
    if ($loads.Count -gt 0) {
        $cpuPct = [math]::Round(($loads | Measure-Object -Average).Average, 1)
    }
}

$os = Get-CimInstance Win32_OperatingSystem
$osCaption = if ($os -and $os.Caption) { ($os.Caption -replace '^Microsoft\s+', '').Trim() } else { 'Windows' }
$ramTotalKib = if ($os) { [double]$os.TotalVisibleMemorySize } else { 0 }
$ramFreeKib = if ($os) { [double]$os.FreePhysicalMemory } else { 0 }
$ramUsedKib = [math]::Max(0, $ramTotalKib - $ramFreeKib)

$cs = Get-CimInstance Win32_ComputerSystem
$brand = if ($cs -and $cs.Manufacturer) { $cs.Manufacturer.Trim() } else { '' }
if (-not $brand -or $brand -match '^(To Be Filled|Default string|System manufacturer|Unknown|None)$') {
    $bb = Get-CimInstance Win32_BaseBoard
    if ($bb -and $bb.Manufacturer) { $brand = $bb.Manufacturer.Trim() }
}

$dimms = @(Get-CimInstance Win32_PhysicalMemory)
$ramInstalledBytes = ($dimms | Measure-Object -Property Capacity -Sum).Sum
$badDmi = '^(Unknown|Undefined|To Be Filled|Not Specified|0+|None)$'
$ramParts = @()
foreach ($d in $dimms) {
    $m = if ($d.Manufacturer) { $d.Manufacturer.Trim() } else { '' }
    $p = if ($d.PartNumber) { $d.PartNumber.Trim() } else { '' }
    if ($m -match $badDmi) { $m = '' }
    if ($p -match $badDmi) { $p = '' }
    $label = (@($m, $p) | Where-Object { $_ }) -join ' '
    if ($label) { $ramParts += $label }
}
$ramName = ($ramParts | Select-Object -Unique) -join ' / '
$ramSpeed = ($dimms | Where-Object { $_.ConfiguredClockSpeed } | Measure-Object -Property ConfiguredClockSpeed -Maximum).Maximum
if (-not $ramSpeed) { $ramSpeed = ($dimms | Where-Object { $_.Speed } | Measure-Object -Property Speed -Maximum).Maximum }

# Process list (PID -> Name, CommandLine, WorkingSetSize) + per-process CPU %
$procMap = @{}
$ramProcs = @()
foreach ($pr in @(Get-CimInstance Win32_Process)) {
    $pidVal = [int]$pr.ProcessId
    if ($pidVal -le 0) { continue }
    $cmdVal = if ($pr.CommandLine) { [string]$pr.CommandLine } else { '' }
    $nameVal = if ($pr.Name) { [string]$pr.Name } else { '' }
    $rssMib = if ($pr.WorkingSetSize) { [math]::Round([double]$pr.WorkingSetSize / 1MB, 1) } else { 0.0 }
    $procMap[$pidVal] = @{ name = $nameVal; cmd = $cmdVal; mem_mib = $rssMib }
    if ($rssMib -ge 1.0) {
        $ramProcs += [pscustomobject]@{ pid = $pidVal; raw_name = $nameVal; cmd = $cmdVal; mem_mib = $rssMib }
    }
}
$ramProcs = @($ramProcs | Sort-Object -Property mem_mib -Descending | Select-Object -First 30)

$cpuDiv = if ($cpuThreads -and $cpuThreads -gt 0) { [double]$cpuThreads } else { 1.0 }
$cpuProcs = @()
foreach ($pp in @(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process)) {
    $pidVal = [int]$pp.IDProcess
    if ($pidVal -le 0) { continue }
    $rawPct = [double]$pp.PercentProcessorTime
    if ($rawPct -le 0) { continue }
    $normPct = [math]::Round($rawPct / $cpuDiv, 1)
    if ($normPct -le 0) { continue }
    $info = $procMap[$pidVal]
    $cpuProcs += [pscustomobject]@{
        pid = $pidVal
        raw_name = if ($info) { $info.name } else { [string]$pp.Name }
        cmd = if ($info) { $info.cmd } else { '' }
        percent = $normPct
    }
}
$cpuProcs = @($cpuProcs | Sort-Object -Property percent -Descending | Select-Object -First 30)

$gpus = @()
$gpuProcs = @()
$smi = (Get-Command nvidia-smi -ErrorAction SilentlyContinue).Source
if (-not $smi -and (Test-Path 'C:\Windows\System32\nvidia-smi.exe')) {
    $smi = 'C:\Windows\System32\nvidia-smi.exe'
}
if ($smi) {
    $lines = & $smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>$null
    foreach ($ln in $lines) {
        if ($ln -and $ln.Trim()) {
            $p = @($ln.Split(',') | ForEach-Object { $_.Trim() })
            if ($p.Count -ge 4) {
                $gpus += [pscustomobject]@{
                    name = $p[0]
                    util_percent = $p[1]
                    mem_used_mib = $p[2]
                    mem_total_mib = $p[3]
                    temp_c = if ($p.Count -ge 5) { $p[4] } else { $null }
                }
            }
        }
    }
    $appLines = & $smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>$null
    foreach ($ln in $appLines) {
        if ($ln -and $ln.Trim()) {
            $p = @($ln.Split(',') | ForEach-Object { $_.Trim() })
            if ($p.Count -ge 3 -and $p[0] -match '^\d+$') {
                $pidVal = [int]$p[0]
                $info = $procMap[$pidVal]
                $gpuProcs += [pscustomobject]@{
                    pid = $pidVal
                    raw_name = if ($info -and $info.name) { $info.name } else { [IO.Path]::GetFileName($p[1]) }
                    cmd = if ($info) { $info.cmd } else { $p[1] }
                    mem_mib = $p[2]
                }
            }
        }
    }
}

@{
    hostname = $env:COMPUTERNAME
    brand = $brand
    os = $osCaption
    os_id = 'windows'
    cpu = @{
        name = $cpuName
        threads = $cpuThreads
        percent = $cpuPct
        normal_ghz = if ($cpuMaxMhz) { [math]::Round($cpuMaxMhz / 1000.0, 2) } else { $null }
        performant_ghz = if ($cpuCurMhz -and $cpuCurMhz -gt $cpuMaxMhz) { [math]::Round($cpuCurMhz / 1000.0, 2) } elseif ($cpuMaxMhz) { [math]::Round($cpuMaxMhz / 1000.0, 2) } else { $null }
    }
    ram = @{
        name = $ramName
        speed_mhz = $ramSpeed
        installed_mib = if ($ramInstalledBytes) { [math]::Round($ramInstalledBytes / 1MB, 1) } else { $null }
        used_mib = [math]::Round($ramUsedKib / 1024.0, 1)
        total_mib = [math]::Round($ramTotalKib / 1024.0, 1)
    }
    gpus = @($gpus)
    processes = @{
        cpu = @($cpuProcs)
        ram = @($ramProcs)
        gpu = @($gpuProcs)
    }
    gaming = [bool](Test-Path 'C:\ProgramData\LocalLLM\state\gaming.on')
} | ConvertTo-Json -Depth 5 -Compress
""".strip()


def _to_float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(round(f)) if f is not None else None


def _clamp_pct(value) -> float | None:
    f = _to_float(value)
    if f is None:
        return None
    return round(max(0.0, min(100.0, f)), 1)


_DMI_PLACEHOLDERS = {
    "",
    "unknown",
    "undefined",
    "none",
    "not specified",
    "to be filled by o.e.m.",
    "default string",
    "system manufacturer",
    "no module installed",
    "[empty]",
}


def _clean_dmi_field(val: str) -> str:
    s = re.sub(r"\s+", " ", (val or "").strip())
    if s.lower() in _DMI_PLACEHOLDERS or re.fullmatch(r"0+", s):
        return ""
    return s


def friendly_process_name(raw_name: str, cmdline: str = "") -> str:
    """Turn a raw process name + command line into a user-friendly program/model label."""
    raw = (raw_name or "").strip()
    cmd = (cmdline or "").strip()
    low_raw = raw.lower()
    low_cmd = cmd.lower()

    # llama-server: extract the GGUF model filename if present (-m / --model)
    if "llama-server" in low_raw or "llama-server" in low_cmd:
        m = re.search(r"(?:-m|--model)\s+['\"]?([^'\"\s]+)", cmd)
        if m:
            model_file = Path(m.group(1).replace("\\", "/")).name
            model_stem = re.sub(r"\.gguf$", "", model_file, flags=re.IGNORECASE)
            return f"llama-server ({model_stem})"
        return "llama-server"

    # LocalLLM stack services
    if "open-webui" in low_cmd or "open_webui" in low_cmd:
        return "Open WebUI"
    if "whisper_server.py" in low_cmd:
        return "Whisper STT"
    if "kokoro" in low_cmd or "api.src.main:app" in low_cmd:
        return "Kokoro TTS"
    if "voice_router.py" in low_cmd:
        return "Voice Router"
    if "search_gateway.py" in low_cmd:
        return "Search Gateway"
    if "docling-serve" in low_cmd or "docling_serve" in low_cmd:
        return "Docling"
    if "comfyui" in low_cmd or ("main.py" in low_cmd and "--listen" in low_cmd):
        return "ComfyUI"
    if "plannotator" in low_cmd or "plannotator" in low_raw:
        return "Plannotator Inbox"
    if "searxng" in low_cmd or "searx" in low_cmd:
        return "SearXNG"
    if "monitor.py" in low_cmd:
        return "LocalLLM Monitor"
    if "chat_types_server.py" in low_cmd:
        return "Chat Types Server"

    base = re.sub(r"\.exe$", "", raw, flags=re.IGNORECASE).strip()
    low_base = base.lower()

    # Well-known desktop/system apps
    known = {
        "brave": "Brave Browser",
        "chromium": "Chromium",
        "chrome": "Google Chrome",
        "firefox": "Firefox",
        "msedge": "Microsoft Edge",
        "code": "VS Code",
        "code-oss": "VS Code",
        "dockerd": "Docker Engine",
        "containerd": "Docker containerd",
        "docker": "Docker",
        "tailscaled": "Tailscale",
        "tailscale": "Tailscale",
        "systemd": "systemd",
        "plasmashell": "KDE Plasma",
        "kwin_wayland": "KDE KWin",
        "gnome-shell": "GNOME Shell",
        "xorg": "Xorg",
        "pipewire": "PipeWire Audio",
        "wireplumber": "WirePlumber",
        "explorer": "Windows Explorer",
        "dwm": "Desktop Window Manager",
        "svchost": "Windows Service Host",
        "msmpeng": "Windows Defender",
        "searchindexer": "Windows Search",
        "sshd": "OpenSSH Server",
        "powershell": "PowerShell",
        "pwsh": "PowerShell",
    }
    if low_base in known:
        return known[low_base]

    # Python / Node scripts: show script filename when helpful
    if low_base in ("python", "python3") or re.fullmatch(r"python3\.\d+", low_base):
        m = re.search(r"\b([A-Za-z0-9_.-]+\.py)\b", cmd)
        if m:
            return f"Python ({m.group(1)})"
        m_mod = re.search(r"-m\s+([A-Za-z0-9_.-]+)", cmd)
        if m_mod:
            return f"Python ({m_mod.group(1)})"
        return "Python"

    if low_base == "node":
        if re.search(r"\bpi\b|@mariozechner/pi-coding-agent", low_cmd):
            return "Pi Coding Agent"
        return "Node.js"

    return base or "Unknown"


def _parse_local_env(path: Path) -> dict[str, str]:
    """Read selected KEY=VALUE assignments from ~/.config/localllm/local.env."""
    wanted = {
        "LOCALLLM_MODE",
        "LOCALLLM_APPS",
        "HELPER_HOST",
        "MAIN_HOST",
        "MAIN_RAM_NAME",
        "MAIN_CPU_NORMAL_GHZ",
        "MAIN_CPU_PERFORMANT_GHZ",
    }
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, rest = line.partition("=")
        key = key.strip()
        if key not in wanted:
            continue
        val = rest.strip()
        if val and val[0] in ("'", '"'):
            try:
                parts = shlex.split(val, comments=True)
                val = parts[0] if parts else ""
            except ValueError:
                val = val.strip("\"'")
        else:
            val = val.split("#", 1)[0].strip()
        values[key] = val
    return values


def _normalize_apps(raw: str) -> str:
    val = (raw or "").strip().lower()
    if not val:
        return "pi,owui"
    parts = {p.strip() for p in val.split(",") if p.strip()}
    if parts == {"pi"}:
        return "pi"
    if parts == {"pi", "owui"}:
        return "pi,owui"
    return val


def _has_helper_ssh_config(home: Path) -> bool:
    cfg = home / ".ssh" / "config"
    if not cfg.is_file():
        return False
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(rf"^\s*Host\s+.*\b{re.escape(HELPER_SSH_ALIAS)}\b", text, re.MULTILINE))


def read_setup_mode(home: Path | None = None) -> dict:
    """Read LOCALLLM_MODE (single|split), LOCALLLM_APPS (pi|pi,owui), and helper host."""
    home_dir = home or Path.home()
    env = _parse_local_env(home_dir / ".config" / "localllm" / "local.env")
    endpoints_path = home_dir / ".config" / "localllm" / "endpoints.json"
    ep = {}
    if endpoints_path.is_file():
        try:
            ep = json.loads(endpoints_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ep = {}

    mode = (env.get("LOCALLLM_MODE") or ep.get("mode") or "single").strip().lower()
    if mode not in ("single", "split"):
        mode = "single"
    apps = _normalize_apps(env.get("LOCALLLM_APPS") or ep.get("apps") or "pi,owui")
    helper_host = (env.get("HELPER_HOST") or ep.get("helper_host") or "").strip()
    helper_configured = bool(helper_host) or _has_helper_ssh_config(home_dir)
    return {
        "mode": mode,
        "apps": apps,
        "helper_host": helper_host,
        "helper_configured": helper_configured,
    }


def _read_local_brand() -> str:
    for p in (Path("/sys/class/dmi/id/sys_vendor"), Path("/sys/class/dmi/id/board_vendor")):
        try:
            val = _clean_dmi_field(p.read_text(encoding="utf-8", errors="replace"))
            if val:
                return val
        except OSError:
            continue
    return ""


def _read_local_os() -> tuple[str, str]:
    os_name, os_id = "Linux", "linux"
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return os_name, os_id
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip().strip("\"'") or os_name
        elif line.startswith("ID="):
            os_id = line.split("=", 1)[1].strip().strip("\"'").lower() or os_id
    return os_name, os_id


def _read_proc_stat() -> tuple[int, int] | None:
    """Return (busy_jiffies, total_jiffies) from /proc/stat."""
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            line = f.readline()
    except OSError:
        return None
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    nums = [int(x) for x in parts[1:]]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    return total - idle, total


def _read_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    return re.sub(r"\s+", " ", name)
    except OSError:
        pass
    return ""


def _read_int_file(path: Path) -> int | None:
    try:
        txt = path.read_text(encoding="utf-8").strip()
        return int(txt) if txt else None
    except (OSError, ValueError):
        return None


def _read_cpu_ghz(cpu_model: str, home: Path | None = None) -> tuple[float | None, float | None]:
    """Return (normal_ghz, performant_ghz) for the local Linux CPU."""
    env = _parse_local_env((home or Path.home()) / ".config" / "localllm" / "local.env")
    base_dir = Path("/sys/devices/system/cpu/cpu0/cpufreq")

    max_khz: int | None = None
    cpu_root = Path("/sys/devices/system/cpu")
    if cpu_root.is_dir():
        for p in cpu_root.glob("cpu[0-9]*/cpufreq/cpuinfo_max_freq"):
            v = _read_int_file(p)
            if v and (max_khz is None or v > max_khz):
                max_khz = v
    if max_khz is None:
        max_khz = _read_int_file(base_dir / "scaling_max_freq")

    norm_khz = (
        _read_int_file(base_dir / "base_frequency")
        or _read_int_file(base_dir / "amd_pstate_nominal_freq")
    )
    if norm_khz is None:
        avail_path = base_dir / "scaling_available_frequencies"
        try:
            vals = [int(x) for x in avail_path.read_text(encoding="utf-8").split() if x.isdigit()]
            if vals:
                norm_khz = max(vals)
        except OSError:
            pass
    if norm_khz is None:
        sc_max = _read_int_file(base_dir / "scaling_max_freq")
        if sc_max and (max_khz is None or sc_max <= max_khz):
            norm_khz = sc_max

    normal_ghz = round(norm_khz / 1_000_000.0, 2) if norm_khz else None
    performant_ghz = round(max_khz / 1_000_000.0, 2) if max_khz else normal_ghz

    if normal_ghz is None and cpu_model:
        m = re.search(r"@\s*(\d+(?:\.\d+)?)\s*GHz", cpu_model, re.IGNORECASE)
        if m:
            normal_ghz = round(float(m.group(1)), 2)

    for env_key, is_perf in (("MAIN_CPU_NORMAL_GHZ", False), ("MAIN_CPU_PERFORMANT_GHZ", True)):
        raw = env.get(env_key, "").strip()
        if raw:
            try:
                val = round(float(raw), 2)
                if is_perf:
                    performant_ghz = val
                else:
                    normal_ghz = val
            except ValueError:
                pass

    return normal_ghz, performant_ghz


def parse_dmidecode_memory(text: str) -> dict:
    """Parse `dmidecode -t memory` output into commercial RAM name, speed, and installed MiB."""
    modules = []
    cur: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line == "Memory Device":
            if cur is not None:
                modules.append(cur)
            cur = {}
            continue
        if cur is not None and ":" in line:
            k, _, v = line.partition(":")
            cur[k.strip()] = v.strip()
    if cur is not None:
        modules.append(cur)

    labels: list[str] = []
    installed_mib = 0.0
    max_speed: int | None = None
    for mod in modules:
        size_str = mod.get("Size", "")
        m_size = re.match(r"(\d+)\s*(GB|GiB|MB|MiB)", size_str, re.IGNORECASE)
        if not m_size:
            continue
        amount = float(m_size.group(1))
        unit = m_size.group(2).lower()
        installed_mib += amount * 1024.0 if unit.startswith("g") else amount

        mfr = _clean_dmi_field(mod.get("Manufacturer", ""))
        part = _clean_dmi_field(mod.get("Part Number", ""))
        label = " ".join(x for x in (mfr, part) if x)
        if label and label not in labels:
            labels.append(label)

        for sp_key in ("Configured Memory Speed", "Speed"):
            m_sp = re.match(r"(\d+)\s*(MT/s|MHz)", mod.get(sp_key, ""), re.IGNORECASE)
            if m_sp:
                sp = int(m_sp.group(1))
                if max_speed is None or sp > max_speed:
                    max_speed = sp
                break

    return {
        "ram_name": " / ".join(labels),
        "ram_speed_mhz": max_speed,
        "ram_installed_mib": round(installed_mib, 1) if installed_mib > 0 else None,
    }


def _hardware_cache_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "localllm" / "hardware.json"


def _read_cached_ram_hw(home: Path | None = None) -> dict:
    home_dir = home or Path.home()
    env = _parse_local_env(home_dir / ".config" / "localllm" / "local.env")
    hw: dict = {"ram_name": "", "ram_speed_mhz": None, "ram_installed_mib": None}

    cache = _hardware_cache_path(home_dir)
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                hw.update({k: data.get(k) for k in hw if data.get(k) is not None})
        except (OSError, ValueError):
            pass

    if not hw["ram_name"]:
        for cmd in (["dmidecode", "-t", "memory"], ["sudo", "-n", "dmidecode", "-t", "memory"]):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3)
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode == 0 and "Memory Device" in proc.stdout:
                parsed = parse_dmidecode_memory(proc.stdout)
                if parsed["ram_name"] or parsed["ram_installed_mib"]:
                    hw.update({k: v for k, v in parsed.items() if v})
                    break

    if env.get("MAIN_RAM_NAME"):
        hw["ram_name"] = env["MAIN_RAM_NAME"]
    return hw


def detect_and_cache_hardware(home: Path | None = None) -> int:
    """Run sudo dmidecode -t memory once and save RAM commercial info to ~/.config/localllm/hardware.json."""
    cmd = ["dmidecode", "-t", "memory"] if os.geteuid() == 0 else ["sudo", "dmidecode", "-t", "memory"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except FileNotFoundError:
        print("ERROR: dmidecode is not installed (install with: sudo pacman -S dmidecode).", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: Could not run dmidecode: {exc}", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()
        print(f"ERROR: dmidecode failed: {msg}", file=sys.stderr)
        return 1

    parsed = parse_dmidecode_memory(proc.stdout)
    cache = _hardware_cache_path(home)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
    name = parsed["ram_name"] or "(not reported by BIOS)"
    cap = f"{parsed['ram_installed_mib'] / 1024:.0f} GB" if parsed["ram_installed_mib"] else "unknown"
    speed = f" @ {parsed['ram_speed_mhz']} MT/s" if parsed["ram_speed_mhz"] else ""
    print(f"Saved main PC RAM info to {cache}: {name} · {cap}{speed}")
    return 0


def _read_meminfo(ram_hw: dict) -> dict:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                m = re.match(r"\s*(\d+)", v)
                if m:
                    info[k.strip()] = int(m.group(1))
    except OSError:
        return {
            "name": ram_hw.get("ram_name") or "",
            "speed_mhz": ram_hw.get("ram_speed_mhz"),
            "installed_mib": ram_hw.get("ram_installed_mib"),
            "used_mib": None,
            "total_mib": None,
            "percent": None,
        }

    total_kib = info.get("MemTotal", 0)
    if total_kib <= 0:
        return {
            "name": ram_hw.get("ram_name") or "",
            "speed_mhz": ram_hw.get("ram_speed_mhz"),
            "installed_mib": ram_hw.get("ram_installed_mib"),
            "used_mib": None,
            "total_mib": None,
            "percent": None,
        }
    if "MemAvailable" in info:
        avail_kib = info["MemAvailable"]
    else:
        avail_kib = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
    used_kib = max(0, total_kib - avail_kib)
    used_mib = round(used_kib / 1024.0, 1)
    total_mib = round(total_kib / 1024.0, 1)
    installed_mib = ram_hw.get("ram_installed_mib")
    ram_name = ram_hw.get("ram_name") or ""
    ram_speed = ram_hw.get("ram_speed_mhz")
    if installed_mib and abs(installed_mib - total_mib) / installed_mib > 0.20:
        installed_mib = None
        ram_name = ""
        ram_speed = None
    return {
        "name": ram_name,
        "speed_mhz": ram_speed,
        "installed_mib": installed_mib,
        "used_mib": used_mib,
        "total_mib": total_mib,
        "percent": _clamp_pct((used_kib / total_kib) * 100.0),
    }


def _parse_nvidia_smi_csv(stdout: str) -> list[dict]:
    gpus: list[dict] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue

        util = _clamp_pct(parts[1])
        mem_used = _to_float(parts[2])
        mem_total = _to_float(parts[3])
        temp = _to_float(parts[4]) if len(parts) >= 5 else None
        mem_pct = _clamp_pct((mem_used / mem_total) * 100.0) if (mem_used is not None and mem_total and mem_total > 0) else None
        gpus.append({
            "name": parts[0],
            "util_percent": util,
            "mem_used_mib": round(mem_used, 1) if mem_used is not None else None,
            "mem_total_mib": round(mem_total, 1) if mem_total is not None else None,
            "mem_percent": mem_pct,
            "temp_c": round(temp, 1) if temp is not None else None,
        })
    return gpus


def _read_local_gpus() -> tuple[list[dict], str | None]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except FileNotFoundError:
        return [], "nvidia-smi not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return [], str(exc)

    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        return [], msg[-1] if msg else f"nvidia-smi exited with {proc.returncode}"
    return _parse_nvidia_smi_csv(proc.stdout), None


def _read_proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_local_processes(cpu_threads: int | None) -> dict:
    """Collect top CPU, RAM, and GPU processes on the local Linux PC."""
    cpu_list: list[dict] = []
    ram_list: list[dict] = []
    divisor = float(cpu_threads) if cpu_threads and cpu_threads > 0 else 1.0

    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,pcpu=,rss=,comm=,args="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.strip().split(None, 4)
                if len(parts) < 4:
                    continue
                pid = _to_int(parts[0])
                pcpu = _to_float(parts[1])
                rss_kib = _to_float(parts[2])
                comm = parts[3]
                args = parts[4] if len(parts) >= 5 else comm
                if pid is None:
                    continue
                fname = friendly_process_name(comm, args)
                if pcpu is not None and pcpu > 0:
                    norm_cpu = round(pcpu / divisor, 1)
                    if norm_cpu > 0:
                        cpu_list.append({
                            "pid": pid,
                            "name": fname,
                            "raw_name": comm,
                            "percent": norm_cpu,
                        })
                if rss_kib is not None and rss_kib >= 1024:
                    ram_list.append({
                        "pid": pid,
                        "name": fname,
                        "raw_name": comm,
                        "mem_mib": round(rss_kib / 1024.0, 1),
                    })
    except (OSError, subprocess.SubprocessError):
        pass

    cpu_list.sort(key=lambda x: x["percent"], reverse=True)
    ram_list.sort(key=lambda x: x["mem_mib"], reverse=True)

    # GPU compute processes via nvidia-smi
    gpu_list: list[dict] = []
    sm_by_pid: dict[int, float] = {}
    try:
        pmon = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1", "-s", "u"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
        if pmon.returncode == 0:
            for line in pmon.stdout.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                cols = s.split()
                if len(cols) >= 4:
                    pid = _to_int(cols[1])
                    sm = _to_float(cols[3])
                    if pid is not None and sm is not None:
                        sm_by_pid[pid] = round(sm, 1)
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        apps = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
        )
        if apps.returncode == 0:
            for line in apps.stdout.splitlines():
                s = line.strip()
                if not s:
                    continue
                parts = [p.strip() for p in s.split(",")]
                if len(parts) < 3:
                    continue
                pid = _to_int(parts[0])
                raw_path = parts[1]
                mem_mib = _to_float(parts[2])
                if pid is None:
                    continue
                cmd = _read_proc_cmdline(pid) or raw_path
                raw_name = Path(raw_path).name
                gpu_list.append({
                    "pid": pid,
                    "name": friendly_process_name(raw_name, cmd),
                    "raw_name": raw_name,
                    "mem_mib": round(mem_mib, 1) if mem_mib is not None else None,
                    "util_percent": sm_by_pid.get(pid),
                })
    except (OSError, subprocess.SubprocessError):
        pass

    gpu_list.sort(key=lambda x: x["mem_mib"] or 0.0, reverse=True)
    return {
        "cpu": cpu_list[:MAX_PROCESSES],
        "ram": ram_list[:MAX_PROCESSES],
        "gpu": gpu_list[:MAX_PROCESSES],
    }


class MainCollector:
    """Collects CPU, RAM, GPU, and process usage on the local Linux main PC."""

    def __init__(self, home: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._home = home
        self._prev_cpu: tuple[int, int] | None = None
        self._cpu_model = _read_cpu_model()
        self._cpu_threads = os.cpu_count()
        self._cpu_normal_ghz, self._cpu_performant_ghz = _read_cpu_ghz(self._cpu_model, home)
        self._ram_hw = _read_cached_ram_hw(home)
        self._brand = _read_local_brand()
        self._os_name, self._os_id = _read_local_os()
        self._hostname = socket.gethostname()

    def _cpu_percent(self) -> float | None:
        sample = _read_proc_stat()
        if sample is None:
            return None
        if self._prev_cpu is None:
            self._prev_cpu = sample
            time.sleep(0.2)
            sample = _read_proc_stat()
            if sample is None:
                return None
        prev_busy, prev_total = self._prev_cpu
        cur_busy, cur_total = sample
        self._prev_cpu = sample
        d_total = cur_total - prev_total
        if d_total <= 0:
            return 0.0
        return _clamp_pct(((cur_busy - prev_busy) / d_total) * 100.0)

    def collect(self) -> dict:
        with self._lock:
            cpu_pct = self._cpu_percent()
            ram = _read_meminfo(self._ram_hw)
            gpus, gpu_error = _read_local_gpus()
            procs = _read_local_processes(self._cpu_threads)
            return {
                "status": "online",
                "hostname": self._hostname,
                "brand": self._brand,
                "os": self._os_name,
                "os_id": self._os_id,
                "cpu": {
                    "name": self._cpu_model,
                    "threads": self._cpu_threads,
                    "normal_ghz": self._cpu_normal_ghz,
                    "performant_ghz": self._cpu_performant_ghz,
                    "percent": cpu_pct,
                },
                "ram": ram,
                "gpus": gpus,
                "gpu_error": gpu_error,
                "processes": procs,
                "collected_at": time.time(),
            }


def _as_list(val) -> list:
    if isinstance(val, dict):
        return [val]
    if isinstance(val, list):
        return val
    return []


def normalize_helper_payload(raw: dict, helper_host: str) -> dict:
    """Normalize the JSON returned by the Windows helper PowerShell script."""
    cpu_in = raw.get("cpu") if isinstance(raw.get("cpu"), dict) else {}
    ram_in = raw.get("ram") if isinstance(raw.get("ram"), dict) else {}
    used_mib = _to_float(ram_in.get("used_mib"))
    total_mib = _to_float(ram_in.get("total_mib"))
    installed_mib = _to_float(ram_in.get("installed_mib"))
    ram_pct = _clamp_pct((used_mib / total_mib) * 100.0) if (used_mib is not None and total_mib and total_mib > 0) else None

    gpus = []
    for g in _as_list(raw.get("gpus")):
        if not isinstance(g, dict):
            continue
        mu = _to_float(g.get("mem_used_mib"))
        mt = _to_float(g.get("mem_total_mib"))
        temp = _to_float(g.get("temp_c"))
        mp = _clamp_pct((mu / mt) * 100.0) if (mu is not None and mt and mt > 0) else None
        gpus.append({
            "name": str(g.get("name") or "GPU"),
            "util_percent": _clamp_pct(g.get("util_percent")),
            "mem_used_mib": round(mu, 1) if mu is not None else None,
            "mem_total_mib": round(mt, 1) if mt is not None else None,
            "mem_percent": mp,
            "temp_c": round(temp, 1) if temp is not None else None,
        })

    procs_in = raw.get("processes") if isinstance(raw.get("processes"), dict) else {}
    cpu_procs = []
    for p in _as_list(procs_in.get("cpu")):
        if not isinstance(p, dict):
            continue
        pct = _to_float(p.get("percent"))
        if pct is None or pct <= 0:
            continue
        rn = str(p.get("raw_name") or "")
        cmd = str(p.get("cmd") or "")
        cpu_procs.append({
            "pid": _to_int(p.get("pid")),
            "name": friendly_process_name(rn, cmd),
            "raw_name": rn,
            "percent": round(pct, 1),
        })
    cpu_procs.sort(key=lambda x: x["percent"], reverse=True)

    ram_procs = []
    for p in _as_list(procs_in.get("ram")):
        if not isinstance(p, dict):
            continue
        mm = _to_float(p.get("mem_mib"))
        if mm is None or mm < 1.0:
            continue
        rn = str(p.get("raw_name") or "")
        cmd = str(p.get("cmd") or "")
        ram_procs.append({
            "pid": _to_int(p.get("pid")),
            "name": friendly_process_name(rn, cmd),
            "raw_name": rn,
            "mem_mib": round(mm, 1),
        })
    ram_procs.sort(key=lambda x: x["mem_mib"], reverse=True)

    gpu_procs = []
    for p in _as_list(procs_in.get("gpu")):
        if not isinstance(p, dict):
            continue
        mm = _to_float(p.get("mem_mib"))
        rn = str(p.get("raw_name") or "")
        cmd = str(p.get("cmd") or "")
        gpu_procs.append({
            "pid": _to_int(p.get("pid")),
            "name": friendly_process_name(rn, cmd),
            "raw_name": rn,
            "mem_mib": round(mm, 1) if mm is not None else None,
            "util_percent": _to_float(p.get("util_percent")),
        })
    gpu_procs.sort(key=lambda x: x["mem_mib"] or 0.0, reverse=True)

    normal_ghz = _to_float(cpu_in.get("normal_ghz"))
    performant_ghz = _to_float(cpu_in.get("performant_ghz"))

    return {
        "status": "online",
        "hostname": str(raw.get("hostname") or helper_host or HELPER_SSH_ALIAS),
        "host": helper_host,
        "brand": _clean_dmi_field(str(raw.get("brand") or "")),
        "os": str(raw.get("os") or "Windows"),
        "os_id": str(raw.get("os_id") or "windows"),
        "gaming": bool(raw.get("gaming")),
        "cpu": {
            "name": str(cpu_in.get("name") or ""),
            "threads": _to_int(cpu_in.get("threads")),
            "normal_ghz": round(normal_ghz, 2) if normal_ghz is not None else None,
            "performant_ghz": round(performant_ghz, 2) if performant_ghz is not None else None,
            "percent": _clamp_pct(cpu_in.get("percent")),
        },
        "ram": {
            "name": str(ram_in.get("name") or ""),
            "speed_mhz": _to_int(ram_in.get("speed_mhz")),
            "installed_mib": round(installed_mib, 1) if installed_mib is not None else None,
            "used_mib": round(used_mib, 1) if used_mib is not None else None,
            "total_mib": round(total_mib, 1) if total_mib is not None else None,
            "percent": ram_pct,
        },
        "gpus": gpus,
        "gpu_error": None if gpus else "No NVIDIA GPU reported",
        "processes": {
            "cpu": cpu_procs[:MAX_PROCESSES],
            "ram": ram_procs[:MAX_PROCESSES],
            "gpu": gpu_procs[:MAX_PROCESSES],
        },
        "error": None,
        "collected_at": time.time(),
    }


def fetch_helper_metrics(setup: dict, home: Path | None = None, timeout: int = 15) -> dict:
    """Query the Windows helper PC over SSH for CPU, RAM, GPU, and process metrics."""
    home_dir = home or Path.home()
    helper_host = setup.get("helper_host") or ""
    if not setup.get("helper_configured"):
        return {
            "status": "not_configured",
            "hostname": "",
            "host": "",
            "brand": "",
            "os": "Windows",
            "os_id": "windows",
            "gaming": False,
            "cpu": None,
            "ram": None,
            "gpus": [],
            "processes": {"cpu": [], "ram": [], "gpu": []},
            "error": "No helper PC configured yet (run ./localllm.sh setup-helper).",
            "collected_at": time.time(),
        }

    ssh_config = home_dir / ".ssh" / "config"
    cmd = [
        "ssh",
        "-F",
        str(ssh_config),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        HELPER_SSH_ALIAS,
        HELPER_PS_LAUNCHER,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=HELPER_PS_SCRIPT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return _helper_error(helper_host, "ssh command not found")
    except subprocess.TimeoutExpired:
        return _helper_error(helper_host, "SSH request timed out")
    except OSError as exc:
        return _helper_error(helper_host, str(exc))

    if proc.returncode != 0:
        err_lines = [ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
        msg = err_lines[-1] if err_lines else f"SSH failed (exit {proc.returncode})"
        return _helper_error(helper_host, msg)

    json_lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    if not json_lines:
        return _helper_error(helper_host, "Helper returned no JSON metrics")
    try:
        parsed = json.loads(json_lines[-1])
    except ValueError as exc:
        return _helper_error(helper_host, f"Invalid JSON from helper: {exc}")
    if not isinstance(parsed, dict):
        return _helper_error(helper_host, "Helper returned unexpected JSON format")

    return normalize_helper_payload(parsed, helper_host)


def _helper_error(helper_host: str, message: str) -> dict:
    return {
        "status": "unreachable",
        "hostname": helper_host or HELPER_SSH_ALIAS,
        "host": helper_host,
        "brand": "",
        "os": "Windows",
        "os_id": "windows",
        "gaming": False,
        "cpu": None,
        "ram": None,
        "gpus": [],
        "processes": {"cpu": [], "ram": [], "gpu": []},
        "error": message,
        "collected_at": time.time(),
    }


class HelperPoller:
    """Polls the helper PC in a background thread so HTTP requests stay fast."""

    def __init__(self, interval: float = DEFAULT_INTERVAL) -> None:
        self._interval = max(2.0, float(interval))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._latest: dict | None = None
        self._thread = threading.Thread(target=self._run, name="helper-poller", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger_refresh(self) -> None:
        self._wake.set()

    def get(self, setup: dict) -> dict:
        if not setup.get("helper_configured"):
            return fetch_helper_metrics(setup)
        with self._lock:
            if self._latest is not None:
                return dict(self._latest)
        return {
            "status": "loading",
            "hostname": setup.get("helper_host") or HELPER_SSH_ALIAS,
            "host": setup.get("helper_host") or "",
            "brand": "",
            "os": "Windows",
            "os_id": "windows",
            "gaming": False,
            "cpu": None,
            "ram": None,
            "gpus": [],
            "processes": {"cpu": [], "ram": [], "gpu": []},
            "error": None,
            "collected_at": None,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                setup = read_setup_mode()
                data = fetch_helper_metrics(setup)
            except Exception as exc:  # noqa: BLE001 — never let the poller thread die silently
                data = _helper_error("", f"Monitor polling error: {exc}")
            with self._lock:
                self._latest = data
            self._wake.wait(timeout=self._interval)
            self._wake.clear()


def build_snapshot(main_collector: MainCollector, helper_data: dict, setup: dict | None = None) -> dict:
    setup_info = setup if setup is not None else read_setup_mode()
    return {
        "setup": setup_info,
        "main": main_collector.collect(),
        "helper": helper_data,
        "generated_at": time.time(),
    }


def make_handler(main_collector: MainCollector, helper_poller: HelperPoller):
    class MonitorHandler(BaseHTTPRequestHandler):
        server_version = "localllm-monitor/1.0"

        def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            if content_type.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self'; frame-ancestors 'none'",
                )
                self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(self, status: int, data: dict) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status, raw, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            static_files = {
                "/": (HTML_FILE, "text/html; charset=utf-8"),
                "/index.html": (HTML_FILE, "text/html; charset=utf-8"),
                "/monitor.css": (CSS_FILE, "text/css; charset=utf-8"),
                "/monitor.js": (JS_FILE, "application/javascript; charset=utf-8"),
            }
            if path in static_files:
                file_path, content_type = static_files[path]
                try:
                    payload = file_path.read_bytes()
                except OSError as exc:
                    self._send_json(500, {"error": f"Could not read {file_path.name}: {exc}"})
                    return
                self._send_bytes(200, payload, content_type)
                return

            if path == "/api/metrics":
                qs = parse_qs(parsed.query)
                if qs.get("refresh") == ["1"]:
                    helper_poller.trigger_refresh()
                setup = read_setup_mode()
                snapshot = build_snapshot(main_collector, helper_poller.get(setup), setup)
                self._send_json(200, snapshot)
                return

            if path == "/health":
                self._send_json(200, {"ok": True})
                return

            self._send_json(404, {"error": "Not found"})

        def log_message(self, fmt: str, *args) -> None:
            pass

    return MonitorHandler


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LocalLLM Main & Helper PC System Monitor")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"Bind address (default: {DEFAULT_HOST})")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP port (default: {DEFAULT_PORT})")
    p.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"Helper PC SSH poll interval in seconds (default: {DEFAULT_INTERVAL:g})",
    )
    p.add_argument("--once", action="store_true", help="Print one JSON snapshot to stdout and exit")
    p.add_argument(
        "--detect-hw",
        action="store_true",
        help="Run sudo dmidecode -t memory once to cache main PC RAM commercial name and installed capacity",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.detect_hw:
        return detect_and_cache_hardware()

    main_collector = MainCollector()

    if args.once:
        setup = read_setup_mode()
        helper_data = fetch_helper_metrics(setup)
        print(json.dumps(build_snapshot(main_collector, helper_data, setup), indent=2))
        return 0

    helper_poller = HelperPoller(interval=args.interval)
    helper_poller.start()

    handler = make_handler(main_collector, helper_poller)
    try:
        server = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"ERROR: Could not bind to {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    print(f"LocalLLM Monitor: http://{args.host}:{args.port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        helper_poller.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
