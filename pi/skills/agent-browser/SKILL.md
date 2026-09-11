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

Reading (summarize, extract, answer questions about a page):
1. `["open", "<url>"]`
2. `["get", "text", "body"]`

No snapshot. It adds tokens and doesn't shorten the text.

Interacting (click, fill, navigate, find a link):
1. `["open", "<url>"]`
2. `["snapshot", "-i"]`
3. Act using `@eN` refs from that snapshot.
4. After navigation, scrolling, or page changes, snapshot again
   before reusing refs.

Snapshot only when the task needs refs, or when the user asks for one.

Searching on sites like Google Scholar (`scholar.google.com`):
- Open the site's homepage first (e.g. `["open", "https://scholar.google.com/"]`) instead of navigating directly to a pre-built search query URL (like `/scholar?q=...`).
- Run `["snapshot", "-i"]`, `["fill", "@eN", "<query>"]` in the search input, and submit with `["press", "Enter"]` (or click the search button), then read results with `["get", "text", "body"]`.


## CAPTCHAs

When a page has a CAPTCHA, `open`, `click`, and `press` wait for it to be resolved before returning:
- If the result says the CAPTCHA is resolved, do **not** click the CAPTCHA checkbox or re-open the page. Leftover `reCAPTCHA` iframes, `checked=true` checkboxes, or disabled challenge buttons in snapshots mean it is already solved. Read the page with `["get", "text", "body"]` or continue your task.
- If you previously saw a CAPTCHA and on the next page read/snapshot it is gone, the page redirected to the real destination — read the current page with `["get", "text", "body"]` instead of re-opening the old URL.
- If a result reports an unsolved Cloudflare Turnstile / managed challenge, do **not** retry, click the widget, or switch to plain HTTP `read` — stop and inform the user so they can complete verification manually in a visible browser (or pick another source).


## Errors

Read the error and `nextActions`. Don't repeat the failed call or
invent selectors or refs. For stale or unknown refs, run
`["snapshot", "-i"]`. If the goal is page text, use
`["get", "text", "body"]`.
