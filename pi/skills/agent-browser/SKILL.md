---
name: agent-browser
description: Instructions for using the agent_browser tool as exposed by Pi. Read before opening, reading, or interacting with web pages.
---

# Pi agent_browser

Use Pi's `agent_browser` tool, not the `agent-browser` CLI. The tool
schema is authoritative; don't use CLI syntax from memory.


## Session: never send it

Never include a session ID in prompts or tool calls. The platform
manages sessions automatically — sending one will cause errors.


## Commands

The main commands are:

- **open** — Navigate to a URL. Accepts `["open", "https://example.com"]`.
- **get** — Retrieve page content. Use `["get", "text", "body"]` for body text,
  `["get", "url"]` for the current URL.
- **snapshot** — Capture the interactive DOM. Use `["snapshot", "-i"]` to get
  actionable element refs (`@eN`). This is how you find clickable elements.
- **fill / press** — Interact with forms. Use `["fill", "@eN", "value"]` to set
  input values, then `["press", "Enter"]` or click submit buttons from the snapshot.
- **eval** — Run JavaScript in the page context. Useful for extracting specific data.


## Workflow

1. **Read a page:** `open` the URL, then `get text body`. For large pages, use
   `offset` and `limit` parameters to paginate through content.
2. **Interact with a page:** `snapshot -i` to get element refs, then use those
   `@eN` refs for fill/press/click operations. Don't guess selectors — always
   snapshot first.
3. **Search on sites:** Use the site: filter in search queries (e.g.,
   `site:github.com`). For GitHub issues/PRs, append `?q=<keywords>` to the URL.
4. **Extract data:** When snippets answer, use them directly. Only snapshot or
   eval when you need interactive elements or specific DOM data.


## CAPTCHAs

If a CAPTCHA appears:
- Report it to the user — do not attempt to solve it yourself.
- Try refreshing the page once; sometimes CAPTCHAs are transient.
- If it persists, note it in your response and suggest the user try manually.


## Errors

Common error handling:

- **"open" timed out:** Run `["get", "url"]` to check if you're already on the
  page. If the URL matches, continue. If it redirected, retry `open` once.
- **"Full page text" output:** The tool returns a local file path with the page
  content. Read that file with `offset/limit` instead of fetching the page again.
- **Blank or empty page:** Retry the `open` once. If still blank, report it.
- **Login walls / paywalls:** Note them and report to the user. Don't attempt
  to bypass authentication mechanisms.
