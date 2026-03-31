import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
// Word rules in 35 languages (generated from lang/phrases.json).
import {
  ACTION_START, BOT_CHECK, ERROR, FROM_MEMORY, LATEST, LOOKUP, NAMED_SOURCE, OFFER, has as hasWords, namedEngine,
} from "./lang-words.ts";

const DEFAULT_SELECTOR = "body";

// Change to "block" if in-place rewrites don't take effect in your Pi version.
const MODE: "rewrite" | "block" = "rewrite";

// For `get text`, allow only "body" or @eN refs. Set false if you
// want the model to be able to use real CSS selectors.
const ONLY_REFS_OR_BODY = true;

// Remove pi-root-... session names from tool results before the model sees them.
// Set false if it causes problems.
const SCRUB_SESSION_FROM_RESULTS = true;

// When a big result is saved to a .json file, also save the page text as a
// plain .txt file (real line breaks, no metadata) and point the model to that.
// Pi's read tool can't show the .json: the text is one 100KB+ line.
const CONVERT_SPILLS = true;
const WRAP_AT = 400; // max characters per line in the .txt

// Redirect other page-reading tools (e.g. web_read from other packages) to
// agent_browser, so pages are always read the same way. Empty to allow them.
// agent_browser_tools turns on extra tools (action, qa, ...) that the model misuses.
const REDIRECT_READ_TOOLS = ["web_read", "agent_browser_code", "agent_browser_tools", "agent_browser_action", "agent_browser_qa"];

// Max web_search calls per task (reset on each new message from you).
// Set 0 to disable.
const MAX_SEARCHES = 20;
// Max similar searches on the same topic in one task (stops rephrasing loops
// while allowing multi-topic research). Set 0 to disable.
const MAX_TOPIC_SEARCHES = 2;
// Max consecutive web_search calls without opening a page or reading docs in
// between (resets on agent_browser / gitmcp_ / context7_). Set 0 to disable.
const MAX_CONSECUTIVE_SEARCHES = 8;

// Max tool calls per task, then the model must answer with what it has. Stops runaway
// loops that would otherwise hit a timeout with no answer. 0 to disable.
const MAX_TOOL_CALLS = 30;
// Only these count toward MAX_TOOL_CALLS. Local work (read, edit, grep, bash...)
// isn't capped: a coding task can legitimately need many steps.
const WEB_TOOLS = new Set(["web_search", "agent_browser"]);
// 37. Docs tools from pi-mcp-adapter (pi/mcp-adapter.json), named <server>_<tool>.
const DOCS_TOOL_RE = /^(?:context7|gitmcp)_/;

// 34. Tools that bring in the web: the finish check's source rules apply only after one of them.
// 47. Ways around a stopped search gateway in bash (run 9's F1 went to the browser instead).
const SEARCH_AROUND_RE = /\b(?:curl|wget|lynx|w3m|links|elinks|http(?:ie)?|xh)\b|https?:\/\//i;
// 49. Installing software on the person's computer (run 9's J1 tried npm -g, pip and pi install for a missing tool).
const SYSTEM_INSTALL_RE = new RegExp([
  String.raw`\b(?:npm|pnpm|bun)\s+(?:i|install|add)\b[^|;&]*\s(?:-g|--global)\b`, String.raw`\byarn\s+global\s+add\b`,
  String.raw`\bpip3?\s+install\b[^|;&]*--(?:user|break-system-packages)\b`, String.raw`\b(?:pipx|cargo|go|brew|pi)\s+install\b`,
  String.raw`(?:^|[;&|]\s*)sudo\s`, String.raw`\bpacman\s+-[SU]`, String.raw`\b(?:apt|apt-get|dnf|yum|zypper)\s+install\b`,
  String.raw`\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b`,
].join("|"), "i");
const INSTALL_ASK_RE = /\binstal|\bsetup\b|\bset up\b/i;
// 51. A file the system protects (read-only, mounted, not ours) stays: run 10's K4 tried umount after rm failed.
const MOUNT_RE = /(?:^|[;&|(]\s*|\s)(?:u?mount|fusermount3?|chattr|setfacl)\s/;
const MOUNT_ASK_RE = /\b(?:un|u)?mount|démont|desmont|smont|chattr|setfacl/i;
const FILE_CHANGE_RE = /\b(?:rm|mv|unlink|truncate|shred|chmod|chown|cp|tee|sed\s+-i)\b|>/;
const PROTECTED_RE = /busy|occup|read-only|lecture seule|nur lesbar|sólo lectura|solo lectura|permission denied|permission non accordée|operation not permitted|opération non permise|keine berechtigung|not permitted|non permise|denied/i;
// 52. Tests that fail: fix the code, not the request (run 10's K1 made --timeout default to None to pass its test).
const TEST_RUN_RE = /\b(?:unittest|pytest|tox|nox|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test|vitest|jest|mocha|cargo\s+test|go\s+test|dotnet\s+test|mvn\s+test|gradle\w*\s+test|ctest|make\s+(?:check|test))\b/;
// 48. The person asks for a plan (English, Spanish, French, German, Italian, Portuguese).
const PLAN_ASK_RE = /\b(?:plan(?:s|ned|ning)?|planea\w*|planifi\w*|planej\w*|pianific\w*|planen)\b/i;
const WEB_TOOL_RE = /^(?:web_search|agent_browser|(?:context7|gitmcp)_.*)$/;

const isWebTool = (name: string) => WEB_TOOLS.has(name) || DOCS_TOOL_RE.test(name);
// Context7's free calls are limited: at most this many per question (cached answers don't count).
const MAX_CONTEXT7_CALLS = 3;
// resolve-library-id results are reused for this long (library IDs rarely change).
// PI_CONTEXT7_ID_CACHE: another file, for tests (tests/guard-tests.mjs).
const CONTEXT7_ID_CACHE = process.env.PI_CONTEXT7_ID_CACHE || path.join(os.homedir(), ".pi", "agent", "context7-ids.json");
const CONTEXT7_ID_DAYS = 30;
const CONTEXT7_ID_MAX_CHARS = 6000;

function readIdCache(): Record<string, { t: number; text: string }> {
  try {
    const raw = fs.readFileSync(CONTEXT7_ID_CACHE, "utf-8");
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function cachedLibraryIds(name: string): string | undefined {
  const cache = readIdCache();
  const entry = cache[name];
  if (!entry) return undefined;
  if (Date.now() - entry.t > CONTEXT7_ID_DAYS * 24 * 60 * 60 * 1000) {
    delete cache[name];
    try { fs.writeFileSync(CONTEXT7_ID_CACHE, JSON.stringify(cache, null, 2)); } catch {}
    return undefined;
  }
  return entry.text.length > CONTEXT7_ID_MAX_CHARS ? undefined : entry.text;
}

function saveLibraryIds(name: string, text: string) {
  const cache = readIdCache();
  cache[name] = { t: Date.now(), text };
  try { fs.writeFileSync(CONTEXT7_ID_CACHE, JSON.stringify(cache, null, 2)); } catch {}
}

const libraryKey = (input: any) => String(input?.libraryName ?? "").trim().toLowerCase();

// --- Manifest detection ---
const MANIFEST_RE = /^(?:package\.json|.*\.csproj|.*\.fsproj|pom\.xml|build\.gradle(?:\.kts)?|pyproject\.toml|requirements.*\.txt|Cargo\.toml|go\.mod|Gemfile|composer\.json)$/;
const VERSION_IN_TEXT_RE = /(?:\bv\d+|\b\d+\.\d+(?:\.\d+)?\b|\bversion\s*\d+|[A-Za-z][\w.+#-]*\s\d{1,3}\b)/i;

function hasManifest(dir: string): boolean {
  try {
    return fs.readdirSync(dir).some(f => MANIFEST_RE.test(f));
  } catch {
    return false;
  }
}

// --- File cache management ---
const MAX_SAVED_FILES = 120;
const KEEP_RECENT_MS = 10 * 60 * 1000;
