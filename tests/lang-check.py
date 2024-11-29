#!/usr/bin/env python3
"""lang-check.py: the language rules of the search gateway's engine and of lang/phrases.json, checked with lang/tests.json.

    python3 -I tests/lang-check.py

Run by tests/guard-tests.mjs (which checks Pi's side of the same sentences); no network, no model. Checks:
  - lang/build.py --check: both generated copies (Pi's lang-words.ts, the tool's block) are up to date;
  - every language has test sentences for each of its rules, and ordinary sentences ("none");
  - the tool: current_info and bot_check sentences match, months with a year are dropped from a query, and the
    ordinary sentences match neither;
  - collisions: each language's phrases, one by one, against the other languages' ordinary sentences.
The tool imports aiohttp and pydantic, which only Open WebUI has; small stand-ins are used here.
"""

import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = json.loads((ROOT / "lang" / "tests.json").read_text(encoding="utf-8"))["languages"]
PHRASES = json.loads((ROOT / "lang" / "phrases.json").read_text(encoding="utf-8"))
TOOL_RULES = ("current_info", "bot_check")
NOT_TESTED = ("please", "verb_last", "name")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


def load_module(name, path):
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool():
    if importlib.util.find_spec("aiohttp") is None:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")
    if importlib.util.find_spec("pydantic") is None:
        pydantic = types.ModuleType("pydantic")
        pydantic.BaseModel = type("BaseModel", (), {})
        pydantic.Field = lambda default=None, **_: default
        sys.modules["pydantic"] = pydantic
    return load_module("search_gateway_engine", ROOT / "services" / "search_gateway_engine.py")


# ---------------------------------------------------------------- generated copies
r = subprocess.run([sys.executable, "-I", str(ROOT / "lang" / "build.py"), "--check"], capture_output=True, text=True)
check("lang/build.py --check: generated copies up to date", r.returncode == 0, (r.stdout + r.stderr).strip())

# ---------------------------------------------------------------- coverage of lang/tests.json
check("every language has test sentences", sorted(TESTS) == sorted(PHRASES["languages"]),
      f"missing: {sorted(set(PHRASES['languages']) - set(TESTS))}, unknown: {sorted(set(TESTS) - set(PHRASES['languages']))}")
for code, lang in PHRASES["languages"].items():
    t = TESTS.get(code, {})
    missing = [rule for rule, entry in lang.items() if rule not in NOT_TESTED and entry and rule not in t]  # [] = can't be tested
    check(f"{code}: a test sentence for each rule", not missing, f"no sentences for {missing}")
    check(f"{code}: at least 2 ordinary sentences", len(t.get("none", [])) >= 2)

# ---------------------------------------------------------------- the tool
tool = load_tool()
TOOL_TEST = {"current_info": tool.wants_current_info, "bot_check": tool.looks_like_bot_check}
for code, t in TESTS.items():
    for rule, test in TOOL_TEST.items():
        for s in t.get(rule, []):
            check(f"tool {code}.{rule}: {s}", test(s))
        for s in t.get("none", []) + t.get("not", {}).get(rule, []):
            check(f"tool {code}: not {rule}: {s}", not test(s))
    for s in t.get("months", []):
        out = tool._strip_unasked_years(f"vite release {s}", "vite release")
        check(f"tool {code}.months: \"{s}\" dropped", out == "vite release", out)


# ---------------------------------------------------------------- collisions between languages
# Each language's rule on its own (Python flavor of the same regexes); on a hit, the phrase that caused it is named.
build = load_module("lang_build", ROOT / "lang" / "build.py")
py = build.Flavor(False)
please = build.please_re(PHRASES, py)
engine = build.engine_re(PHRASES, py)


def phrase_regex(rule, raw, anchor):
    return re.compile(build.combine([build.phrase_re(raw, py, anchor, engine if rule == "named_engine" else "")], py, please))


def culprits(code, rule, text):
    entry = PHRASES["languages"][code][rule]
    if not entry:
        return ["(no phrases defined)"]
    anchor = PHRASES["rules"][rule].get("anchor", "any")
    raws = entry.get("phrases", []) if rule == "named_source" else entry
    found = [raw for raw in raws if phrase_regex(rule, raw, anchor).search(text)]
    return found or ["(a verb … noun pair)"]


rules = [r for r, info in PHRASES["rules"].items() if r not in ("please", "months")]
for code in PHRASES["languages"]:
    one = {**PHRASES, "languages": {code: PHRASES["languages"][code]}}
    for rule in rules:
        if not PHRASES["languages"][code].get(rule):
            continue
        pattern = re.compile(build.build_rule(one, rule, py))
        hits = []
        for other, t in TESTS.items():
            if other == code:
                continue
            for s in t.get("none", []) + t.get("not", {}).get(rule, []):
                text = build.norm(s)
                if pattern.search(text):
                    hits.append(f"{other} \"{s}\" ← {', '.join(culprits(code, rule, text))}")
        check(f"{code}.{rule}: no phrase matches another language's ordinary sentences", not hits, "\n        ".join(hits))

print(f"lang-check: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
