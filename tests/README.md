# Tests

These checks and tests keep Pi (the model with this repo's extensions and `pi/AGENTS.md`), Open WebUI and the setup on
both PCs working after a change. Three kinds: **guard checks** (no model, no live services, seconds; after every
change), **system checks** (the live setup as it runs, no model; after a section of work or a change to the setup) and
**model tests** (real questions to the model; only when wanted):

- **Guard checks** (`tests/guard-tests.mjs`, which also runs `tests/lang-check.py`): the extensions' rules and the
  word rules in every language, without the model. Seconds, no search credits.
  ```bash
  node --experimental-strip-types tests/guard-tests.mjs
  ```

- **System checks** (`tests/system-checks.sh`): the running setup against the repo and the model profile — llama-server serves the profile's model file as the alias, with its slots and context (and refuses requests without the API key in split mode); Pi's `models.json`, compaction in `settings.json` and `model.json`; SearXNG, the search gateway and its usage dashboard; every service running (`./localllm.sh status`) and the last backup named. Read-only; needs the services running. About a minute.
  ```bash
  tests/system-checks.sh
  ```
- **Restorability check** (`tests/restorability-check.sh`): whether everything installed on this machine is brought back by the repo's scripts, the settings backup or a README step, whether `restore.sh` puts the backup back (into a temporary HOME), and whether the repo itself clones from `backup/repo.bundle`. No model, read-only; run it after adding anything to the setup.
  ```bash
  tests/restorability-check.sh
  ```
- **Script lint** (ShellCheck, settings in `.shellcheckrc`): common mistakes in the shell scripts. Seconds.
  ```bash
  shellcheck -x $(git ls-files '*.sh')
  ```
- **Model tests** (`tests/run-pi-tests.sh`, prompts in `tests/pi-tests.json`): real questions to the model, one `pi -p` run each. About 1–3 minutes per test, and web tests use real search credits.
  ```bash
  tests/run-pi-tests.sh                      # everything except F1 and J1
  tests/run-pi-tests.sh E I1 H3              # some tests, or whole groups by letter
  tests/run-pi-tests.sh --with-f1 --with-j1  # also F1 (stops the search gateway for a moment) and J1 (needs the Inbox)
  ```
  Each run writes `~/LocalLLM/test-runs/<date-time>/<ID>.md` (prompt, expectation, reasoning on/off, every tool call with its arguments and result, blocks, the answer) and `summary.tsv`.

## A. Search filters

The `site:` table in AGENTS.md. Results come through the search gateway.

| # | Prompt | Expected |
|---|---|---|
| A1 | How do I fix "error: failed to commit transaction (invalid or corrupted package)" with pacman? | `site:wiki.archlinux.org` (or a general retry); the fix comes from a page it opened |
| A2 | How do I cancel a fetch() request in JavaScript? | `site:developer.mozilla.org` (or a docs tool); the answer mentions AbortController |
| A3 | What does rsync's --partial flag do? | `site:man.archlinux.org`; the answer comes from the man page text |
| A4 | Is there an official Docker image for Ollama? | `site:hub.docker.com`; names ollama/ollama |
| A5 | What's the latest version of the httpx Python package? | `site:pypi.org`, then opens the PyPI page and gives the version from it |
| A6 | Find recent papers on speculative decoding for LLMs. | lists papers, mostly with arXiv links (`site:arxiv.org` preferred, not required); titles and dates as the results or pages give them |
| A7 | Who developed the Mamba state space model architecture? | `site:wikipedia.org` or no filter; names Albert Gu and Tri Dao |

## B. Reading pages

| # | Prompt | Expected |
|---|---|---|
| B1 | Summarize https://docs.python.org/3/whatsnew/3.13.html | `open`, `get text body`, then reads the saved .txt in chunks; no snapshot |
| B3 | On https://wiki.archlinux.org/title/Pacman, what does the -Syu option do? | Opens the page; grep on the saved .txt (run by the guard) instead of reading it all |
| B4 | Read https://huggingface.co/Qwen/this-model-does-not-exist and tell me what's there. | Says the page doesn't exist (404); invents no content |

## C. Links and PDFs

| # | Prompt | Expected |
|---|---|---|
| C1 | Open https://pypi.org/project/httpx/ and follow the link to its documentation. Tell me what the docs' homepage says. | Link-finding `eval`, then `open` of the docs URL; no `find`, no clicking |
| C2 | Open https://arxiv.org/abs/1706.03762 and tell me the paper's title and authors, then open its PDF and tell me the first sentence of the abstract. | Reads the abstract page, opens the PDF; its text comes from pdftotext |

## D. Search behaviour and limits

The search tool has rate limits to prevent abuse:

- Default: 10 queries per minute
- Burst limit: 3 simultaneous queries
- Use `MAX_RESULTS` to reduce bandwidth on large result sets

## E. Reasoning switch and questions without the web

Reasoning is on when the assistant messages have thinking blocks (the report says "Reasoning: on/off").

| # | Prompt | Expected |
|---|---|---|
| E1 | What's a Python decorator? | No tools, no reasoning, a few seconds |
| E2 | What's the capital of Portugal? | No tools, no reasoning |
| E3 | think: is 2^31 - 1 prime? | Reasoning on, no web; yes (a Mersenne prime) |
| E4 | What files are in the current directory? (in a folder with config.yml) | `bash` only, no web; lists config.yml |

## F. Failures and fallbacks

When search fails, the tool falls back to reasoning mode:

1. Try search with the primary engine
2. If that fails, try a secondary engine (if configured)
3. If all search fails, answer from training data

## I. Editing files

The tool can read and write files on the filesystem. Files are written to the current working directory unless a full path is specified.

## K. Todo list, subagents and the sandbox

For complex tasks, use the todo list to track progress. Subagents can run in parallel for independent subtasks. The sandbox isolates file writes.

## L. Other languages

The tool supports multiple programming languages:

- JavaScript/TypeScript (primary)
- Python (via `tests/lang-check.py`)
- Shell scripts

Language-specific rules are defined in the extensions directory.

## G. Guard checks (no model)

Run guard checks alone:

```bash
node --experimental-strip-types tests/guard-tests.mjs
```

This verifies:
- Extensions rules are valid
- Word filters work in all languages
- No syntax errors in configuration

## S. System checks by hand (they change the live setup)

Some checks modify the environment. Run them carefully. This tests the live connection to Qwen-7B-Chat and verifies the API is responding correctly.
