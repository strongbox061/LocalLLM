# Tests

both PCs working after a change. Three kinds: **guard checks** (no model, no live services, seconds; after every
change), **system checks** (the live setup as it runs, no model; after a section of work or a change to the setup) and
**model tests** (real questions to the model; only when wanted):

- **Guard checks** (`tests/guard-tests.mjs`, which also runs `tests/lang-check.py`): the extensions' rules and the
  word rules in every language, without the model. Seconds, no search credits.
  ```bash
  node --experimental-strip-types tests/guard-tests.mjs
  ```

- **System checks**: verify the live setup is running correctly. No model needed. Run from the project root.

- **Model tests**: ask real questions to Qwen-7B-Chat and verify responses. Only run when you want model feedback.

## A. Search filters

Control what gets returned by the search engine. Set environment variables before running:

- `SEARCH_ENGINE=google` (default) or `SEARCH_ENGINE=duckduckgo`
- `MAX_RESULTS=5` to limit results per query
- `SAFE_SEARCH=true` to filter explicit content

## B. Reading pages

Test that page extraction works correctly. The tool extracts readable text from HTML pages, strips navigation and footers, and returns the main content.

## C. Links and PDFs

Verify link following and PDF handling. PDFs are extracted as plain text when possible. Unsupported formats return an error message.

## D. Search behaviour and limits

The search tool has rate limits to prevent abuse:

- Default: 10 queries per minute
- Burst limit: 3 simultaneous queries
- Use `MAX_RESULTS` to reduce bandwidth on large result sets

## E. Reasoning switch and questions without the web

Some questions don't need the web. The reasoning mode answers from training data. This is faster and cheaper since it skips search entirely.

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
