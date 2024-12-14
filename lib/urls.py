#!/usr/bin/env python3
"""Where each service runs and the address its clients use, for this machine's mode.

    python3 lib/endpoints.py write <dir>   writes <dir>/endpoints.json (Pi's extensions) and <dir>/endpoints.env
                                           (lib/common.sh); prints nothing unless something is wrong
    python3 lib/endpoints.py print         the JSON on stdout

The services and their ports come from config/services.json; the mode from the environment, which lib/common.sh fills
from ~/.config/localllm/local.env:
    LOCALLLM_MODE    single (everything on the main PC, the default) or split (some services on the helper PC)
    LOCALLLM_APPS    pi,owui (default) or pi (which apps run)
    HELPER_HOST      the helper PC's Tailscale name (split mode)
    MAIN_HOST        the main PC's Tailscale name, which the helper uses to reach llama-server and SearXNG; when
                     unset, read from `tailscale status` (split mode)
    PLACE_<NAME>     main or helper: moves a movable service (e.g. PLACE_SEARCH=main) in split mode, with the one it
                     shares a browser with; remove it to restore the default placement.
Exit status 1 with the reason on stderr when the settings don't fit together.
"""

import json
import os
import shlex
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES_FILE = os.path.join(REPO, "config", "services.json")


class SettingsError(Exception):
    pass


def env_name(name):
    return name.upper().replace("-", "_")


def tailscale_self_name():
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        return (json.loads(out.stdout).get("Self", {}).get("DNSName") or "").rstrip(".")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def parse_apps(raw):
    val = (raw or "").strip().lower()
    if not val:
        return "pi,owui"
    parts = {p.strip() for p in val.split(",") if p.strip()}
    if parts == {"pi"}:
        return "pi"
    if parts == {"pi", "owui"}:
        return "pi,owui"
    raise SettingsError(f"LOCALLLM_APPS is '{raw}' in local.env: it must be pi or pi,owui.")


def build(environ, services_file=SERVICES_FILE, self_name=tailscale_self_name):
    with open(services_file) as f:
        services = json.load(f)["services"]
    mode = environ.get("LOCALLLM_MODE", "").strip() or "single"
    if mode not in ("single", "split"):
        raise SettingsError(f"LOCALLLM_MODE is '{mode}' in local.env: it must be single or split.")
    apps = parse_apps(environ.get("LOCALLLM_APPS", ""))
    helper = environ.get("HELPER_HOST", "").strip()
    main = environ.get("MAIN_HOST", "").strip()
    if mode == "split":
        if not helper:
            raise SettingsError("LOCALLLM_MODE=split needs HELPER_HOST in local.env (./localllm.sh setup-helper sets it).")
        main = main or self_name()
        if not main:
            raise SettingsError("Split mode: the main PC's Tailscale name is unknown (Tailscale not running?). "
                                "Start it (sudo systemctl start tailscaled), or set MAIN_HOST in local.env.")

    apps_set = set(apps.split(","))
    places = {}
    for s in services:
        name = s["name"]
        if "owui" not in apps_set and s.get("owui_only"):
            places[name] = None
            continue
        override = environ.get(f"PLACE_{env_name(name)}", "").strip()
        if mode == "single":
            places[name] = "main" if s["single"] else None
            continue
        place = s["split"]
        if override:
            if override not in ("main", "helper"):
                raise SettingsError(f"PLACE_{env_name(name)} is '{override}': it must be main or helper.")
            place = override
        places[name] = place
    # "together" works both ways: a service moved on its own takes its partner along, unless the partner is placed too.
    # When one partner is disabled (LOCALLLM_APPS=pi), the other keeps its place on its own.
    titles = {s["name"]: s["title"] for s in services}
    partners = {}
    for s in services:
        if s.get("together"):
            partners[s["name"]] = s["together"]
            partners[s["together"]] = s["name"]
    placed = {name for name in places if environ.get(f"PLACE_{env_name(name)}", "").strip()}
    for name, other in partners.items():
        if mode == "split" and places.get(name) and places.get(other) and name in placed and other not in placed:
            places[other] = places[name]
    for name, other in partners.items():
        if places.get(name) and places.get(other) and places.get(other) != places[name]:
            raise SettingsError(f"{titles[name]} and {titles[other]} must run on the same PC: check PLACE_* in local.env.")

    hosts = {"main": "127.0.0.1", "helper": helper}
    # How the helper reaches a service: its own on 127.0.0.1, the main PC's by its Tailscale name.
    from_helper_hosts = {"main": main, "helper": "127.0.0.1"}
    out_services = {}
    for s in services:
        name, place, port = s["name"], places[s["name"]], s["port"]
        entry = {"title": s["title"], "where": place, "port": port, "health": s["health"], "url": "", "from_helper": "",
                 "owui_only": bool(s.get("owui_only"))}
        if place and port:
            entry["url"] = f"http://{hosts[place]}:{port}"
            if mode == "split" and place == "helper" and s.get("split_url"):
                entry["url"] = s["split_url"].format(helper=helper)
            if mode == "split":
                entry["from_helper"] = f"http://{from_helper_hosts[place]}:{port}"
        out_services[name] = entry
    return {"mode": mode, "apps": apps, "helper_host": helper if mode == "split" else "",
            "main_host": main if mode == "split" else "", "services": out_services}


def as_env(data, services_file=SERVICES_FILE):
    with open(services_file) as f:
        services = json.load(f)["services"]
    lines = ["# Generated by lib/endpoints.py from local.env and config/services.json; don't edit.",
             f"LOCALLLM_MODE={shlex.quote(data['mode'])}",
             f"LOCALLLM_APPS={shlex.quote(data.get('apps', 'pi,owui'))}"]
    # HELPER_HOST stays as local.env has it (setup-helper needs it in single mode too); MAIN_HOST as found in split.
    if data["main_host"]:
        lines.append(f"MAIN_HOST={shlex.quote(data['main_host'])}")
    for s in services:
        e = data["services"][s["name"]]
        lines.append(f"WHERE_{env_name(s['name'])}={shlex.quote(e['where'] or '')}")
        if s["env"]:
            lines.append(f"{s['env']}={shlex.quote(e['url'])}")
    return "\n".join(lines) + "\n"


def write(directory, data):
    os.makedirs(directory, exist_ok=True)
    for name, text in (("endpoints.json", json.dumps(data, indent=2) + "\n"), ("endpoints.env", as_env(data))):
        path = os.path.join(directory, name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)


def main(argv):
    """CLI entry point: 'write <dir>' or 'print'."""
    if len(argv) < 2:
        print("Usage: urls.py write <dir> | print", file=sys.stderr)
        sys.exit(1)

    action = argv[1]
    environ = dict(os.environ)

    data = build(environ)

    if action == "write":
        if len(argv) < 3:
            print("Usage: urls.py write <dir>", file=sys.stderr)
            sys.exit(1)
        directory = argv[2]
        write(directory, data)
    elif action == "print":
        print(json.dumps(data, indent=2))
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
