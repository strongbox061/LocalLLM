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
- **System checks** (`tests/system-checks.sh`): the running setup against the repo and the model profile:
  llama-server serves the profile's model file as the alias, with its slots and context (and refuses requests without
  the API key in split mode); Pi's `models.json`, compaction in `settings.json` and `model.json`; SearXNG, the search
  gateway and its usage dashboard; every service running (`./localllm.sh status`) and the last backup named; OCR of a
  generated scanned page; and the restorability check below. With `--helper` (split mode), over SSH: the helper
  reaches llama-server (and is refused without the key), Open WebUI's model connection, document search model and
  hybrid search, each chat type's preset (only the lookup tool and the filter, web search off), and the integrity of
  Open WebUI's and the search gateway's databases. Read-only; needs the services running. About a minute.
  ```bash
  tests/system-checks.sh            # this PC
  tests/system-checks.sh --helper   # and the helper PC
  ```
- **Restorability check** (`tests/restorability-check.sh`): whether everything installed on this machine is brought
  back by the repo's scripts, the settings backup or a README step, whether `restore.sh` puts the backup back
  (into a temporary HOME), and whether the repo itself clones from `backup/repo.bundle`. No model, read-only; run it
  after adding anything to the setup.
  ```bash
  tests/restorability-check.sh
  ```
- **Context7 check** (`tests/context7-check.sh`): a real docs lookup through Pi with only Context7's tools and an
  empty library-ID cache: `resolve-library-id` for Next.js, then `query-docs` with a v14 ID, judged from the session
  file. Needs llama-server and the Context7 sign-in; about a minute.
  ```bash
  tests/context7-check.sh
  ```
- **Script lint** (ShellCheck, settings in `.shellcheckrc`): common mistakes in the shell scripts. Seconds.
  ```bash
  shellcheck -x $(git ls-files '*.sh')
  ```
- **Model tests** (`tests/run-pi-tests.sh`, prompts in `tests/pi-tests.json`): real questions to the model, one `pi -p` run
  each. About 1–3 minutes per test, and web tests use real search credits.
  ```bash
  tests/run-pi-tests.sh                      # everything except F1 and J1
  tests/run-pi-tests.sh E I1 H3              # some tests, or whole groups by letter
  tests/run-pi-tests.sh --with-f1 --with-j1  # also F1 (stops the search gateway for a moment) and J1 (needs the Inbox)
  ```
  Each run writes `~/LocalLLM/test-runs/<date-time>/<ID>.md` (prompt, expectation, reasoning on/off, every tool call
  with its arguments and result, blocks, the answer) and `summary.tsv`.

**Judge from the trace, not the answer.** A correct answer reached the wrong way (a search engine page in the browser,
Context7 for "latest" docs, retyped edit text) is a failure. Each report ends with a verdict line to fill in. Runs vary:
when a test fails, run it again once before concluding.

Expected for every web test: no `!` left in an effective query, no `backend` field, at most 4 `web_search` calls, links
copied from results, and `Service: <name>` in search results (the search gateway answered).

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
| B2 | What does the README of https://github.com/vercel-labs/agent-browser/blob/main/README.md say about installation? | The open is rewritten to raw.githubusercontent.com; one read; no curl |
| B3 | On https://wiki.archlinux.org/title/Pacman, what does the -Syu option do? | Opens the page; grep on the saved .txt (run by the guard) instead of reading it all |
| B4 | Read https://huggingface.co/Qwen/this-model-does-not-exist and tell me what's there. | Says the page doesn't exist (404); invents no content |

## C. Links and PDFs

| # | Prompt | Expected |
|---|---|---|
| C1 | Open https://pypi.org/project/httpx/ and follow the link to its documentation. Tell me what the docs' homepage says. | Link-finding `eval`, then `open` of the docs URL; no `find`, no clicking |
| C2 | Open https://arxiv.org/abs/1706.03762 and tell me the paper's title and authors, then open its PDF and tell me the first sentence of the abstract. | Reads the abstract page, opens the PDF; its text comes from pdftotext |

## D. Search behaviour and limits

| # | Prompt | Expected |
|---|---|---|
| D1 | What's new in the latest Ollama release? | General `web_search`; reads the release page or GitHub releases; dates in the answer |
| D2 | Compare the context lengths of Qwen3.5-35B-A3B and Llama 3.3 70B according to their model cards. | Two model cards read, two numbers, two links; at most 4 searches |
| D3 | Search Mojeek for "pi coding agent". | No browser search: `web_search` with the same words, and the answer starts by saying Mojeek doesn't allow automated browsers; nothing invented |
| D4 | Is there an issue or pull request in fitchmultz/pi-agent-browser-native about browsers staying open after pi exits? Give its title and number. | #89, with its exact title read from the issue page |
| D5 | Find the GitHub issue in pi-search-hub about the DuckDuckGo uv lookup bug. | Finds the related pull requests in ronnieops/pi-search-hub (#7, #8: ddgs installed with uv), read from GitHub, within the budget; invents no issue |

## E. Reasoning switch and questions without the web

Reasoning is on when the assistant messages have thinking blocks (the report says "Reasoning: on/off").

| # | Prompt | Expected |
|---|---|---|
| E1 | What's a Python decorator? | No tools, no reasoning, a few seconds |
| E2 | What's the capital of Portugal? | No tools, no reasoning |
| E3 | think: is 2^31 - 1 prime? | Reasoning on, no web; yes (a Mersenne prime) |
| E4 | What files are in the current directory? (in a folder with config.yml) | `bash` only, no web; lists config.yml |

## F. Failures and fallbacks

| # | Prompt | Expected |
|---|---|---|
| F1 | What's new in the latest Ollama release? — with the search gateway stopped (`--with-f1` stops and restarts it) | `web_search` says the server isn't running; the model tells you to start it (./start.sh, or on the helper PC in split mode) and doesn't search another way |
| F2 | Search Google directly for "qwen3.5 release" using the browser and list the top 3 results. | No browser search: `web_search` with the same words, and the answer starts by saying Google doesn't allow automated browsers |

## H. Library docs (Context7, GitMCP)

The project folder is called `webapp`, not after its version, so the model has to read package.json to learn it.

| # | Folder | Prompt | Expected |
|---|---|---|---|
| H1 | webapp (Next.js pinned to 14.2.3) | In this project, how do I read a dynamic route parameter (like [slug]) inside a page component? Check the docs for the version this project uses. | Reads package.json; `resolve-library-id` (or the cached ID); `query-docs` with a v14 ID; at most 3 Context7 calls |
| H2 | webapp | In this project, how do I set metadata (title, description) for a page? Check the docs for the version this project uses. | The Next.js ID comes from the cache (resolve blocked as cached); one `query-docs` with a v14 ID |
| H3 | empty | In the latest Vite docs, how do I configure a dev server proxy? | The live docs first (GitMCP, or the official docs site); no call reaches Context7 |
| H4 | empty | What changed in the latest Vite release? | The releases or changelog page, or `web_search`; not Context7 |

## I. Editing files

| # | Folder | Prompt | Expected |
|---|---|---|---|
| I1 | config.yml | In config.yml, change the port to 9090 and the logging level to debug. | Reads the file first; one edit call with short old texts; the file has port 9090 and level debug (shown in the report) |

## J. Plans (needs the Inbox: `plannotator inbox`)

| # | Folder | Prompt | Expected |
|---|---|---|---|
| J1 | webapp | Plan how to add a health-check endpoint to this project. | PLAN.md written; `plannotator_inbox` `send_message` with attachments ["PLAN.md"]; no other file changed (the report lists the folder) |

## K. Todo list, subagents and the sandbox

K3 may take up to 25 minutes (its own `timeout`: with one subagent at once, four code-finders run one after another).
K2 and K3 run in a copy of this repo's last commit (`git archive HEAD`); their reports say how many subagents ran at
once at most (the test's child `pi` processes). K4 runs with a stand-in HOME that has a
`.bashrc` (Pi's own folder and the model profile are the real ones), and the report says whether it is still there.

| # | Folder | Prompt | Expected |
|---|---|---|---|
| K1 | a small Python app (config, server, CLI, tests, README) | In this project, rename the setting port to listen_port everywhere (code, tests and README; the CLI option becomes --listen-port), add a --timeout option to the CLI (default 30) that is passed through to start() and shows in its message, add a test for it, then run the tests with python3 -m unittest and fix anything that fails. | The `todo` tool: the stages added first, each ticked off as it is done; no `port` left (only `listen_port`, `--listen-port`); `--timeout` 30 passed to `start()`; a new test; the tests run and pass (the report runs them again and lists any `port` left) |
| K2 | repo | Use the code-finder to find where the web_search tool is defined. | One `subagent` call with the code-finder; the main session reads no files itself, it gets only the summary; names `pi/extensions/search-gateway.ts` |
| K3 | repo | Send 4 code-finders in parallel, one each to find where these are defined: the web_search tool, the edit guard, the model alias, the restorability check. | Never more at once than `SUBAGENTS_AT_ONCE` (the report's "Most at once"), however the model splits its calls; all 4 answers come back with the right files |
| K5 | empty | Use the web-researcher to find out what changed in the latest httpx release. | One `subagent` call with the web-researcher (web search, reads the release page); the main session opens no pages itself; version, date and links from the pages. Uses search credits |
| K4 | config.yml, stand-in HOME | Delete ~/.bashrc | Any `rm` or write on `~/.bashrc` is blocked by the sandbox (nothing asks in `pi -p`); the answer says so; `.bashrc` is still there |

## L. Other languages

The same checks as their English versions, asked in another language; the answer comes back in that language.
`tests/run-pi-tests.sh H` runs H3-es and H3-zh too.

| # | Folder | Prompt | Expected |
|---|---|---|---|
| H3-es | empty | En la documentación más reciente de Vite, ¿cómo configuro un proxy para el servidor de desarrollo? | As H3: the live docs first; no call reaches Context7; in Spanish |
| D3-es | empty | Busca en Mojeek "pi coding agent". | As D3: no browser search, `web_search` with the same words, and the answer starts by saying so; in Spanish |
| E1-es | empty | ¿Qué es un decorador de Python? | As E1: no tools, no reasoning; in Spanish |
| I1-es | config.yml | En config.yml, cambia el puerto a 9090 y el nivel de registro a debug. | As I1: reads the file first; one edit call; port 9090 and level debug (shown in the report); in Spanish |
| H3-zh | empty | 在最新的 Vite 文档中，如何配置开发服务器代理？ | As H3: the live docs first; no call reaches Context7; in Chinese |
| D3-ar | empty | ابحث في Mojeek عن "pi coding agent". | As D3: no browser search, `web_search` with the same words, and the answer starts by saying so; in Arabic |

## G. Guard checks (no model)

`tests/guard-tests.mjs` checks, with a fake Pi:

- **web_search queries:** bangs become `site:` filters (`!gh` → `site:github.com`), engine bangs are removed, `backend`
  and `compact` are dropped.
- **Browser searches:** search engines' results pages (and the local SearXNG) are blocked with the same words handed
  to `web_search`; an engine the user names too (search engines refuse automated browsers), and the answer says so.
- **Library docs:** "latest" questions send the model to GitMCP first (with a ready call); a pinned project may use Context7
  first; 3 Context7 calls per question; the same lookup twice is refused; library IDs come from the cache. A
  temporary cache file is used.
- **web_search:** no backend parameter; unopenable links dropped; the same words again ask for another service, 2
  retries at most; a stopped server says to run ./start.sh.
- **short-tool-docs.ts:** long tool descriptions are shortened, `tool_search` is dropped, other tools untouched.
- **edit-guard.ts, write:** on an existing file, `write` is blocked until the session read all of it (not part, not
  a cut-short or failed read) or wrote it; `edit` and new files are fine; without your go-ahead the permission message
  comes first.
- **Finish check, files only:** an answer from files read (a code-finder's line numbers) isn't sent back for numbers
  missing from them; after a web search the same answer is.
- **todo.ts:** Pi's example unchanged below our header; `add` (text needed), `toggle` (a known id), `list`, `clear`;
  a resumed session gets its list back from the last todo result.
- **Subagents:** at once = model.json's `subagentsAtOnce` (from `SUBAGENTS_AT_ONCE` in local.env; 1 without it, never
  more than the slots); the queue hands out the highest free slot, the next in line gets a slot given back, an aborted
  wait gives up. With a stand-in for the child `pi`: a lone code-finder runs in the last slot; with 1 at once, 4 tasks in a
  call and two calls at the same time run one after another; with 2 at once, 3 tasks run at most 2 side by side in
  slots 1 and 0. `SUBAGENTS_MAX` per message of yours: a call that would go over it is refused
  (the model is told how many are left), a new message starts the count again; 8 without it, not capped at the slots. `lib/model.sh` writes the setting, capped at the slots with a
  warning, and refuses a value that isn't a number. Children get `--model llamacpp/local` (the alias in `lib/model.sh`), the agent's tools, no Plannotator or
  subagents, no session file, and your message; an unknown agent names the ones there are. The four agents are found,
  each with the alias as its model.
- **Sandbox merge** (the snippet in `install/pi-config.sh`, on temporary files with a temporary HOME): the repo's
  rules on a fresh install, nothing added the second time, your own rules kept, a path written out in full not added
  again with `~`, a broken file left alone with an error; our denyWrite repeats pi-sandbox's defaults.
- **Compaction** (the snippet in `install/pi-config.sh`, on a temporary `settings.json`): on, with the profile's
  reserve and kept part; every other key kept; nothing changed the second time; a missing file made; a broken one
  left alone with an error.
- **Fan scripts** (`tools/fans/`, temporary HOME): each runs its command from `local.env`; without it (or without
  `local.env`), a message naming the variable and `config/local.example.env`.
- **Model profiles** (`lib/model.sh`): llama-server's flags per profile, `local.env` overriding it, broken or unknown
  profiles refused, the subagent settings; the reasoning switch per reasoning type; the model's name nowhere outside `models/`.
- **The Python checks** it runs: `dashboard-check.py` (the usage dashboard's numbers against sample usage and
  Valves), `search-gateway-check.py`, `protected-chat-check.py` (the filter, the lookup failing closed, personal data
  refused, chat type files), `old-pages-check.py`.
- **`tests/two-pc-check.sh`:** where each service runs per mode, `localllm.sh`'s arguments and the helper unreachable,
  `setup-helper`'s edits, split mode's API key, the helper's `config.json`, the deploy (with a fake helper: only
  changed files are sent, files gone from the repo are removed, the helper's data never is), rebuilding from a
  split-mode backup.
- **The name check:** see README, "Testing Pi".
- **edit-fuzzy.ts:** spacing differences are fixed; a real difference names the closest lines and the file's text.
- **Languages** (`lang/tests.json`): for each of the 35 languages, sentences that must match each rule and ordinary
  sentences that must match none, against Pi's `lang-words.ts`; a few rules in use in Spanish, Chinese, Arabic and
  Japanese (latest docs, a named engine, a question without "?", reasoning on or off).
- **`tests/lang-check.py`** (the Open WebUI tool, with stand-ins for aiohttp and pydantic): `lang/build.py --check`;
  a test sentence for every rule of every language; current information, bot-check pages and months dropped from a
  query; and each language's phrases, one by one, against the other languages' ordinary sentences (collisions such
  as Czech "padá" in Malay "pada").

## S. System checks by hand (they change the live setup)

`tests/system-checks.sh` only reads. These change what runs, so they are done by hand, when the part they cover has
changed, with the reason written down when one is skipped:

- **Switching:** `./localllm.sh switch single`, then `switch split`. Each prints the chats, users, files and Knowledge
  bases on both sides; they must be equal, the integrity check `ok`, the old copy renamed (`...-moved-<date>`). Then
  `tests/system-checks.sh --helper`.
- **Gaming mode:** `./localllm.sh gaming on`: `nvidia-smi` on the helper shows its GPU memory freed, a question about
  a document in Open WebUI is still answered (search on the CPU), the small model and ComfyUI are stopped.
  `./localllm.sh gaming off`: all back (`./localllm.sh status`).
- **The helper unreachable:** with the helper off, `./localllm.sh status` names it unreachable and the main PC's part
  still runs; Pi answers.
- **The sandbox:** model test K4 (`tests/run-pi-tests.sh K4`, a stand-in HOME).
- **A rebuild:** README, "Rebuilding both PCs" (what only a real rebuild proves is listed there).

`run-pi-tests.sh` runs under `systemd-inhibit` (sleep and idle blocked) when it is available: an idle suspend once
froze a test for 89 minutes.

Pi's sessions all use llama-server's slot 0 (Open WebUI uses slot 1), so a Pi session working during a run makes the
tests wait at every step: each takes turns with it and reads its whole prompt again (run 17's B3, C1 and D2 ran into
their time limit that way). The runner warns at the start when another Pi session is open, and before each test waits
until llama-server is idle (at most 30 minutes), printing how long it waited.

F1 stops the search gateway with `./localllm.sh stop search-gateway`, so it works in split mode too (the gateway runs
on the helper PC). J1 runs with `tests/inbox-tool-on.ts` (`pi -e`): Plannotator leaves its Inbox tool off in `pi -p`,
since no reply could reach that session, and J1 checks what an interactive session does. J1 leaves a test message in
your Inbox.
