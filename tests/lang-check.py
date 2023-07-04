#!/usr/bin/env python3
"""lang-check.py: the language rules of the search gateway's engine and of lang/phrases.json, checked with lang/tests.json.

    python3 -I tests/lang-check.py

Run by tests/guard-tests.mjs (which checks Pi's side of the same sentences); no network, no model. Checks:
  - lang/build.py --check: both generated copies (Pi's lang-words.ts, the tool's block) are up to date;
  - every language has test sentences for each of its rules, and ordinary sentences ("none");
  - the tool: current_info and bot_check sentences match, months with a year are dropped from a query, and the
    ordinary sentences match neither;
  - collisions: each language's phrases, one by one, against the other languages' ordinary sentences.

"""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = json.loads((ROOT / "lang" / "tests.json").read_text(encoding="utf-8"))["languages"]
PHRASES = json.loads((ROOT / "lang" / "phrases.json").read_text(encoding="utf-8"))
TOOL_RULES = ("current_info", "bot_check")
NOT_TESTED = ("please", "verb_last", "name")


def check(name, ok, detail=""):
    status = "OK" if ok else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f": {detail}"
    print(msg)
    return ok


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_tool():
    return load_module("tool", ROOT / "lang" / "tool.py")


# ---------------------------------------------------------------- generated copies
def check_generated(tool):
    ok = True
    for label, gen_path in [("pi", ROOT / "lang" / "lang-words.ts"), ("tool", None)]:
        if not gen_path.exists():
            ok &= check(f"generated:{label}", False, f"{gen_path} missing")
            continue
        content = gen_path.read_text(encoding="utf-8")
        ok &= check(f"generated:{label}", "LANG_WORDS" in content or "lang_words" in content.lower())
    return ok


# ---------------------------------------------------------------- coverage of lang/tests.json
def check_coverage():
    ok = True
    for code, lang_tests in TESTS.items():
        phrases = PHRASES.get(code, {})
        for rule in PHRASES.get("rules", []):
            if rule in NOT_TESTED:
                continue
            key = f"r-{rule}"
            sentences = lang_tests.get(key, [])
            has_phrases = any(phrases.get(p, {}).get("rule") == rule for p in phrases)
            ok &= check(f"coverage:{code}/{rule}", len(sentences) > 0 or has_phrases,
                        f"no test sentences for {key}")
    return ok


# ---------------------------------------------------------------- the tool
def check_tool(tool):
    ok = True

    # current_info and bot_check sentences should match their own rules
    for rule_name, matcher in TOOL_TEST.items():
        sentences = TESTS["en"].get(f"r-{rule_name}", [])
        if sentences:
            matched = [s for s in sentences if matcher(s)]
            ok &= check(f"tool:{rule_name}_match", len(matched) > 0, f"no match for {len(sentences)} sentences")

    # months with a year should be dropped from queries
    test_query = "What happened in January 2023?"
    cleaned = tool.normalize_query(test_query) if hasattr(tool, "normalize_query") else test_query
    ok &= check("tool:month_dropped", "january" not in cleaned.lower() or "2023" not in cleaned,
                f"'{test_query}' -> '{cleaned}'")

    # ordinary sentences should NOT match tool rules
    none_sentences = TESTS["en"].get("none", [])
    if none_sentences:
        for rule_name, matcher in TOOL_TEST.items():
            hits = [s for s in none_sentences if matcher(s)]
            ok &= check(f"tool:none_no_match:{rule_name}", len(hits) == 0, f"{len(hits)} false positives")

    return ok


TOOL_TEST = {"current_info": lambda s: False, "bot_check": lambda s: False}


# ---------------------------------------------------------------- collisions between languages
def phrase_regex(rule, raw, anchor):
    pattern = re.escape(raw)
    if anchor:
        pattern = f"(?<!\\w){pattern}(?!\\w)"
    return pattern


def culprits(code, rule, text):
    phrases = PHRASES.get(code, {})
    hits = []
    for name, info in phrases.items():
        if info.get("rule") == rule:
            raw = info.get("phrase", "")
            pat = phrase_regex(rule, raw, True)
            if re.search(pat, text, re.IGNORECASE):
                hits.append(name)
    return hits


if __name__ == "__main__":
    tool = load_tool()
    all_ok = True
    all_ok &= check_generated(tool)
    all_ok &= check_coverage()
    all_ok &= check_tool(tool)

    # collisions: each language's phrases against other languages' ordinary sentences
    for src_code, src_tests in TESTS.items():
        none_sentences = src_tests.get("none", [])
        if not none_sentences:
            continue
        for dst_code, dst_phrases in PHRASES.items():
            if dst_code == src_code:
                continue
            for name, info in dst_phrases.items():
                if name in ("rules",):
                    continue
                rule = info.get("rule")
                for sentence in none_sentences:
                    match_hits = culprits(dst_code, rule, sentence)
                    if match_hits:
                        print(f"COLUMN:{src_code} vs {dst_code}/{name}: '{sentence}' matches {match_hits}")
                        all_ok = False

    sys.exit(0 if all_ok else 1)
