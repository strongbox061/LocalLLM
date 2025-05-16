#!/usr/bin/env python3
"""Unit tests for tools/monitor/monitor.py (no live helper PC required)."""

import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "monitor"))
import monitor  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" ({detail})" if detail else ""))


def test_setup_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        cfg = home / ".config" / "localllm"
        cfg.mkdir(parents=True)

        s = monitor.read_setup_mode(home)
        check("default mode is single", s["mode"] == "single", str(s))
        check("default apps is pi,owui", s["apps"] == "pi,owui", str(s))
        check("default helper_configured is False", s["helper_configured"] is False, str(s))

        (cfg / "local.env").write_text(
            'LOCALLLM_MODE="split"\nLOCALLLM_APPS=pi\nHELPER_HOST="helper.example.ts.net"\n',
            encoding="utf-8",
        )
        s = monitor.read_setup_mode(home)
        check("reads split mode", s["mode"] == "split", str(s))
        check("reads pi-only apps mode", s["apps"] == "pi", str(s))
        check("reads helper_host and marks configured", s["helper_host"] == "helper.example.ts.net" and s["helper_configured"], str(s))

        (cfg / "local.env").write_text("LOCALLLM_MODE=single\nLOCALLLM_APPS='owui,pi'\n", encoding="utf-8")
        s = monitor.read_setup_mode(home)
        check("normalizes owui,pi -> pi,owui", s["mode"] == "single" and s["apps"] == "pi,owui", str(s))


def test_parsers() -> None:
    check(
        "helper SSH launcher stays tiny (script piped over stdin)",
        len(monitor.HELPER_PS_LAUNCHER) < 300,
        f"len={len(monitor.HELPER_PS_LAUNCHER)}",
    )
    check(
        "friendly_process_name extracts GGUF model from llama-server",
        monitor.friendly_process_name("llama-server", "/usr/bin/llama-server -m /models/demo-model-q4.gguf --port 8081")
        == "llama-server (demo-model-q4)",
    )
    check(
        "friendly_process_name maps LocalLLM services",
        monitor.friendly_process_name("python.exe", "python.exe C:\\repo\\services\\whisper_server.py") == "Whisper STT"
        and monitor.friendly_process_name("python3", "/venv/bin/open-webui serve") == "Open WebUI",
    )

    gpus = monitor._parse_nvidia_smi_csv("NVIDIA GeForce RTX 4060, 42, 6144, 8192, 65\n")
    check("nvidia-smi parser extracts 1 GPU", len(gpus) == 1, str(gpus))
    check("nvidia-smi parser calculates mem_percent", gpus[0]["mem_percent"] == 75.0 and gpus[0]["util_percent"] == 42.0, str(gpus))

    dmi_sample = """
Handle 0x0010, DMI type 17, 92 bytes
Memory Device
\tSize: 16 GB
\tSpeed: 5600 MT/s
\tManufacturer: Crucial Technology
\tPart Number: CT16G56C46S5.M8G1
\tConfigured Memory Speed: 5600 MT/s

Handle 0x0011, DMI type 17, 92 bytes
Memory Device
\tSize: 16 GB
\tSpeed: 5600 MT/s
\tManufacturer: Crucial Technology
\tPart Number: CT16G56C46S5.M8G1
\tConfigured Memory Speed: 5600 MT/s
"""
    dmi = monitor.parse_dmidecode_memory(dmi_sample)
    check("dmidecode parses commercial RAM name", dmi["ram_name"] == "Crucial Technology CT16G56C46S5.M8G1", str(dmi))
    check("dmidecode sums installed MiB and speed", dmi["ram_installed_mib"] == 32768.0 and dmi["ram_speed_mhz"] == 5600, str(dmi))

    helper = monitor.normalize_helper_payload(
        {
            "hostname": "HELPER-PC",
            "brand": "ExampleVendor Inc.",
            "os": "Windows 11 Pro",
            "os_id": "windows",
            "cpu": {"name": "Example CPU", "threads": 12, "percent": 33.4, "normal_ghz": 3.6, "performant_ghz": 4.4},
            "ram": {"name": "Crucial CT16G48C40S5", "speed_mhz": 4800, "installed_mib": 16384.0, "used_mib": 8192.0, "total_mib": 16384.0},
            "gpus": {"name": "GTX 1080", "util_percent": 15, "mem_used_mib": 4096, "mem_total_mib": 8192, "temp_c": 58},
            "processes": {
                "cpu": {"pid": 123, "raw_name": "python.exe", "cmd": "python.exe services\\whisper_server.py", "percent": 12.5},
                "ram": [{"pid": 456, "raw_name": "open-webui.exe", "cmd": "open-webui serve", "mem_mib": 2048.0}],
                "gpu": {"pid": 123, "raw_name": "python.exe", "cmd": "python.exe services\\whisper_server.py", "mem_mib": 1500.0},
            },
            "gaming": True,
        },
        "helper.example.ts.net",
    )
    check("helper payload normalizes single GPU dict to list", len(helper["gpus"]) == 1 and helper["gpus"][0]["mem_percent"] == 50.0, str(helper))
    check("helper payload preserves brand, OS, and CPU GHz", helper["brand"] == "ExampleVendor Inc." and helper["os_id"] == "windows" and helper["cpu"]["percent"] == 33.4, str(helper))
    check(
        "helper payload normalizes process lists with friendly names",
        helper["processes"]["cpu"][0]["name"] == "Whisper STT"
        and helper["processes"]["ram"][0]["name"] == "Open WebUI"
        and helper["processes"]["gpu"][0]["name"] == "Whisper STT",
        str(helper["processes"]),
    )


def test_edge_cases() -> None:
    """Test edge cases and error handling in parsers."""
    # Empty GPU list should stay empty
    helper = monitor.normalize_helper_payload(
        {"hostname": "TEST", "brand": "", "os": "Linux", "os_id": "linux",
         "cpu": {"name": "Test", "threads": 4, "percent": 10.0, "normal_ghz": 2.5, "performant_ghz": 3.2},
         "ram": {"name": "DDR4", "speed_mhz": 3200, "installed_mib": 8192.0, "used_mib": 4096.0, "total_mib": 8192.0},
         "gpus": [],
         "processes": {"cpu": [], "ram": [], "gpu": []},
         "gaming": False},
        "localhost",
    )
    check("empty GPU list stays empty", len(helper["gpus"]) == 0, str(helper["gpus"]))

    # Unknown process name should return raw name
    check(
        "friendly_process_name returns raw name for unknown processes",
        monitor.friendly_process_name("node", "node some-unknown-script.js") == "some-unknown-script",
    )

    # Malformed nvidia-smi data should not crash
    gpus = monitor._parse_nvidia_smi_csv("invalid, data\n")
    check("malformed nvidia-smi returns empty list", gpus == [], str(gpus))

    # CPU with zero threads should not cause division issues
    helper = monitor.normalize_helper_payload(
        {"hostname": "TEST", "brand": "Test", "os": "Linux", "os_id": "linux",
         "cpu": {"name": "Test", "threads": 0, "percent": 0.0, "normal_ghz": 0.0, "performant_ghz": 0.0},
         "ram": {"name": "DDR4", "speed_mhz": 3200, "installed_mib": 8192.0, "used_mib": 0.0, "total_mib": 8192.0},
         "gpus": [],
         "processes": {"cpu": [], "ram": [], "gpu": []},
         "gaming": False},
        "localhost",
    )
    check("zero-thread CPU does not crash", helper["cpu"]["threads"] == 0, str(helper["cpu"]))


def test_http_server() -> None:
    mc = monitor.MainCollector()

    class StubPoller(monitor.HelperPoller):
        def get(self, setup):
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
                "error": "No helper PC configured.",
                "collected_at": 0,
            }

    poller = StubPoller()
    server = monitor.ThreadingHTTPServer(("127.0.0.1", 0), monitor.make_handler(mc, poller))
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
            check("GET / serves HTML page", resp.status == 200 and "LocalLLM System Monitor" in html and "procBackdrop" in html)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/monitor.css", timeout=5) as resp:
            css = resp.read().decode("utf-8")
            check("GET /monitor.css serves stylesheet with pie colors",
                  resp.status == 200 and resp.headers["Content-Type"].startswith("text/css")
                  and "--pie-free:" in css and "--pie-used:" in css)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/monitor.js", timeout=5) as resp:
            js = resp.read().decode("utf-8")
            check("GET /monitor.js serves script",
                  resp.status == 200 and resp.headers["Content-Type"].startswith("application/javascript")
                  and "/api/metrics" in js)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/metrics", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            check("GET /api/metrics returns setup, main, helper", {"setup", "main", "helper"}.issubset(data.keys()))
            check(
                "main PC metrics include brand, os_id, cpu GHz, ram, processes",
                data["main"]["status"] == "online"
                and bool(data["main"]["os_id"])
                and data["main"]["ram"]["total_mib"] > 0
                and data["main"]["cpu"]["performant_ghz"] is not None
                and isinstance(data["main"]["processes"]["ram"], list),
                str(data["main"]),
            )
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            check("GET /health returns ok", json.loads(resp.read().decode("utf-8")) == {"ok": True})
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    test_setup_mode()
    test_parsers()
    test_http_server()
    print(f"monitor-check: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
