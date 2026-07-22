---
name: agent-browser
description: Instructions for using the agent_browser tool as exposed by Pi. Read before opening, reading, or interacting with web pages.
---

# Pi agent_browser

Use Pi's `agent_browser` tool, not the `agent-browser` CLI. The tool
schema is authoritative; don't use CLI syntax from memory.


## Session: never send it

Pi manages the browser session. Never send `session`, `sessionName`,
`--session`, or `--session-name`.


## Commands

| Goal | args |
|---|---|
| Open a page | `["open", "<url>"]` |
| Whole/main page text | `["get", "text", "body"]` |
| Page title | `["get", "title"]` |
| List links, buttons, inputs | `["snapshot", "-i"]` |
| Text of one element | `["get", "text", "@eN"]` (ref from the latest snapshot) |

`get text` always needs a selector. Never guess selectors like
`#content`, `main`, or `h1`; use `body` or an `@eN` ref you've seen.


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

Read the error and `nextActions`. Don't repeat the failed call or
invent selectors or refs. For stale or unknown refs, run
`["snapshot", "-i"]`. If the goal is page text, use
`["get", "text", "body"]`.
