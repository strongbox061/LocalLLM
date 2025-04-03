#!/usr/bin/env python3
"""
LocalLLM System Monitor — web dashboard for CPU, GPU, and RAM usage on the main
and helper PCs, plus the current setup mode (single/split) and apps (pi/pi,owui).

Usage:
    python3 tools/monitor/app.py [--host 127.0.0.1] [--port 8095] [--interval 5]
    python3 tools/monitor/app.py --once        # print one JSON snapshot and exit
    python3 tools/monitor/app.py --detect-hw   # detect main PC RAM brand/model via sudo dmidecode and cache it
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


# ---------------------------------------------------------------------------
# Helpers to query the main PC and remote helper PC
# ---------------------------------------------------------------------------


def get_cpu_percent() -> float:
    """Return current CPU usage percentage (0-100) via /proc/stat."""
    with open("/proc/stat") as f:
        line = f.readline()
    values = line.split()[1:]
    idle = int(values[3])
    total = sum(int(v) for v in values)
    return round((1 - idle / total) * 100, 1)


def get_gpu_stats() -> list[dict]:
    """Parse nvidia-smi output for GPU info."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "temp": float(parts[2]),
                    "util": float(parts[3]),
                    "mem_used": float(parts[4]),
                    "mem_total": float(parts[5]),
                })
        return gpus
    except (subprocess.TimeoutExpired, OSError):
        return []


def get_ram_stats() -> dict:
    """Read /proc/meminfo for RAM usage."""
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                value_kb = int(parts[1])
                mem[key] = value_kb * 1024  # convert to bytes

    total = mem.get("MemTotal", 0)
    free = mem.get("MemFree", 0)
    available = mem.get("MemAvailable", free)
    used = total - available
    pct = round((used / total) * 100, 1) if total else 0

    return {
        "total_gb": round(total / (1024 ** 3), 1),
        "used_gb": round(used / (1024 ** 3), 1),
        "free_gb": round(free / (1024 ** 3), 1),
        "available_gb": round(available / (1024 ** 3), 1),
        "percent": pct,
    }


def get_process_list() -> list[dict]:
    """Return top processes by CPU usage from /proc."""
    procs = []
    try:
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                comm = (pid_dir / "comm").read_text().strip()
                stat = (pid_dir / "stat").read_text()
                parts = stat.split(")")
                if len(parts) < 2:
                    continue
                fields = parts[1].split()
                utime = int(fields[10])
                stime = int(fields[11])
                total_time = utime + stime
                procs.append({
                    "pid": int(pid_dir.name),
                    "name": comm,
                    "cpu_time": total_time,
                })
            except (OSError, IndexError, ValueError):
                continue

        # Get total CPU time from /proc/stat
        with open("/proc/stat") as f:
            stat_line = f.readline()
        values = stat_line.split()[1:]
        total_cpu_time = sum(int(v) for v in values)

        procs.sort(key=lambda p: p["cpu_time"], reverse=True)
        return procs[:MAX_PROCESSES]
    except OSError:
        return []


def query_helper_pc() -> dict | None:
    """SSH to the helper PC and collect stats via PowerShell."""
    try:
        cmd = ["ssh", HELPER_SSH_ALIAS, HELPER_PS_LAUNCHER]
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=30,
        )
        stdout, _ = proc.communicate(input=HELPER_PS_SCRIPT)
        if proc.returncode != 0:
            return None
        return json.loads(stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return None


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
    if "voice_router.py" in low_cmd:
        return "Voice Router"
    if "search_gateway.py" in low_cmd:
        return "Search Gateway"
    if "comfyui" in low_cmd or ("main.py" in low_cmd and "--listen" in low_cmd):
        return "ComfyUI"
    if "searxng" in low_cmd or "searx" in low_cmd:
        return "SearXNG"
    if "app.py" in low_cmd:
        return "LocalLLM Monitor"
    if "chat_types_server.py" in low_cmd:
        return "Chat Types Server"

    base = re.sub(r"\.exe$", "", raw, flags=re.IGNORECASE).strip()
    low_base = base.lower()

    # Well-known desktop/system apps
    known = {
        "brave": "Brave Browser",
    }
    if low_base in known:
        return known[low_base]
    return raw or cmd or "Unknown"


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------


def build_snapshot() -> dict:
    """Build a single JSON-serializable snapshot of all system stats."""
    snapshot = {
        "ts": time.time(),
        "hostname": socket.gethostname(),
        "cpu_percent": get_cpu_percent(),
        "gpu": get_gpu_stats(),
        "ram": get_ram_stats(),
        "processes": get_process_list(),
        "helper": query_helper_pc(),
    }

    # Add RAM cache info if available
    ram_info = detect_ram_info()
    if ram_info:
        snapshot["ram_info"] = ram_info

    return snapshot


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class MonitorHandler(BaseHTTPRequestHandler):
    """Handle requests for the monitoring dashboard."""

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass

    def _set_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _send_json(self, data, status=200):
        self._set_headers(status=status)
        self.wfile.write(json.dumps(data, indent=2).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/snapshot":
            snapshot = build_snapshot()
            self._send_json(snapshot)

        elif path == "/api/health":
            self._send_json({"status": "ok", "ts": time.time()})

        elif path == "/dashboard" or path == "/":
            self._serve_file(HTML_FILE, "text/html")

        elif path == "/monitor.css":
            self._serve_file(CSS_FILE, "text/css")

        elif path == "/monitor.js":
            self._serve_file(JS_FILE, "application/javascript")

        else:
            self._send_json({"error": "not found"}, status=404)

    def _serve_file(self, filepath: Path, content_type: str):
        if filepath.exists():
            self._set_headers(content_type=content_type)
            self.wfile.write(filepath.read_bytes())
        else:
            self._send_json({"error": f"file not found: {filepath.name}"}, status=404)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="LocalLLM System Monitor")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    parser.add_argument("--detect-hw", action="store_true", help="Detect hardware RAM info and cache it")

    args = parser.parse_args()

    if args.detect_hw:
        result = detect_ram_info()
        print(json.dumps(result, indent=2) if result else "No RAM info detected (try running with sudo)")
        return

    if args.once:
        snapshot = build_snapshot()
        print(json.dumps(snapshot, indent=2))
        return

    # Start the HTTP server
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    print(f"LocalLLM Monitor running at http://{args.host}:{args.port}")
    print(f"Dashboard: http://{args.host}:{args.port}/dashboard")
    print(f"API snapshot: http://{args.host}:{args.port}/api/snapshot")
    print(f"Polling interval: {args.interval}s (background thread)")

    def poll_and_cache():
        """Periodically poll and cache snapshot data."""
        while True:
            try:
                snapshot = build_snapshot()
                cache_path = HERE / ".last_snapshot.json"
                with open(cache_path, "w") as f:
                    json.dump(snapshot, f)
            except Exception:
                pass
            time.sleep(args.interval)

    poller = threading.Thread(target=poll_and_cache, daemon=True)
    poller.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
