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
  try { return JSON.parse(fs.readFileSync(CONTEXT7_ID_CACHE, "utf8")) ?? {}; } catch { return {}; }
}
function cachedLibraryIds(name: string): string | undefined {
  const hit = readIdCache()[name];
  return hit && Date.now() - hit.t < CONTEXT7_ID_DAYS * 86400_000 ? hit.text : undefined;
}
function saveLibraryIds(name: string, text: string) {
  const cache = readIdCache();
  for (const [k, v] of Object.entries(cache)) if (Date.now() - v.t >= CONTEXT7_ID_DAYS * 86400_000) delete cache[k];
  cache[name] = { t: Date.now(), text: text.slice(0, CONTEXT7_ID_MAX_CHARS) };
  try { fs.writeFileSync(CONTEXT7_ID_CACHE, JSON.stringify(cache)); } catch {}
}
const libraryKey = (input: any) => String(input?.libraryName ?? "").trim().toLowerCase();
// Context7 goes first only for a pinned version: a project file in the folder, or a version in the user's message,
// and not a question about the latest docs. Otherwise GitMCP or the official docs are tried first (they're free and live).
const MANIFEST_RE = /^(?:package\.json|.*\.csproj|.*\.fsproj|pom\.xml|build\.gradle(?:\.kts)?|pyproject\.toml|requirements.*\.txt|Cargo\.toml|go\.mod|Gemfile|composer\.json)$/;
// "v14", "14.2.3", "version 14", "Next.js 14", ".NET 8", "Spring Boot 3"
const VERSION_IN_TEXT_RE = /(?:\bv\d+|\b\d+\.\d+(?:\.\d+)?\b|\bversion\s*\d+|[A-Za-z][\w.+#-]*\s\d{1,3}\b)/i;
function hasManifest(dir: string): boolean {
  try { return fs.readdirSync(dir).some((f) => MANIFEST_RE.test(f)); } catch { return false; }
}

// Saved pages to keep. pi-sandbox expands the read glob for these files into one
// shell argument, and Linux caps that at 128 KB: past about 300 files, every bash
// call fails with "spawn E2BIG". Files younger than KEEP_RECENT_MS always stay.
const MAX_SAVED_FILES = 120;
const KEEP_RECENT_MS = 10 * 60 * 1000;

// !bangs the model knows from SearXNG, searched as site: filters instead. web_search goes
// through the search gateway, whose services don't understand bangs.
// Other bangs (engines like !go, !ddg, or invented ones) are removed.
const BANG_SITES: Record<string, string> = {
  gh: "github.com",
  st: "stackoverflow.com",
  su: "superuser.com",
  mdn: "developer.mozilla.org",
  man: "man.archlinux.org",
  dh: "hub.docker.com",
  npm: "npmjs.com",
  pypi: "pypi.org",
  al: "wiki.archlinux.org",
  ge: "wiki.gentoo.org",
  ubuntu: "askubuntu.com",
  wp: "wikipedia.org",
  wd: "wikipedia.org",
  arx: "arxiv.org",
  se: "semanticscholar.org",
  pub: "pubmed.ncbi.nlm.nih.gov",
};

// web_search fields other tools have and the model sometimes copies; this web_search takes only query and numResults.
const INVENTED_SEARCH_FIELDS = ["backend", "combine", "compact", "engine", "engines", "categories"];

// A search engine's results page. Searches go through web_search, not the browser.
const SEARCH_PAGES: Array<[RegExp, string]> = [
  [/^https?:\/\/(?:www\.)?google\.[a-z.]+\/search\b/i, "google"],
  [/^https?:\/\/(?:www\.)?bing\.com\/search\b/i, "bing"],
  [/^https?:\/\/search\.brave\.com\/search\b/i, "brave"],
  [/^https?:\/\/(?:www\.|html\.|lite\.)?duckduckgo\.com\/(?:html\/?|lite\/?)?\?/i, "duckduckgo"],
  [/^https?:\/\/(?:[a-z]+\.)?search\.yahoo\.com\/search\b/i, "yahoo"],
  [/^https?:\/\/(?:www\.)?mojeek\.com\/search\b/i, "mojeek"],
  [/^https?:\/\/(?:www\.)?startpage\.com\/(?:do\/|sp\/)?search\b/i, "startpage"],
  [/^https?:\/\/(?:www\.)?ecosia\.org\/search\b/i, "ecosia"],
  [/^https?:\/\/(?:www\.)?qwant\.com\/\?/i, "qwant"],
  [/^https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):(?:8080|8888|8090)\/search\b/i, "searxng"],
];

// 46. A search engine's home page (to type into its search box): web_search instead.
const SEARCH_HOMES: Array<[RegExp, string, string]> = [
  [/^https?:\/\/(?:www\.)?google\.[a-z.]+\/?(?:[?#].*)?$/i, "google", "https://www.google.com/search?q="],
  [/^https?:\/\/(?:www\.)?bing\.com\/?(?:[?#].*)?$/i, "bing", "https://www.bing.com/search?q="],
  [/^https?:\/\/search\.brave\.com\/?(?:[?#].*)?$/i, "brave", "https://search.brave.com/search?q="],
  [/^https?:\/\/(?:www\.)?duckduckgo\.com\/?$/i, "duckduckgo", "https://html.duckduckgo.com/html/?q="],
  [/^https?:\/\/(?:www\.)?mojeek\.com\/?(?:[?#].*)?$/i, "mojeek", "https://www.mojeek.com/search?q="],
  [/^https?:\/\/(?:www\.)?startpage\.com\/?(?:[?#].*)?$/i, "startpage", "https://www.startpage.com/do/search?q="],
  [/^https?:\/\/(?:www\.)?ecosia\.org\/?(?:[?#].*)?$/i, "ecosia", "https://www.ecosia.org/search?q="],
  [/^https?:\/\/(?:www\.)?yahoo\.com\/?$|^https?:\/\/search\.yahoo\.com\/?$/i, "yahoo", "https://search.yahoo.com/search?p="],
];

// Text that marks a bot-protection / challenge page: names and codes here, the words in every language in BOT_CHECK.
const BOT_CHECK_NAMES_RE = /Anubis|cf-chl|Attention Required! \| Cloudflare|DDoS protection by/i;
const botCheckText = (text: string) => BOT_CHECK_NAMES_RE.test(text) || hasWords(BOT_CHECK, text);

const PDF_URL_RE = /^https?:\/\/(?:\S+\.pdf(?:[?#]\S*)?|(?:\S+\.)?arxiv\.org\/pdf\/\S+)$/i;
const MAX_PDF_BYTES = 50 * 1024 * 1024;

// Tries of one identical bash call before it's blocked.
const MAX_IDENTICAL_CALLS = 2;

// The user's words and the model's answer are checked with lang-words.ts: NAMED_SOURCE (the user named a source to
// read, so snippets aren't enough), ERROR (asked for a fix), LOOKUP, ACTION_START, namedEngine ("Search Mojeek for
// x": the answer has to say what happened with that engine), and in the answer FROM_MEMORY and OFFER (a closing offer,
// in the last lines).
const COMMANDS_RE = /```|\bsudo\s/;
const MAX_SOURCE_CHARS = 8 * 1024 * 1024;
// The model repeated a tool's status line instead of answering.
const TOOL_ECHO_RE = /^\{[^\n]*"(?:success|resultCategory)"\s*:[^\n]*\}$/;
// An answer that repeats these extensions' instructions instead of following them (run 10's F2 answered with the
// CAPTCHA message: "...Search with web_search instead...").
const GUARD_ECHO_RE = /Your next step is a tool call|Search with web_search instead|Start your answer by saying|^Not (?:run|sent to Context7):/m;
// Most check messages per user message, not counting an empty answer.
const MAX_FINISH_CHECKS = 3;
// 45. Refused repeats of the same call in one task before every tool call is refused.
const MAX_REFUSED_REPEATS = 3;

// Finish-check messages reach the model as user messages. This tag keeps them
// from counting as a new task, and tells the model nobody said them.
const FINISH_TAG = "[Automatic check, not from the user.";

// What the person sent, from Pi's `input` event: typed text, `pi -p`, RPC, or an extension's sendUserMessage (the
// Plannotator Inbox delivers the person's replies that way). Hidden messages other extensions add (the Inbox reminder,
// the end-of-answer checks) reach the model as user messages too, but never fire `input`. An Inbox reply's first line
// ("Plannotator Inbox: <title> (<id>)") is the model's own thread title, so it is dropped.
export function personText(text: string): string {
  const t = String(text ?? "");
  return t.startsWith("Plannotator Inbox: ") ? t.slice(t.indexOf("\n") + 1 || t.length) : t;
}
const FINISH_NOTE =
  `${FINISH_TAG} The user can't see this check or your earlier reply. The answer you write next is the only ` +
  "reply they will read, so make it complete, as if for the first time. Start it with a line saying only " +
  "ANSWER: and put nothing before that line.]";
const FINISH_NOTE_UI =
  `${FINISH_TAG} The user already sees your previous reply above, so do NOT repeat the whole answer. ` +
  "Write only a brief, clear correction or addendum.]";

// After a check: the marker line, and the talk about corrections that the model may still write first.
const ANSWER_MARK_RE = /^[*_#\s]*ANSWER:[*_]*[ \t]*\n*/m;
const CORRECTION_TALK_RE =
  /\b(?:you(?:'|’)re right|you are right|the user\b|thank you for|my (?:previous|earlier|first|second|last) (?:answer|reply|response|attempt)|previous (?:answer|reply)|i was wrong|i apologi[sz]e|sorry|corrected (?:answer|version|comparison)|i(?:'|’)ve now read|the claim that|this check|re-?check|let me (?:re|correct|fix|verify))/i;
const MAX_TALK_CHARS = 500;

function cleanCheckedAnswer(text: string): string {
  const mark = text.match(ANSWER_MARK_RE);
  if (mark && mark.index !== undefined) return text.slice(mark.index + mark[0].length).trim();
  const paragraphs = text.split(/\n\s*\n/);
  while (paragraphs.length > 1 && paragraphs[0].length <= MAX_TALK_CHARS &&
         (CORRECTION_TALK_RE.test(paragraphs[0]) || /^\s*(?:---+|\*\*\*+)\s*$/.test(paragraphs[0]))) {
    paragraphs.shift();
  }
  return paragraphs.join("\n\n");
}

// URLs match if seen verbatim, modulo trailing slash / www. prefix, as the root homepage of a seen host,
// or as a child page below a read page named by text on that page (.../releases/tag/v1.2).
function urlInSources(lower: string, flat: string, has: (needle: string, inText: string) => boolean): boolean {
  if (flat.includes(lower)) return true;
  const noSlash = lower.replace(/\/+$/, "");
  if (noSlash !== lower && flat.includes(noSlash)) return true;
  const wwwAlt = noSlash.replace(/^(https?:\/\/)(www\.)?/, (_, proto, www) => proto + (www ? "" : "www."));
  if (flat.includes(wwwAlt)) return true;
  const root = noSlash.match(/^https?:\/\/(?:www\.)?([^/?#]+)$/);
  if (root) {
    const escaped = root[1].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (new RegExp(`https?://(?:www\\.)?${escaped}(?=[/?#\\s"'<>\\)\\]]|$)`).test(flat)) return true;
  }
  const parent = lower.split(/[?#]/)[0].split("/");
  const last = parent.pop() ?? "";
  const grand = parent.slice(0, -1).join("/");
  return Boolean(last && flat.includes(grand) && parent.length > 4 && has(last, flat));
}

// Parts of an answer that should be findable in what was read this task.
function unsupportedClaims(answer: string, sources: string): string[] {
  const missing = new Set<string>();
  const flat = sources.toLowerCase();
  const digitsOnly = flat.replace(/(\d)[,_ ](?=\d{3}\b)/g, "$1");
  const has = (needle: string, inText: string) =>
    new RegExp(`(?<![\\w.])${needle.replace(/[.*+?^\${}()|[\]\\]/g, "\\$&")}(?![\\w])`).test(inText);

  // URLs first, so their digits and dashes aren't checked again.
  let rest = answer;
  for (const raw of answer.match(/https?:\/\/[^\s)\]>"'`]+/g) ?? []) {
    const url = raw.replace(/[.,;:*]+$/, "");
    const lower = url.toLowerCase();
    // Placeholder hosts in code examples (example.com/.org/.net, localhost) claim nothing (run 5's A2).
    const placeholder = /^https?:\/\/(?:[\w-]+\.)*(?:example\.(?:com|org|net)|localhost)(?=[:/?#]|$)/.test(lower);
    if (!placeholder && !urlInSources(lower, flat, has)) missing.add(url);
    rest = rest.split(raw).join(" ");
  }

  // Shell commands in code blocks.
  for (const block of rest.matchAll(/```(\w*)\n([\s\S]*?)```/g)) {
    if (block[1] && !/^(?:bash|sh|shell|console|zsh)$/i.test(block[1])) continue;
    for (const raw of block[2].split("\n")) {
      if (/unverified/i.test(raw)) continue;
      const line = raw.replace(/^\s*[#$]\s*/, "").replace(/\s+#.*$/, "").replace(/^sudo\s+/, "").trim();
      if (!/^[a-z][\w.-]*\s+\S/.test(line) || /[=(]/.test(line.split(" ")[0])) continue;
      const core = line.split(/\s*<|\s*&&/)[0].trim().toLowerCase();
      if (core && !flat.replace(/\s+/g, " ").includes(core.replace(/\s+/g, " "))) missing.add(line);
    }
  }
  const prose = rest.replace(/```[\s\S]*?```/g, " ");

  // Command-line flags.
  for (const m of prose.matchAll(/(?:^|[\s`(]|(?<![\w*])\*+)(--?[A-Za-z][\w-]*)(?=[\s`*:),.]|$)/gm)) {
    if (!has(m[1].toLowerCase(), flat)) missing.add(m[1]);
  }

  // Numbers with three or more digits (years, sizes, versions).
  for (const m of prose.matchAll(/(?<![\w.-])\d[\d,.]*\d(?![\w-])/g)) {
    const token = m[0].replace(/\.$/, "");
    const plain = token.replace(/,/g, "");
    if (plain.replace(/\./g, "").length < 3) continue;
    if (!has(token, flat) && !has(plain, digitsOnly)) missing.add(token);
  }

  // Sizes like 256k: copied as written, not converted.
  for (const m of prose.matchAll(/(?<![\w.-])\d+\s?[kKmM]\b/g)) {
    const compact = m[0].replace(/\s/g, "").toLowerCase();
    const digits = compact.slice(0, -1);
    const zeros = compact.endsWith("k") ? 3 : 6;
    // A rounded 262,144 written as 262k is fine; 256k isn't anywhere.
    const rounded = new RegExp(`(?<![\\w.])${digits}\\d{${zeros}}(?!\\w)`).test(digitsOnly);
    if (!rounded && !has(compact, flat.replace(/(\d) ([km])\b/g, "$1$2"))) missing.add(m[0]);
  }
  return [...missing].slice(0, 8);
}

// Challenge pages are tiny (about 350 characters on the Arch Wiki). Longer
// results that look like a bot check are real pages that mention those words.
const BOT_CHECK_MAX_CHARS = 1500;

// Block curl/wget to websites in bash; pages go through agent_browser.
// API calls and local endpoints are allowed.
const BLOCK_CURL = true;
const API_URL_RE =
  /https?:\/\/(?:api\.|localhost\b|127\.0\.0\.1\b|0\.0\.0\.0\b|\[::1\]|192\.168\.|10\.)|https?:\/\/[^\s"'`|;&]+(?:\/(?:api|graphql)(?:[/?#]|$)|\/v[1-9]\d*\/|\.(?:json|xml|ya?ml)(?:[?#]|$))/i;
const API_CURL_FLAG_RE =
  /(?:^|\s)(?:-X\s*(?:POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b|--request(?:=|\s+)(?:POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b|-d(?:\s|$)|--data(?:-(?:raw|binary|urlencode|ascii))?(?:=|\s+)|--json(?:=|\s+))|(?:Accept|Content-Type)\s*:\s*application\/(?:json|vnd\.[^"'\s;]+|xml|graphql)|Authorization\s*:/i;
const isApiCurl = (cmd: string): boolean => API_URL_RE.test(cmd) || API_CURL_FLAG_RE.test(cmd);

// Valid `get` subcommands, from agent-browser's own error message.
const GET_SUBCOMMANDS = new Set([
  "text", "html", "value", "attr", "url", "title", "count", "box", "styles", "cdp-url",
]);

// Commands the model uses most; only used to decide whether a stray leading
// flag can safely be dropped.
const COMMON_COMMANDS = new Set([
  "open", "snapshot", "get", "click", "fill", "type", "press", "scroll",
  "screenshot", "close", "wait", "select", "find", "back", "forward", "reload",
]);

const SESSION_FLAGS = new Set(["--session", "--session-name"]);
const SESSION_FIELDS = ["session", "sessionName"];

const SESSIONS_DIR = path.join(os.homedir(), ".pi", "agent", "sessions");
const ARTIFACTS_DIR_NAME = ".pi-agent-browser-artifacts";
const OUTPUT_FILE_RE = /^pi-agent-browser-(?:output|snapshot)-[A-Za-z0-9]+\.(json|txt)$/;

// A saved file's real name, without the leading dot the model sometimes adds.
function savedName(file: string): string {
  return path.basename(file).replace(/^\./, "");
}

// All agent-browser output files, newest first.
function listArtifactFiles(): { file: string; mtime: number }[] {
  const found: { file: string; mtime: number }[] = [];
  const safeList = (dir: string) => {
    try { return fs.readdirSync(dir); } catch { return []; }
  };
  for (const session of safeList(SESSIONS_DIR)) {
    const artifacts = path.join(SESSIONS_DIR, session, ARTIFACTS_DIR_NAME);
    for (const run of safeList(artifacts)) {
      const runDir = path.join(artifacts, run);
      for (const name of safeList(runDir)) {
        if (!OUTPUT_FILE_RE.test(name)) continue;
        const file = path.join(runDir, name);
        try { found.push({ file, mtime: fs.statSync(file).mtimeMs }); } catch {}
      }
    }
  }
  return found.sort((a, b) => b.mtime - a.mtime);
}

// Deletes the oldest saved pages beyond MAX_SAVED_FILES. They are only copies of
// web pages, used within the task that fetched them.
function pruneSavedPages() {
  const list = (dir: string) => { try { return fs.readdirSync(dir); } catch { return []; } };
  const files: { file: string; mtime: number }[] = [];
  const runDirs: string[] = [];
  for (const session of list(SESSIONS_DIR)) {
    const artifacts = path.join(SESSIONS_DIR, session, ARTIFACTS_DIR_NAME);
    for (const run of list(artifacts)) {
      const runDir = path.join(artifacts, run);
      runDirs.push(runDir);
      for (const name of list(runDir)) {
        const file = path.join(runDir, name);
        try { files.push({ file, mtime: fs.statSync(file).mtimeMs }); } catch {}
      }
    }
  }
  files.sort((a, b) => b.mtime - a.mtime);
  const cutoff = Date.now() - KEEP_RECENT_MS;
  for (const { file, mtime } of files.slice(MAX_SAVED_FILES)) {
    if (mtime < cutoff) { try { fs.unlinkSync(file); } catch {} }
  }
  for (const dir of runDirs) { try { fs.rmdirSync(dir); } catch {} } // only succeeds if empty
}

function repairArtifactPath(input: any): any {
  const key = typeof input?.path === "string" ? "path"
            : typeof input?.file_path === "string" ? "file_path"
            : undefined;
  if (!key) return;

  const requested: string = input[key];
  // The model sometimes drops the artifacts folder and writes ".pi-agent-browser-output-x.txt".
  if (!requested.includes(ARTIFACTS_DIR_NAME) && !OUTPUT_FILE_RE.test(savedName(requested))) return;

  const expanded = requested.startsWith("~/") ? path.join(os.homedir(), requested.slice(2)) : requested;

  // Reading the .json when a plain-text copy exists: read the .txt instead.
  const txtSibling = expanded.replace(/\.json$/, ".txt");
  if (txtSibling !== expanded && fs.existsSync(txtSibling)) {
    if (MODE === "rewrite") { input[key] = txtSibling; return; }
    return { block: true, reason: `Read the plain-text version instead: ${txtSibling}` };
  }
  if (fs.existsSync(expanded)) return;

  const files = listArtifactFiles();
  const wanted = savedName(expanded);

  // Same file name, wrong folder (the usual case: a mangled ID in the path).
  let match = files.find((f) => path.basename(f.file) === wanted);

  // File name mangled too: fall back to the newest output from the last 10 minutes.
  if (!match && files.length && Date.now() - files[0].mtime < 10 * 60 * 1000) {
    match = files[0];
  }

  if (!match) {
    return {
      block: true,
      reason: `No such agent-browser output file. Copy the exact path from the tool result, or fetch the page again.`,
    };
  }

  if (MODE === "rewrite") {
    input[key] = match.file;
    return;
  }
  return { block: true, reason: `Wrong path. The file is at: ${match.file}` };
}

function stripSessionArgs(args: string[]): boolean {
  let changed = false;
  for (let i = 0; i < args.length; ) {
    const arg = args[i];
    if (SESSION_FLAGS.has(arg)) {
      args.splice(i, 2); // flag + its value
      changed = true;
    } else if ([...SESSION_FLAGS].some((f) => arg.startsWith(f + "="))) {
      args.splice(i, 1); // --session=value
      changed = true;
    } else {
      i++;
    }
  }
  return changed;
}

export default function (pi: any) {
  // Hide redirected tools from the model entirely, so it isn't tempted to
  // call them. The tool_call block below stays as a backup.
  pi.on("session_start", async () => {
    pruneSavedPages();
    if (typeof pi.getActiveTools !== "function" || typeof pi.setActiveTools !== "function") return;
    const active: string[] = pi.getActiveTools();
    const kept = active.filter((name) => !REDIRECT_READ_TOOLS.includes(name));
    if (kept.length !== active.length) pi.setActiveTools(kept);
  });

  // 14. Search budget, counted per task.
  let searches = 0;
  let consecutiveSearches = 0;
  let searchTopics: { queries: Set<string>[] }[] = [];
  let openedPage = false;
  let urlHintGiven = false;
  let toolCalls = 0;
  let callCounts = new Map<string, number>();
  let refusedRepeats = 0;
  // 47. web_search said the search gateway isn't running: no searching another way until the next message.
  let searchDown = false;
  let context7Calls = 0;
  let docsQueries = new Set<string>();
  let freeDocsTried = false;
  let context7OrderHints = 0;
  const resetBudget = () => {
    searches = 0; consecutiveSearches = 0; searchTopics = []; urlHintGiven = false; toolCalls = 0; callCounts = new Map(); refusedRepeats = 0; searchDown = false;
    context7Calls = 0; docsQueries = new Set(); freeDocsTried = false; context7OrderHints = 0;
  };
  // 34. Finish check: per message, not per run, because a continuation starts a new run.
  let finishAsked = new Set<string>();
  let readPage = false;
  let sources = "";
  const addSource = (text: unknown) => {
    if (typeof text === "string" && sources.length < MAX_SOURCE_CHARS) sources += `\n${text}`;
  };
  let lastAssistant: any;
  let lastPdf: { url: string; path: string } | undefined;
  // 34. The source checks are for answers from the web: with only files read (a code-finder's line numbers), skip them.
  let usedWeb = false;
  // 48. A plan goes to PLAN.md and the Inbox (AGENTS.md, "Plans"); run 9's J1 wrote it in chat only.
  let planWritten = false;
  let planSent = false;
  // 24. The note that results for a named engine come from Search Gateway: once per message.
  let engineNoteGiven = false;
  // edit-guard.ts blocked a file change this turn and told the model to ask whether to apply it: skip grounding/offer checks.
  let editBlocked = false;
  // Interactive finish-check replies are short corrections, not full rewrites to strip in message_end.
  let interactiveFinish = false;
  const newMessage = () => { engineNoteGiven = false; editBlocked = false; interactiveFinish = false; finishAsked = new Set(); openedPage = false; readPage = false; lastAssistant = undefined; sources = ""; usedWeb = false; releaseDatesAdded.clear(); issueMatchesAdded.clear(); planWritten = false; planSent = false; };
  pi.on("before_agent_start", () => { resetBudget(); newMessage(); });
  pi.on("agent_end", (event: any) => {
    pruneSavedPages();
    const assistants = (event?.messages ?? []).filter((m: any) => m?.role === "assistant");
    if (assistants.length) lastAssistant = assistants[assistants.length - 1];
  });

  // 37. After a check, the user reads only the answer, not the model's talk about the correction.
  pi.on("message_end", (event: any) => {
    const message = event?.message;
    if (message?.role !== "assistant" || !finishAsked.size || interactiveFinish || !Array.isArray(message.content)) return;
    let changed = false;
    const content = message.content.map((part: any) => {
      if (part?.type !== "text" || typeof part.text !== "string") return part;
      const text = cleanCheckedAnswer(part.text);
      if (text === part.text) return part;
      changed = true;
      return { ...part, text };
    });
    return changed ? { message: { ...message, content } } : undefined;
  });

  // 34. Last chance before the turn ends: ask once for what's missing.
  pi.on("agent_before_settle", (event: any, ctx: any) => {
    if (!lastAssistant) return;
    const hasUI = Boolean(ctx?.hasUI);
    const answer = textOf(lastAssistant.content).trim();
    let reason: string | undefined;
    let reminder = "";
    let partialRewrite = false;
    if (!answer || TOOL_ECHO_RE.test(answer)) {
      reason = "empty";
      reminder = "You ended without writing anything for the user. Write your final answer now, using what you found.";
    } else if (GUARD_ECHO_RE.test(answer)) {
      reason = "echo";
      reminder = "Your answer repeats a tool's instructions instead of following them. Do what they say now (make " +
        "the tool call they name), then write your answer from its result.";
    } else if (!readPage && hasWords(NAMED_SOURCE, lastUserText)) {
      reason = "source";
      reminder =
        'The user named a source to read, but you didn\'t read its text. Read it with agent_browser ' +
        '({"args":["read","<url>"]} or ["get","text","body"]), then answer from what it says.';
    } else if (!readPage && hasWords(ERROR, lastUserText) && COMMANDS_RE.test(answer)) {
      reason = "fix";
      reminder = "You gave commands without reading their source. Read the most relevant page with agent_browser, then answer using only the commands it gives.";
    } else if (!sources && toolCalls === 0 && hasWords(LOOKUP, lastUserText)) {
      reason = "lookup";
      reminder = "The user asked you to look this up and not to answer from memory. Look it up with your web tools first, then answer from what you find.";
    } else if (!sources && toolCalls === 0 && hasWords(ACTION_START, lastUserText) && hasWords(OFFER, answer)) {
      reason = "do-it";
      reminder = "The user asked you to do this, so don't ask first. Do it now with your tools. If the engine or site they named isn't available, say so and use another.";
    }
    // Plannotator leaves its Inbox tool off where no reply could reach the session (pi -p): PLAN.md alone then.
    const canSend = (pi.getActiveTools?.() ?? []).includes("plannotator_inbox");
    if (!reason && PLAN_ASK_RE.test(lastUserText) && !(planWritten && (planSent || !canSend))) {
      reason = "plan";
      reminder = !canSend
        ? "Plans go to PLAN.md, not only the chat. Write the plan to PLAN.md (open questions inside it). The Inbox " +
          "tool isn't on in this session: say so in one line; don't look for it or install anything."
        : planWritten
        ? 'Send the plan now: plannotator_inbox, action send_message, a one-line summary as the body, attachments ["PLAN.md"].'
        : "Plans go to the Inbox, not only the chat. Write the plan to PLAN.md (open questions inside it), then send " +
          'it: plannotator_inbox, action send_message, a one-line summary as the body, attachments ["PLAN.md"].';
    }
    if (!reason || finishAsked.has(reason)) reason = undefined;
    if (!reason && usedWeb && sources && !finishAsked.has("memory") && hasWords(FROM_MEMORY, answer)) {
      reason = "memory";
      partialRewrite = true;
      reminder = hasUI
        ? "You read sources for this, so briefly correct any parts answered from memory using what you read."
        : "You read sources for this, so answer from what you read, not from memory. Write the final answer again.";
    }
    // In interactive mode (hasUI), the user already sees the first reply: cap grounding at 1 correction so Pi never loops.
    if (!reason && usedWeb && sources && !editBlocked && !finishAsked.has(hasUI ? "grounding" : "grounding2")) {
      // The user's own message counts as a source: a value they gave ("change the port to 9090") isn't made up.
      const missing = unsupportedClaims(answer, `${sources}\n${lastUserText}`);
      if (missing.length) {
        reason = finishAsked.has("grounding") ? "grounding2" : "grounding";
        partialRewrite = true;
        const hasUrl = missing.some((m) => /^https?:\/\//i.test(m));
        const removeHint = hasUrl ? "Remove them (including any markdown links to those URLs)" : "Remove them";
        reminder = hasUI
          ? `These aren't in anything you read: ${missing.join(" | ")}. ${removeHint}, or clarify what the ` +
            "source actually says in a brief correction without repeating the whole answer."
          : `These aren't in anything you read: ${missing.join(" | ")}. ${removeHint}, or replace them with what the ` +
            "source actually says. Write the complete final answer again.";
      }
    }
    if (!reason && !hasUI && sources && !editBlocked && !finishAsked.has("offer") && hasWords(OFFER, answer.slice(-300))) {
      reason = "offer";
      reminder = "Your answer ends with an offer. Remove it, and anything about options you haven't checked. Write the complete final answer again.";
    }
    // 39. Pages were read but the answer names none of them.
    if (!reason && readPage && !finishAsked.has("links") && !/https?:\/\//.test(answer)) {
      reason = "links";
      partialRewrite = true;
      reminder = hasUI
        ? "List the pages you used, with full URLs copied from what you read ([title](https://...)). " +
          "For releases and news, include their dates. Do not repeat the whole answer."
        : "Name the pages you used, with full URLs copied from what you read ([title](https://...)). " +
          "For releases and news, include their dates. Write the complete final answer again.";
    }
    // 44. "Read/accessed on <date>": the model can't know when a page was fetched, and the date was wrong every time
    //     (run 5 and 6's D1: "accessed Nov 16, 2026", "read directly via agent_browser on 2026-11-10").
    if (!reason && !finishAsked.has("accessed") && ACCESSED_ON_RE.test(answer)) {
      reason = "accessed";
      partialRewrite = true;
      reminder = hasUI
        ? "Add a brief one-line correction noting you don't know the exact access date. Do not repeat the whole answer."
        : "Remove the date you say you read or accessed a page on: you don't know it. Keep only dates the " +
          "pages show. Write the complete final answer again.";
    }
    // 43. The answer is in another script than the person's message (run 5's K4: Chinese for an English prompt).
    const askedIn = mainScript(lastUserText, 4);  // "Delete ~/.bashrc" is a whole message
    const answeredIn = mainScript(answer);
    if (!reason && askedIn && answeredIn && askedIn !== answeredIn && !finishAsked.has("language")) {
      reason = "language";
      reminder = "Your answer isn't in the language of the person's message. Write the complete final answer again, " +
        "in the language they wrote in.";
    }
    const named = namedEngine(lastUserText);
    const engine = named && named[0].toUpperCase() + named.slice(1);
    if (!reason && engine && !finishAsked.has("engine") && !new RegExp(engine, "i").test(answer)) {
      reason = "engine";
      partialRewrite = true;
      reminder = hasUI
        ? `The user asked for ${engine}, and your answer doesn't mention it. Add a brief note saying whether these results ` +
          `really come from ${engine} and, if not, that it was blocked or unavailable and which engine you used.`
        : `The user asked for ${engine}, and your answer doesn't mention it. Begin by saying whether these results ` +
          `really come from ${engine} and, if not, that it was blocked or unavailable and which engine you used. ` +
          "Then give the results again.";
    }
    const reasoned = (lastAssistant.content ?? []).some?.((p: any) => p?.type === "thinking");
    if (!reason && !hasUI && usedWeb && sources && !editBlocked && !reasoned && !finishAsked.size) {
      reason = "review";
      reminder =
        `Re-read the question: "${lastUserText.slice(0, 300)}". If your answer doesn't directly state what was asked ` +
        "(for example it gives a different version or category), fix it. Write the complete final answer again, " +
        "not a comment about it.";
    }
    if (!reason || (reason !== "empty" && finishAsked.size >= MAX_FINISH_CHECKS)) return;
    finishAsked.add(reason);
    const note = hasUI && partialRewrite ? FINISH_NOTE_UI : FINISH_NOTE;
    if (hasUI && partialRewrite) {
      interactiveFinish = true;
      ctx?.ui?.notify?.(`Automatic check (${reason}): requesting brief correction`, "info");
    }
    return {
      entries: [...(event?.entries ?? []), { type: "custom_message", customType: "finish-check", content: `${reminder}\n\n${note}`, display: false }],
      continue: true,
    };
  });
  // Backup reset that doesn't depend on those event names: a new user
  // message in the request means a new task.
  let lastOpenedUrl: string | undefined;
  // 38. The latest snapshot's text (inline, plus the saved .txt it may point to), to tell links from buttons.
  let lastSnapshotText = "";
  let botBlockedUrl: string | undefined;
  let lastUserText = "";
  // A new message from the person starts a new task (hidden messages from extensions don't; see personText).
  pi.on("input", (event: any) => {
    resetBudget();
    newMessage();
    lastUserText = personText(event?.text);
  });

  // An agent_browser open with no page text fetched since (see rule 28: guessed paths to the page).
  let pageTextPending = false;
  pi.on("tool_call", async (event: any) => {
    if (event.toolName !== "agent_browser") return;
    const cmd = event.input?.args?.[0];
    if (cmd === "open") pageTextPending = true;
    // Only commands that return the page's text; "get url" or "get title" don't (run 7's B2).
    else if (cmd === "read" || cmd === "snapshot" || cmd === "eval" ||
             (cmd === "get" && ["text", "html"].includes(event.input?.args?.[1]))) pageTextPending = false;
  });

  // 40. Wording alone didn't stop the model ticking items before doing them (test run 4, K1). Refusing every second
  //     tick in a row (run 4's version) left items unticked when the model ticked several after the work (run 5).
  let workSinceAdd = true;
  let todoDone = new Set<number>();
  pi.on("tool_call", async (event: any) => {
    if (event.toolName === "codemode") return;
    if (event.toolName !== "todo") { workSinceAdd = true; return; }
    const action = event.input?.action;
    const id = Number(event.input?.id);
    if (action === "add") workSinceAdd = false;
    if (action === "clear") todoDone = new Set();
    if (action !== "toggle" || !Number.isFinite(id)) return;
    if (todoDone.has(id)) { todoDone.delete(id); return; }
    if (!workSinceAdd) {
      return {
        block: true,
        reason: `Not ticked: #${id} isn't done yet (nothing has been done since the list was made). Do the step ` +
          "first (edit, run, check), then tick it.",
      };
    }
    todoDone.add(id);
  });

  pi.on("tool_call", async (event: any, ctx: any) => {
    // 45. A loop: after 3 refused repeats of a call in one task, every tool call is refused until the answer. Rule 33
    //     refused each repeat, but the model sent the same ls 26 times (run 6's D4 rerun).
    if (searchDown && (event.toolName === "agent_browser" ||
        (event.toolName === "bash" && SEARCH_AROUND_RE.test(String(event.input?.command ?? ""))))) {
      return {
        block: true,
        reason: "Not run: web_search said the search gateway isn't running, so don't search another way. Tell the " +
          "user what web_search said to do (start the search gateway), and stop. Don't answer from memory.",
      };
    }
    if (event.toolName === "bash" && SYSTEM_INSTALL_RE.test(String(event.input?.command ?? "")) &&
        !INSTALL_ASK_RE.test(lastUserText)) {
      return {
        block: true,
        reason: "Not run: that installs software on the person's computer, which they didn't ask for. If something " +
          "you need is missing, say so in your answer (what's missing and how they could add it), and go on without it.",
      };
    }
    if (event.toolName === "bash" && MOUNT_RE.test(` ${String(event.input?.command ?? "")}`) && !MOUNT_ASK_RE.test(lastUserText)) {
      return {
        block: true,
        reason: "Not run: that changes how the system protects files, which the person didn't ask for. A file you " +
          "can't change is protected on purpose: tell the person you couldn't, and why, and stop.",
      };
    }
    if (refusedRepeats >= MAX_REFUSED_REPEATS) {
      return {
        block: true,
        reason: "You are repeating calls that can't give anything new. Stop calling tools and write your final answer " +
          "now with what you have; if you didn't find it, say so plainly.",
      };
    }
    // Only a call that reads docs counts; GitMCP's name-to-repo lookup reads none (run 4's H3 slipped through on it).
    if ((event.toolName.startsWith("gitmcp_") && !event.toolName.includes("owner_repo_mapping")) ||
        (event.toolName === "agent_browser" && ["open", "read"].includes(event.input?.args?.[0]))) {
      freeDocsTried = true;
    }
    if (DOCS_TOOL_RE.test(event.toolName) ||
        (event.toolName === "agent_browser" && ["open", "read", "get"].includes(event.input?.args?.[0]))) {
      consecutiveSearches = 0;
    }
    // 33. The same command again won't give a different answer. (A repeated web_search goes to a
    //     different search service instead: search-gateway.ts handles those.)
    if (event.toolName === "bash") {
      const key = `bash ${event.input?.command ?? ""}`;
      const seen = (callCounts.get(key) ?? 0) + 1;
      callCounts.set(key, seen);
      if (seen > MAX_IDENTICAL_CALLS) {
        refusedRepeats++;
        return {
          block: true,
          reason: "You already ran this exact call and the result won't change. Try something different, or answer with what you have.",
        };
      }
    }

    // 37. Context7 and GitMCP docs lookups.
    if (DOCS_TOOL_RE.test(event.toolName)) {
      // The same docs query again gives the same text, which is already above.
      const key = `${event.toolName} ${JSON.stringify(event.input ?? {})}`.toLowerCase();
      if (docsQueries.has(key)) {
        return { block: true, reason: "You already ran this exact docs lookup; its result is above. Use it, or ask something different." };
      }
      if (event.toolName === "context7_resolve-library-id") {
        const cached = libraryKey(event.input) && cachedLibraryIds(libraryKey(event.input));
        if (cached) {
          docsQueries.add(key);
          return {
            block: true,
            reason: `Not sent to Context7: these IDs for "${event.input.libraryName}" were looked up earlier (cached, still valid). ` +
              `Pick the ID for the version you need and call context7_query-docs.\n\n${cached}`,
          };
        }
      }
      // Not a pinned-version question: the free, live sources first. Refused at most twice per question, so it
      // can't loop; the message names a ready GitMCP call when the library's GitHub repo is known.
      if (event.toolName.startsWith("context7_") && !freeDocsTried && context7OrderHints < 2) {
        const pinned = (hasManifest(ctx?.cwd ?? process.cwd()) || VERSION_IN_TEXT_RE.test(lastUserText)) && !hasWords(LATEST, lastUserText);
        if (!pinned) {
          context7OrderHints++;
          // A GitHub repo for GitMCP: from the ID, or from the cached IDs of that library (a "/websites/..." ID isn't one).
          const givenId = String(event.input?.libraryId ?? "");
          const cachedText = cachedLibraryIds(libraryKey(event.input)) ??
            Object.values(readIdCache()).find((c) => givenId && c.text.includes(givenId))?.text ?? "";
          const id = [givenId, ...(cachedText.match(/\/[\w.-]+\/[\w.-]+/g) ?? [])].find((x) => /^\/(?!websites\/)[\w.-]+\/[\w.-]+/.test(x)) ?? "";
          const repo = id.match(/^\/([\w.-]+)\/([\w.-]+)/);
          const library = String(event.input?.libraryName ?? "").trim();
          const webApi = /^(?:javascript|js|typescript|ts|html|css|dom|web ?apis?|browser|fetch)$/i.test(library) ||
            (!library && !givenId);
          const query = String(event.input?.query ?? "").slice(0, 200);
          const ready = repo
            ? `Call gitmcp_search_generic_documentation with {"owner":"${repo[1]}","repo":"${repo[2]}","query":${JSON.stringify(query || "<your question>")}}.`
            : webApi
              ? `Search with web_search: {"query":${JSON.stringify((query || "<your question>") + " site:developer.mozilla.org")}}.`
              : `Search with web_search: {"query":${JSON.stringify(`${query || "<your question>"} ${library || "official docs"}`.trim())}}, ` +
                "or gitmcp_search_generic_documentation (owner, repo, query) if you know its GitHub repo.";
          return {
            block: true,
            reason: `Not sent to Context7: this question is about the current docs, not a pinned version, so use the free, live docs first. ${ready} ` +
              "Use Context7 only if that doesn't answer.",
          };
        }
      }
      if (event.toolName.startsWith("context7_") && ++context7Calls > MAX_CONTEXT7_CALLS) {
        return {
          block: true,
          reason: `Context7's limit for this question is reached (${MAX_CONTEXT7_CALLS} calls). Use what you have, ` +
            "or GitMCP, the official docs or web_search.",
        };
      }
      docsQueries.add(key);
    }

    //  30. Caps web tool calls (search, browser, docs) per task, then asks for an answer.
    //      Checked here, incremented only once a web tool call is actually allowed (so blocked calls don't burn budget).
    if (MAX_TOOL_CALLS > 0 && isWebTool(event.toolName) && toolCalls >= MAX_TOOL_CALLS) {
      return {
        block: true,
        reason:
          "You've used enough steps. Stop calling tools and answer now with what you found. " +
          "If you didn't find it, say so plainly.",
      };
    }
    if (DOCS_TOOL_RE.test(event.toolName)) toolCalls++;

    // 16. Bangs to site: filters in web_search queries.
    if (event.toolName === "web_search" && typeof event.input?.query === "string") {
      // 25. The user gave a URL: open it before searching (once, so a real
      //     need to search still can).
      const givenUrl = lastUserText.match(/https?:\/\/[^\s)\]>"'`]+/)?.[0];
      if (givenUrl && !openedPage && !urlHintGiven) {
        urlHintGiven = true;
        return {
          block: true,
          reason:
            `You were given a URL. Open it before searching: {"args":["open","${givenUrl}"]}, ` +
            `then {"args":["get","text","body"]}.`,
        };
      }
      // 19 + 26. Fields of other search tools (backend: "duckduckgo", compact...): every search goes through
      //     the search gateway.
      for (const field of INVENTED_SEARCH_FIELDS) delete event.input[field];
      event.input.query = bangsToSite(event.input.query);
      // 17. Drop years the user didn't ask for (the model adds "2024 2025" from
      //     its training data, which biases results toward old pages).
      event.input.query = dropUnaskedYears(event.input.query, lastUserText);
      // 24. "Search Mojeek for x" isn't x site:mojeek.com (that searches Mojeek's own pages: run 13's D3-es
      //     answered about pi on Mojeek's calculator page).
      const named = namedEngine(lastUserText);
      if (named) {
        const withoutEngine = event.input.query.replace(new RegExp(`(?:^|\\s)-?site:\\S*\\b${named}\\b\\S*`, "gi"), " ").replace(/\s+/g, " ").trim();
        if (withoutEngine) event.input.query = withoutEngine;
      }
    }

    if (event.toolName === "web_search") {
      if (MAX_SEARCHES > 0 && searches >= MAX_SEARCHES) {
        return {
          block: true,
          reason:
            `Search budget reached (${MAX_SEARCHES} searches for this question). ` +
            `Don't search again. Answer now with what you found; if the requested source has nothing, say so.`,
        };
      }
      const tokens = searchTopicTokens(String(event.input?.query ?? ""));
      const topic = tokens.size > 0
        ? searchTopics.find((t) => t.queries.some((prev) => isSameSearchTopic(prev, tokens)))
        : undefined;
      if (MAX_TOPIC_SEARCHES > 0 && topic && topic.queries.length >= MAX_TOPIC_SEARCHES) {
        return {
          block: true,
          reason:
            `You already ran ${topic.queries.length} similar searches on this topic. ` +
            `Open one of the URLs you found with agent_browser (or use gitmcp_*), move on to a different topic, ` +
            `or answer with what you have.`,
        };
      }
      if (MAX_CONSECUTIVE_SEARCHES > 0 && consecutiveSearches >= MAX_CONSECUTIVE_SEARCHES) {
        return {
          block: true,
          reason:
            `You ran ${consecutiveSearches} searches in a row without opening any page or docs. ` +
            `Open one of the URLs you found with agent_browser (or query gitmcp_*/context7_) before searching again, ` +
            `or answer with what you have.`,
        };
      }
      searches++;
      consecutiveSearches++;
      toolCalls++;
      if (tokens.size > 0) {
        if (topic) topic.queries.push(tokens);
        else searchTopics.push({ queries: [tokens] });
      }
    }

    // 20. bash can't see ~/.pi (sandbox) and can't reach package registries.
    //     Send the model to web_search instead of letting it hunt with find/npm.
    if (event.toolName === "bash") {
      const cmd = String(event.input?.command ?? "");
      // 27. web_search called as a shell command.
      if (/(?:^|[\s;&|(])agent[-_]browser(?:\s|$)/.test(cmd)) {
        return {
          block: true,
          reason: 'agent_browser is a tool, not a shell command. Call the agent_browser tool: {"args":["open","<url>"]}.',
        };
      }
      if (/^\s*(?:web_search\b|!)/.test(cmd)) {
        return {
          block: true,
          reason:
            "web_search is a tool, not a shell command. Call the web_search tool: {\"query\":\"<words>\"}. " +
            "If it isn't available, tell the user; don't search another way.",
        };
      }
      const touchesPi = /(~|\$HOME|\/home\/[^/\s]+)\/\.pi(\/|["\s]|$)/.test(cmd) &&
        !/pi-agent-browser-(?:artifacts|output|snapshot)/.test(cmd);
      const registry = /\b(npm\s+(view|info|show|search)|pip3?\s+(show|index|search|download)|pypi)\b/.test(cmd);
      if (touchesPi) {
        // The extension itself can read the file, so answer with its contents
        // instead of sending the model round in circles.
        const shown = readPiFileForBash(cmd);
        if (shown) return { block: true, reason: shown };
        const file = cmd.match(/(?:~|\$HOME|\/home\/[^/\s]+)\/\.pi\/\S*\.(?:json|md|ts|js|txt|ya?ml)/)?.[0];
        return {
          block: true,
          reason:
            "bash can't see ~/.pi (the sandbox hides it), so files there look missing. " +
            "Use the read tool on the file instead" +
            (file ? `: read ${file}` : ", e.g. read ~/.pi/agent/npm/node_modules/<package>/package.json") +
            ". Installed Pi packages are in ~/.pi/agent/npm/node_modules/.",
        };
      }
      if (registry) {
        return {
          block: true,
          reason:
            "Package registries (npm, PyPI) aren't reachable from bash here. If the package is installed, " +
            "read its package.json with the read tool; otherwise use web_search, e.g. \"<name> npm\".",
        };
      }
    }

    // 15. No curl/wget for web pages (API calls and local endpoints are allowed).
    if (BLOCK_CURL && event.toolName === "bash") {
      const rawCmd = String(event.input?.command ?? "");
      // Blank quoted strings without a URL, so a ";" in a User-Agent doesn't
      // hide the URL that follows it.
      const cmd = rawCmd
        .replace(/(["'])(?:(?!\1).)*\1/g, (quoted) => (/https?:\/\//.test(quoted) ? quoted : '""'));
      if (/\b(curl|wget)\b[^|;&]*https?:\/\//.test(cmd) && !isApiCurl(rawCmd)) {
        return {
          block: true,
          reason:
            `Don't use curl/wget for websites. Use agent_browser: {"args":["open","<url>"]} then ` +
            `{"args":["get","text","body"]}. GitHub files open as raw automatically.`,
        };
      }
    }

    // 42. No git clone of a remote repo unless the person asked for one (clone, clonar, klonen, cloner...).
    if (event.toolName === "bash" && !/clon|klon/i.test(lastUserText)) {
      const cmd = String(event.input?.command ?? "");
      if (/\bgit\s+(?:-\S+\s+)*(?:clone|ls-remote)\b[^|;&]*(?:https?:\/\/|git@|ssh:\/\/)/.test(cmd)) {
        return {
          block: true,
          reason:
            "Don't clone repositories to look things up (issues and pull requests aren't in a clone). Open the " +
            'repo\'s pages with agent_browser: {"args":["open","https://github.com/<owner>/<repo>/issues?q=<words>"]} ' +
            "(or /pulls?q=...), then get text. If the search budget is used up, answer with what you found.",
        };
      }
    }

    // 7. Repair mistyped paths to agent-browser output files
    if (event.toolName === "read") {
      // 28. read is for local files.
      const target = event.input?.path ?? event.input?.file_path;
      if (typeof target === "string" && /^https?:\/\//.test(target)) {
        return {
          block: true,
          reason: `read is for local files. Use agent_browser: {"args":["open","${target}"]} then {"args":["get","text","body"]}.`,
        };
      }
      // ... and a page just opened isn't saved anywhere yet: a read of a file that doesn't exist, after an open and
      //     before any get text, is a guessed path (run 4's B2: ".../raw.githubusercontent.com%2F...README.md",
      //     then ".../agent_browser_<n>_raw.txt"). Mistyped paths to saved pages are repaired below instead.
      if (typeof target === "string" && pageTextPending && !/\.pi-agent-browser-artifacts/.test(target) &&
          (() => { try { return fs.statSync(target.replace(/^~(?=\/)/, os.homedir())).isDirectory(); } catch { return true; } })()) {
        return {
          block: true,
          reason: "That isn't the page: an opened page isn't saved as a file until you get its text. Use agent_browser " +
            '{"args":["get","text","body"]}, then read the .txt path its result names if it is long.',
        };
      }
      return repairArtifactPath(event.input);
    }

    // 8. bash can't see Pi's session folder (sandbox). Send the model to `read`
    //    with the real path instead of letting it search with ls/cat/find.
    if (event.toolName === "bash") {
      const cmd = String(event.input?.command ?? "");
      if (/\.pi\/agent\/sessions|pi-agent-browser-artifacts|pi-agent-browser-(?:output|snapshot)/.test(cmd)) {
        // grep on a saved page: run the search here and return the matches.
        const grepped = emulateGrep(cmd);
        if (grepped) return { block: true, reason: grepped };

        const all = listArtifactFiles();
        const newest = (all.find((f) => f.file.endsWith(".txt")) ?? all[0])?.file;
        return {
          block: true,
          reason:
            "Don't open agent-browser output files with bash. Use the read tool instead" +
            (newest ? `. The newest output file is: ${newest}` : ", with the exact path from the agent_browser result."),
        };
      }
    }

    // 0. Other page readers -> agent_browser
    if (REDIRECT_READ_TOOLS.includes(event.toolName)) {
      const url = event.input?.url ?? "<url>";
      return {
        block: true,
        reason:
          `Don't use ${event.toolName}. Use agent_browser: ` +
          `to read a page {"args":["open","${url}"]} then {"args":["get","text","body"]}; ` +
          `to search saved page text use grep -n -i 'word' <file>.`,
      };
    }

    if (event.toolName !== "agent_browser") return;

    const input = event.input;
    if (!input || typeof input !== "object") { toolCalls++; return; }

    const problems: string[] = [];

    // 1. Session parameters
    for (const field of SESSION_FIELDS) {
      if (field in input) {
        problems.push(`remove "${field}"`);
        if (MODE === "rewrite") delete input[field];
      }
    }

    // 1d. eval mistakes seen in practice:
    //     - args sent as one string instead of an array
    //     - stdin wrapped in an extra pair of quotes, so eval just returns
    //       the code as a string instead of running it
    if (typeof input.args === "string") {
      let parsed: any;
      try { parsed = JSON.parse(input.args); } catch {}
      if (Array.isArray(parsed)) input.args = parsed;
      else if (/\beval\b/.test(input.args) && typeof input.stdin === "string") input.args = ["eval", "--stdin"];
    }
    if (typeof input.stdin === "string") {
      const t = input.stdin.trim();
      if (t.length > 1 && /^["'`]/.test(t) && t[0] === t[t.length - 1] && /[(=>]/.test(t)) {
        let inner = t.slice(1, -1);
        if (t[0] === '"') { try { inner = JSON.parse(t); } catch {} }
        input.stdin = inner;
      }
    }

    const args = input.args;
    if (Array.isArray(args)) {
      // 38. Clicking a link: open its URL instead (clicks on links land on the wrong page or a stale snapshot).
      // ... also inside a batch: [["click","@e62"]] or [{"type":"click","selector":"@e62"}] (run 7's C1 rerun).
      const clickRefs = args[0] === "click" && typeof args[1] === "string" ? [args[1]]
        : args[0] === "batch" && typeof input.stdin === "string"
          ? [...input.stdin.matchAll(/click[^\]}]{1,40}?@?\b(e\d+)\b/gi)].map((m) => m[1]) : [];
      for (const clickRef of clickRefs) {
        const ref = clickRef.replace(/^@/, "");
        let text = lastSnapshotText;
        for (const f of lastSnapshotText.match(/\S+pi-agent-browser-snapshot-[A-Za-z0-9]+\.(?:txt|json)/g) ?? []) {
          try {
            text += "\n" + (f.endsWith(".json") ? (extractText(JSON.parse(fs.readFileSync(f, "utf8"))) ?? "") : fs.readFileSync(f, "utf8"));
          } catch {}
        }
        const label = text.match(new RegExp(`link "([^"\\n]{1,120})"[^\\n]*\\[ref=${ref.replace(/[^\w-]/g, "")}\\]`))?.[1];
        if (label) {
          const word = label.replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter((w) => w.length > 2)[0] ?? label.slice(0, 20);
          const finder = `[...document.links].filter(a=>/${word.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}/i.test(a.textContent+' '+a.href)).slice(0,20).map(a=>a.textContent.trim().slice(0,80)+' -> '+a.href).join(' | ')`;
          return {
            block: true,
            reason: `Don't click links: "${label}" is a link. Find its URL and open it instead: ` +
              `{"args":["eval","--stdin"],"stdin":${JSON.stringify(finder)}}, then {"args":["open","<the URL>"]}.`,
          };
        }
      }
      if (MODE === "rewrite") {
        if (stripSessionArgs(args)) problems.push("remove --session from args");
      } else if (args.some((a: string) => SESSION_FLAGS.has(a) || [...SESSION_FLAGS].some((f) => a.startsWith(f + "=")))) {
        problems.push("remove --session from args");
      }

      // 1a. Invented flags before the command ("--no-auto-connect", "--headless"...)
      //     The command must come first. Drop flags when the next arg is a
      //     known command; otherwise block.
      while (args.length && String(args[0]).startsWith("--")) {
        const flag = String(args[0]);
        const next = String(args[1] ?? "");
        const droppable = flag.includes("=") || flag.startsWith("--no-") || COMMON_COMMANDS.has(next);
        if (droppable && MODE === "rewrite") {
          args.shift();
          continue;
        }
        return {
          block: true,
          reason:
            `Invalid agent_browser call: args must start with the command, not "${flag}". ` +
            `Don't add flags. Example: {"args":["open","<url>"]}.`,
        };
      }

      // 46. A search engine's home page, to type into its box (run 8's F2): open the results URL, or use web_search.
      if (["open", "read"].includes(String(args[0])) && typeof args[1] === "string") {
        const home = SEARCH_HOMES.find(([re]) => re.test(args[1]));
        if (home) {
          const quoted = lastUserText.match(/["“«„]([^"”»“]{2,200})["”»“]/)?.[1]?.trim() ?? "";
          const named = namedEngine(lastUserText);
          const words = quoted || "<search words>";
          return {
            block: true,
            reason: named === home[1]
              ? namedEngineReason(home[1], `{"query":${JSON.stringify(words)}}`)
              : `Don't search in the browser. Search with the web_search tool instead: {"query":${JSON.stringify(words)}}.`,
          };
        }
      }

      // 24. A search engine's results page: search with web_search instead, also when the user named that engine
      //     (it used to get one try: Google and Mojeek refused the automated browser every time, 78 times in runs 1-10).
      if (["open", "read"].includes(String(args[0])) && typeof args[1] === "string") {
        const engine = SEARCH_PAGES.find(([re]) => re.test(args[1]))?.[1];
        if (engine) {
          const named = namedEngine(lastUserText);
          let words = "";
          try {
            const u = new URL(args[1]);
            words = u.searchParams.get("q") ?? u.searchParams.get("p") ?? u.searchParams.get("query") ?? "";
          } catch {}
          const call = `{"query":${JSON.stringify(words || "<search words>")}}`;
          return {
            block: true,
            reason: named === engine
              ? namedEngineReason(engine, call)
              : `Don't search in the browser. Search with the web_search tool instead: ${call}. ` +
                "Then open the result URLs you need with agent_browser.",
          };
        }
      }

      // 54. Direct Google Scholar search URLs (/scholar?q=...) trigger anti-bot checks more readily than
      //     searching from the homepage: direct the model to open https://scholar.google.com/, snapshot,
      //     fill the search box, and press Enter.
      if (["open", "goto", "navigate", "read"].includes(String(args[0])) && typeof args[1] === "string" &&
          /^https?:\/\/scholar\.google\.[a-z.]+\/scholar\b/i.test(args[1])) {
        let q = "";
        try { q = new URL(args[1]).searchParams.get("q")?.trim() ?? ""; } catch {}
        if (q) {
          return {
            block: true,
            reason:
              `Don't navigate directly to a Google Scholar search URL. Open the homepage first: {"args":["open","https://scholar.google.com/"]}, ` +
              `then run ["snapshot","-i"], ["fill","@eN",${JSON.stringify(q)}] in the search box, ["press","Enter"], and ["get","text","body"].`,
          };
        }
      }

      if (["open", "read"].includes(String(args[0]))) openedPage = true;
      if ((args[0] === "read" && args.length > 1) || (args[0] === "get" && args[1] === "text")) readPage = true;

      // 1g. After a bot-check page: turn the next read / get text / snapshot of
      //     that page into `read <url>`, which fetches it over plain HTTP.
      if (botBlockedUrl && MODE === "rewrite") {
        if (args[0] === "open" && args[1] !== botBlockedUrl) {
          botBlockedUrl = undefined;
        } else if ((args[0] === "read" && args.length === 1) ||
                   (args[0] === "get" && args[1] === "text") ||
                   args[0] === "snapshot" ||
                   (args[0] === "open" && args[1] === botBlockedUrl)) {
          args.splice(0, args.length, "read", botBlockedUrl);
          botBlockedUrl = undefined;
        }
      }

      // 32. is:issue hides pull requests, where the answer often is.
      if (MODE === "rewrite" && args[0] === "open" && typeof args[1] === "string" && /^https?:\/\/github\.com\/[^/]+\/[^/]+\/issues\?/.test(args[1])) {
        args[1] = args[1].replace(/(q=)is(?:%3A|:)issue(?:\+|%20)?/i, "$1");
      }

      // 1f. Invented command names for eval
      if (["evaluate", "js", "javascript", "exec"].includes(String(args[0])) && MODE === "rewrite") args[0] = "eval";

      // 1e. GitHub file pages -> raw files (plain text, a fraction of the tokens)
      //     github.com/OWNER/REPO/blob/BRANCH/PATH -> raw.githubusercontent.com/OWNER/REPO/BRANCH/PATH
      if (args[0] === "open" && typeof args[1] === "string" && MODE === "rewrite") {
        const gh = args[1].match(/^https?:\/\/github\.com\/([^/]+)\/([^/]+)\/blob\/(.+)$/);
        if (gh) args[1] = `https://raw.githubusercontent.com/${gh[1]}/${gh[2]}/${gh[3].split("#")[0]}`;
      }

      // 1b. Invented `get` subcommands ("text/html", "page-text", "content"...)
      if (args[0] === "get" && typeof args[1] === "string" && !GET_SUBCOMMANDS.has(args[1])) {
        const sub = args[1].toLowerCase();
        const fixed = sub.includes("text") || sub.includes("content") ? "text"
                    : sub.includes("html") ? "html"
                    : undefined;
        if (fixed && MODE === "rewrite") {
          args[1] = fixed;
        } else {
          problems.push(
            `"get ${args[1]}" doesn't exist. Valid: ${[...GET_SUBCOMMANDS].join(", ")}. ` +
            `For page text use ["get","text","body"]`
          );
          if (!fixed) {
            return { block: true, reason: `Invalid agent_browser call. Fix: ${problems.join("; ")}.` };
          }
        }
      }

      // 1c. outputPath on these commands writes raw spill JSON or stray files, or
      //     fails on a bad path. Results already point to a readable .txt, so drop it.
      if (["get", "open", "read", "snapshot"].includes(String(args[0])) && "outputPath" in input) {
        if (MODE === "rewrite") delete input.outputPath;
        else problems.push(`remove "outputPath"; the page text is saved as .txt automatically`);
      }

      // 2. get text without selector
      if (args[0] === "get" && args[1] === "text" && args.length === 2) {
        problems.push(`add a selector: ["get","text","${DEFAULT_SELECTOR}"]`);
        if (MODE === "rewrite") args.push(DEFAULT_SELECTOR);
      }

      // 3. get text with a guessed CSS selector (#content, main, .article...)
      //    Only "body" or an @eN ref from a snapshot is guaranteed to exist.
      if (
        ONLY_REFS_OR_BODY &&
        args[0] === "get" &&
        args[1] === "text" &&
        args.length >= 3 &&
        args[2] !== DEFAULT_SELECTOR &&
        !String(args[2]).startsWith("@")
      ) {
        problems.push(
          `"${args[2]}" is a guessed selector. Use ["get","text","body"] or an @eN ref from ["snapshot","-i"]`
        );
        if (MODE === "rewrite") args[2] = DEFAULT_SELECTOR;
      }
    }

    if (problems.length === 0 || MODE === "rewrite") { toolCalls++; return; }

    return {
      block: true,
      reason: `Invalid agent_browser call. Fix: ${problems.join("; ")}. Pi manages the browser session automatically.`,
    };
  });

  // 4. Hide session names from the model. Pi validates the schema BEFORE
  //    tool_call runs, so an extra "session" field can't be stripped above.
  //    Instead, remove session values from results so the model never sees one
  //    to copy.
  // 4 + 9. Clean up agent_browser results before the model sees them.
  pi.on("tool_result", async (event: any) => {
    if (WEB_TOOL_RE.test(event.toolName)) usedWeb = true; // 34.
    if (event.toolName === "bash" && event.isError && Array.isArray(event.content)) {
      const command = String(event.input?.command ?? "");
      const output = textOf(event.content);
      // 51.
      if (FILE_CHANGE_RE.test(command) && PROTECTED_RE.test(output) && !TEST_RUN_RE.test(command)) {
        event.content.push({ type: "text", text:
          "\n\nThe system protects this file (busy, read-only or not allowed). Don't try to get around it (no " +
          "umount, sudo, chmod, or writing over it another way): tell the person you couldn't, and why, and stop." });
        return { content: event.content };
      }
      // 52.
      if (TEST_RUN_RE.test(command)) {
        event.content.push({ type: "text", text:
          "\n\nSome tests failed. Fix the code so it does what the person asked. Don't change what they asked for " +
          "(defaults, names, messages, options) or weaken a test to make it pass; if their request and an " +
          "existing test disagree, follow the request and say so." });
        return { content: event.content };
      }
    }
    if (event.isError && /The user hasn't asked for file changes\./.test(textOf(event.content ?? []))) {
      editBlocked = true;
    }
    if (event.toolName === "web_search" && event.isError &&
        /isn't running, so web_search can't search/.test(textOf(event.content ?? []))) searchDown = true; // 47.
    if (!event.isError && ["write", "edit"].includes(event.toolName) &&
        /(?:^|\/)PLAN\.md$/.test(String(event.input?.path ?? ""))) planWritten = true; // 48.
    if (!event.isError && event.toolName === "plannotator_inbox" && event.input?.action === "send_message" &&
        JSON.stringify(event.input?.attachments ?? []).includes("PLAN.md")) planSent = true;
    // 37. Docs from Context7 or GitMCP count as read; Context7's library IDs are kept for later questions.
    if (DOCS_TOOL_RE.test(event.toolName) && !event.isError) {
      readPage = true;
      if (event.toolName === "context7_resolve-library-id" && libraryKey(event.input)) {
        const text = (event.content ?? []).map((p: any) => (typeof p?.text === "string" ? p.text : "")).join("\n");
        if (/\/[\w.-]+\/[\w.-]+/.test(text)) saveLibraryIds(libraryKey(event.input), text);
      }
    }
    // 36. Remember what was read, for the finish check. A URL it opened counts too.
    const openedUrl = event.toolName === "agent_browser" && !event.isError ? event.input?.args?.[1] : undefined;
    if (typeof openedUrl === "string" && /^https?:/.test(openedUrl)) addSource(openedUrl);
    if (Array.isArray(event.content)) {
      for (const part of event.content) {
        addSource(part?.text);
        const spill = typeof part?.text === "string" ? part.text.match(/Full (?:output|redacted snapshot) path: (\S+\.json)/)?.[1] : undefined;
        if (spill) { try { addSource(extractText(JSON.parse(fs.readFileSync(spill, "utf8")))); } catch {} }
      }
    }

    // 18. A site: search with 0 results: tell the model what to do next.
    if (event.toolName === "web_search" && Array.isArray(event.content)) {
      // 24. The person named an engine: these results aren't from it, and the answer says so first (run 13's D3
      //     didn't mention Mojeek once AGENTS.md alone said it); no sentence to copy: run 15's D3-es copied an English one
      //     into a Spanish answer).
      const named = namedEngine(lastUserText);
      if (named && !event.isError && !engineNoteGiven) {
        engineNoteGiven = true;
        const name = named[0].toUpperCase() + named.slice(1);
        event.content.push({ type: "text", text:
          `\n\nThe person asked for ${name}. These results come from Search Gateway, not ${name}: search engines ` +
          `refuse automated browsers, so ${name} wasn't opened. Begin your answer, in the language of the person's ` +
          `message, by saying the results come from Search Gateway because ${name} doesn't allow automated browsers.` });
        return { content: event.content };
      }
      for (const part of event.content) {
        if (part?.type !== "text" || typeof part.text !== "string") continue;
        const q = part.text.match(/Search Results:\s*"([^"\n]*\bsite:[^"\n]*)"\s*\n/);
        if (/Results:\s*0\b/.test(part.text) && q) {
          part.text += "\n\nHint: nothing on that site. Search once more without site:, with 2-4 keywords.";
          return { content: event.content };
        }
      }
      return;
    }

    if (event.toolName !== "agent_browser") return;
    if (!Array.isArray(event.content)) return;

    // 21. Bot-check pages (Anubis, Cloudflare...): the real browser got a
    //     challenge page instead of the content. agent-browser's `read`
    //     fetches over plain HTTP and often gets through.
    const url = typeof event.input?.args?.[1] === "string" && /^https?:/.test(event.input.args[1])
      ? event.input.args[1] : lastOpenedUrl;
    if (event.input?.args?.[0] === "open" && typeof event.input.args[1] === "string") lastOpenedUrl = event.input.args[1];
    const whole = event.content.map((p: any) => (typeof p?.text === "string" ? p.text : "")).join("\n");
    if (event.input?.args?.[0] === "snapshot") lastSnapshotText = whole;

    // 35. PDFs: the browser shows no text and `read` returns raw bytes, so extract it here.
    const command = event.input?.args?.[0];
    const target = event.input?.args?.[1];
    if ((command === "open" || command === "read") && typeof target === "string" && PDF_URL_RE.test(target)) {
      const extracted = await pdfToText(target);
      if (extracted) {
        lastPdf = { url: target, path: extracted.file };
        addSource(extracted.text);
        return {
          content: [{
            type: "text",
            text:
              `PDF opened: ${target}\nFull page text (${extracted.lines} lines): ${extracted.file}\n` +
              `Read it with the read tool for more. It begins:\n\n${extracted.text.slice(0, 2000)}`,
          }],
        };
      }
    }
    // 24b. A search engine answered with a CAPTCHA ("google.com/sorry/...", "unusual traffic"): it's blocked. Say so
    //      plainly, so the answer can say it too.
    const searchEngine = typeof url === "string" ? SEARCH_PAGES.find(([re]) => re.test(url))?.[1] : undefined;
    if (searchEngine && (command === "open" || command === "read") &&
        (/\/sorry\/|captcha|unusual traffic|are you a robot|not a robot|\b40[39]\b|forbidden|too many requests/i.test(whole) || botCheckText(whole))) {
      const words = (() => { try { const u = new URL(url); return u.searchParams.get("q") ?? u.searchParams.get("p") ?? ""; } catch { return ""; } })();
      return {
        content: [{
          type: "text",
          text:
            `No results on this page: ${searchEngine[0].toUpperCase() + searchEngine.slice(1)} blocked the search (CAPTCHA or access denied). ` +
            `Your next step is a tool call, not the answer: call web_search with exactly the same words and no site: filter: ` +
            `{"query":${JSON.stringify(words || "<the same words>")}}. ` +
            `Once you have its results, answer, beginning with: ${searchEngine} blocked the search, so the results come from Search Gateway.`,
        }],
        isError: true,
      };
    }
    let changed = false;

    // 53. Pages with a cross-origin reCAPTCHA iframe (`iframe[title="reCAPTCHA"]` or `#captcha`):
    //     wait for manual resolution (#recaptcha-token value / checkbox), return to the main frame,
    //     and wait for any URL redirect + load before the model reads it.
    //     Runs before looksLikeBotCheck so manual CAPTCHA resolution isn't interrupted by a `read` rewrite.
    let captchaResolved = false;
    if (!event.isError && (command === "open" || command === "goto" || command === "navigate" || command === "click" || command === "press")) {
      const session = sessionNameOf(event);
      const socketDir = session ? findSessionSocketDir(session, event?.details?.managedSessionSocketDir) : undefined;
      if (session && socketDir) {
        const result = await waitForCaptchaRedirect(
          (args, timeoutMs) => runSessionBrowser(session, socketDir, args, timeoutMs),
          typeof target === "string" ? target : undefined,
        );
        if (result) {
          captchaResolved = true;
          lastOpenedUrl = result.url;
          addSource(result.url);
          if (result.redirected) {
            event.content.splice(0, event.content.length, {
              type: "text",
              text:
                `CAPTCHA resolved and redirected to: ${result.url}${result.title ? ` (${result.title})` : ""}. ` +
                `The browser is now on the destination page — read it with ["get", "text", "body"] (or continue your task). Do not re-open the original URL.`,
            });
          } else {
            event.content.unshift({
              type: "text",
              text:
                `CAPTCHA resolved on: ${result.url}${result.title ? ` (${result.title})` : ""}. ` +
                `The CAPTCHA is already solved — do not click the CAPTCHA checkbox or re-open the page (any remaining reCAPTCHA iframe or disabled challenge buttons are just leftover DOM). ` +
                `Read the page with ["get", "text", "body"] or continue your task.\n\n`,
            });
          }
          changed = true;
        }
      }
    }

    // Cloudflare Turnstile: agent_browser flags unsolved Turnstile as an error, but the
    // browser session is still alive and the page is loaded. Run waiting even on errors
    // so the model can wait for manual resolution (unlike reCAPTCHA, agent_browser does
    // not wait natively — it returns an error immediately).
    if ((command === "open" || command === "goto" || command === "navigate" || command === "click" || command === "press") && !captchaResolved) {
      const session = sessionNameOf(event);
      const socketDir = session ? findSessionSocketDir(session, event?.details?.managedSessionSocketDir) : undefined;
      if (session && socketDir) {
        const turnstileResult = await waitForTurnstileRedirect(
          (args, timeoutMs) => runSessionBrowser(session, socketDir, args, timeoutMs),
          typeof target === "string" ? target : undefined,
        );
        if (turnstileResult) {
          captchaResolved = true;
          lastOpenedUrl = turnstileResult.url;
          addSource(turnstileResult.url);
          if (turnstileResult.redirected) {
            event.content.splice(0, event.content.length, {
              type: "text",
              text:
                `Cloudflare Turnstile resolved and redirected to: ${turnstileResult.url}${turnstileResult.title ? ` (${turnstileResult.title})` : ""}. ` +
                `The browser is now on the destination page — read it with ["get", "text", "body"] (or continue your task). Do not re-open the original URL.`,
            });
          } else {
            event.content.unshift({
              type: "text",
              text:
                `Cloudflare Turnstile is already solved on: ${turnstileResult.url}${turnstileResult.title ? ` (${turnstileResult.title})` : ""}. ` +
                `The Turnstile is already solved — do not click the widget or re-open the page. ` +
                `Read the page with ["get", "text", "body"] or continue your task.\n\n`,
            });
          }
          changed = true;
        } else {
          // No widget detected (or not a solvable widget): fall back to interstitial check.
          const turnstile = await detectTurnstile(
            (args, timeoutMs) => runSessionBrowser(session, socketDir, args, timeoutMs),
            typeof target === "string" ? target : undefined,
          );
          if (turnstile?.present && turnstile.kind === "interstitial") {
            return {
              content: [{
                type: "text",
                text:
                  `Cloudflare Turnstile / managed challenge detected on: ${turnstile.url}${turnstile.title ? ` (${turnstile.title})` : ""}. ` +
                  `Do not retry, click the challenge, or switch to plain HTTP read. Stop and inform the user that this page is gated by a Cloudflare Turnstile challenge so they can complete verification manually in a visible browser (or choose another source).`,
              }],
              isError: true,
            };
          }
        }
      }
    }

    const looksLikeBotCheck = !captchaResolved &&
      whole.length <= BOT_CHECK_MAX_CHARS &&
      !/Full (?:output|redacted snapshot) path:/.test(whole) &&
      botCheckText(whole);
    if (looksLikeBotCheck && !(event.input?.args?.[0] === "read" && event.input?.args?.[1])) {
      botBlockedUrl = url;
      event.content.push({
        type: "text",
        text:
          "\n\nThis is a bot-check page, not the real content. Don't search elsewhere yet: your next " +
          `page-reading call will fetch it without the browser ({"args":["read","${url ?? "<url>"}"]}).`,
      });
      return { content: event.content };
    }

    // 23. The browser's PDF viewer exposes no text, and `read` returns raw PDF bytes.
    if (/^\s*\(empty string\)/.test(whole) && /Origin: \S*(?:\.pdf|\/pdf\/)/i.test(whole)) {
      if (lastPdf) {
        event.content.push({ type: "text", text: `\n\nThis PDF's text was saved to: ${lastPdf.path}. Read it with the read tool.` });
        return { content: event.content };
      }
      event.content.push({
        type: "text",
        text:
          "\n\nThis is a PDF: the browser can't extract its text, and read returns raw bytes. Don't retry or " +
          "download it. Use an HTML version if there is one (for arXiv: https://arxiv.org/html/<id>), and say " +
          "the text came from there.",
      });
      return { content: event.content };
    }

    // 50. A GitHub issue or pull request search: add GitHub's best matches for the same words (once per page).
    const issueSearch = typeof target === "string" && ["open", "read"].includes(command) && !event.isError
      ? issueSearchWords(target) : undefined;
    if (issueSearch && !issueMatchesAdded.has(target)) {
      issueMatchesAdded.add(target);
      const matches = await issueMatches(issueSearch.owner, issueSearch.repo, issueSearch.words);
      if (matches) {
        addSource(matches);
        event.content.push({ type: "text", text: `\n\n${matches}` });
        return { content: event.content };
      }
    }

    // 41. A GitHub releases page read as text: add each release's date from GitHub's API (once per page).
    const releases = typeof url === "string" &&
      url.match(/^https?:\/\/github\.com\/([\w.-]+)\/([\w.-]+)\/releases(?:\/(?:tag\/([^/?#]+)|latest))?\/?(?:[?#].*)?$/);
    if (releases && (command === "read" || (command === "get" && event.input?.args?.[1] === "text")) &&
        !releaseDatesAdded.has(url) && !event.isError) {
      releaseDatesAdded.add(url);
      const dates = await releaseDates(releases[1], releases[2], releases[3]);
      if (dates) {
        addSource(dates);
        event.content.push({ type: "text", text: `\n\n${dates}` });
        return { content: event.content };
      }
    }

    for (const part of event.content) {
      if (part?.type !== "text" || typeof part.text !== "string") continue;
      let text = part.text;
      if (CONVERT_SPILLS) text = convertSpill(text);
      if (SCRUB_SESSION_FROM_RESULTS) text = scrubSession(text);
      if (text !== part.text) {
        part.text = text;
        changed = true;
      }
    }
    if (changed) return { content: event.content };
  });
}

// 44. "accessed on Nov 16, 2026", "read directly via agent_browser on 2026-11-10", "retrieved as of 10 Nov 2026".
const ACCESSED_ON_RE = new RegExp(
  "\\b(?:accessed|retrieved|fetched|read|visited|checked|viewed)\\b(?:[^.\\n]{0,40}?\\b(?:on|as of))?\\s+" +
  "(?:\\d{4}-\\d{2}-\\d{2}|[A-Z][a-z]{2,8}\\.? \\d{1,2},? \\d{4}|\\d{1,2} [A-Z][a-z]{2,8}\\.? \\d{4})", "i");

// 43. The main writing system of a text (letters outside code, URLs and paths), or undefined when too short or mixed.
const SCRIPTS: [string, RegExp][] = [
  ["han-kana", /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u], ["hangul", /\p{Script=Hangul}/u],
  ["arabic", /\p{Script=Arabic}/u], ["cyrillic", /\p{Script=Cyrillic}/u], ["hebrew", /\p{Script=Hebrew}/u],
  ["devanagari", /\p{Script=Devanagari}/u], ["bengali", /\p{Script=Bengali}/u], ["thai", /\p{Script=Thai}/u],
  ["greek", /\p{Script=Greek}/u], ["latin", /\p{Script=Latin}/u],
];
function mainScript(text: string, minLetters = 8): string | undefined {
  const prose = text.replace(/```[\s\S]*?```/g, " ").replace(/`[^`]*`/g, " ").replace(/https?:\/\/\S+/g, " ")
    .replace(/~?\/[\w./-]+/g, " ");
  const counts = new Map<string, number>();
  for (const ch of prose) {
    const hit = SCRIPTS.find(([, re]) => re.test(ch));
    if (hit) counts.set(hit[0], (counts.get(hit[0]) ?? 0) + 1);
  }
  // A CJK character carries a word, so it weighs more: a Chinese sentence with English terms still counts as Chinese.
  const weighted = [...counts.entries()].map(([k, v]) => [k, k === "han-kana" || k === "hangul" ? v * 3 : v] as const)
    .sort((x, y) => y[1] - x[1]);
  const total = weighted.reduce((sum, [, v]) => sum + v, 0);
  if (total < minLetters || weighted[0][1] < 0.6 * total) return;
  return weighted[0][0];
}

// GitHub's API, with the token in ~/.config/localllm/github-token when there is one (lib/common.sh: optional; it
// raises the search limit from 10 to 30 a minute). The token is only sent to GitHub, never shown to the model.
const githubApi = () => (process.env.PI_GITHUB_API || "https://api.github.com").replace(/\/$/, "");
function githubHeaders(): Record<string, string> {
  const headers: Record<string, string> = { accept: "application/vnd.github+json", "user-agent": "pi-agent-browser-fix" };
  try {
    const file = process.env.PI_GITHUB_TOKEN_FILE || path.join(os.homedir(), ".config/localllm/github-token");
    const token = fs.readFileSync(file, "utf8").trim();
    if (/^[\w-]{20,}$/.test(token)) headers.authorization = `Bearer ${token}`;
  } catch { /* no token: GitHub's lower limits */ }
  return headers;
}

// 41. Release dates from GitHub's API: "tag — date — page" per release, newest first (on any error, none).
const releaseDatesAdded = new Set<string>();
async function releaseDates(owner: string, repo: string, tag?: string): Promise<string | undefined> {
  const where = tag ? `releases/tags/${encodeURIComponent(decodeURIComponent(tag))}` : "releases?per_page=10";
  try {
    const res = await fetch(`${githubApi()}/repos/${owner}/${repo}/${where}`, {
      headers: githubHeaders(),
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return undefined;
    const data = await res.json();
    const list = (Array.isArray(data) ? data : [data]).filter((r: any) => r?.tag_name && r?.published_at);
    if (!list.length) return undefined;
    const lines = list.map((r: any) =>
      `- ${r.tag_name}${r.prerelease ? " (pre-release)" : ""} — ${String(r.published_at).slice(0, 10)} — ${r.html_url ?? ""}`.trim());
    return `Release dates (from GitHub's API; the page text above shows none):\n${lines.join("\n")}`;
  } catch {
    return undefined;
  }
}

// 50. A repo's issue or pull request search page lists newest first, issues and pull requests mixed, 25 a page: the
//     issue asked about can be on page 3 (run 10's D4: #89 was 70th for "browser exit"). GitHub's search API ranks by
//     relevance and keeps the two apart (#89 is 4th of 9 issues). Its best matches for the same words are added.
const GITHUB_SEARCH_PAGE_RE = /^https?:\/\/github\.com\/([\w.-]+)\/([\w.-]+)\/(?:issues|pulls)\/?\?/;
const issueMatchesAdded = new Set<string>();
export function issueSearchWords(url: string): { owner: string; repo: string; words: string } | undefined {
  const m = url.match(GITHUB_SEARCH_PAGE_RE);
  if (!m) return;
  let q = "";
  try { q = new URL(url).searchParams.get("q") ?? ""; } catch { return; }
  // The page's own kind filters go: both kinds are searched. Other qualifiers (is:closed, label:...) stay.
  const words = q.replace(/(?:^|\s)(?:is|type):(?:issues?|prs?|pull-requests?)(?=\s|$)/gi, " ").replace(/\s+/g, " ").trim();
  // Only qualifiers left (e.g. "is:open"): no words to rank by.
  if (!words.split(" ").some((w) => w && !/^-?[\w-]+:/.test(w))) return;
  return { owner: m[1], repo: m[2], words };
}
async function issueMatches(owner: string, repo: string, words: string): Promise<string | undefined> {
  const kinds: Array<[string, string]> = [["is:issue", "Issues"], ["is:pr", "Pull requests"]];
  const lines = [`GitHub's best matches for "${words}" (its search API, most relevant first; the page above lists newest first):`];
  for (const [kind, label] of kinds) {
    try {
      const ask = new URLSearchParams({ q: `repo:${owner}/${repo} ${kind} ${words}`, per_page: "10" });
      const res = await fetch(`${githubApi()}/search/issues?${ask}`, { headers: githubHeaders(), signal: AbortSignal.timeout(8000) });
      if (res.status === 403 || res.status === 429) {
        const why = await res.text().catch(() => "");
        if (/rate limit/i.test(why) || res.headers.get("x-ratelimit-remaining") === "0") {
          lines.push(`${label}: GitHub's search limit is used up for now (it resets within a minute). Use the page's own list.`);
          break;
        }
        return;
      }
      if (!res.ok) return;
      const data = await res.json();
      const items: any[] = Array.isArray(data?.items) ? data.items : [];
      lines.push(`${label} (${Number(data?.total_count) || items.length} found${items.length ? "" : ", none with all these words"}):`);
      for (const i of items) {
        const state = i?.pull_request?.merged_at ? "merged" : String(i?.state ?? "");
        lines.push(`- #${i?.number} [${state}] ${String(i?.title ?? "").replace(/\s+/g, " ").slice(0, 200)} — ${i?.html_url ?? ""}`);
      }
    } catch {
      return;
    }
  }
  lines.push("If none of these is it, search again with other words for the same thing: GitHub matches every word as written.");
  return lines.join("\n");
}

// 24. The engine the person named: not opened (search engines refuse automated browsers), and the answer says so.
function namedEngineReason(engine: string, call: string): string {
  const name = engine[0].toUpperCase() + engine.slice(1);
  return `Not opened: ${name} doesn't allow automated browsers like this one (it answers with a CAPTCHA or an error). ` +
    `Your next step is a tool call: web_search with the same words, ${call}. Once you have its results, ` +
    `begin your answer, in the language of the person's message, by saying the results come from Search Gateway ` +
    `because ${name} doesn't allow automated browsers.`;
}

// Downloads a PDF and saves its text where the read tool may open it.
async function pdfToText(url: string): Promise<{ file: string; lines: number; text: string } | undefined> {
  const tmp = path.join(os.tmpdir(), `pi-pdf-${process.pid}-${Date.now()}.pdf`);
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(30000), headers: { "user-agent": "Mozilla/5.0" } });
    if (!response.ok) return;
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length > MAX_PDF_BYTES || bytes.subarray(0, 5).toString() !== "%PDF-") return;
    fs.writeFileSync(tmp, bytes);
    const text = await new Promise<string>((resolve, reject) => {
      execFile("pdftotext", ["-enc", "UTF-8", tmp, "-"], { timeout: 30000, maxBuffer: 64 * 1024 * 1024 }, (err, out) =>
        err ? reject(err) : resolve(String(out)));
    });
    const clean = wrapLongLines(text.replace(/\f/g, "\n").replace(/\n{3,}/g, "\n\n").trim());
    if (!clean) return;
    const name = `pi-agent-browser-output-${createHash("sha1").update(url).digest("hex").slice(0, 16)}.txt`;
    const dir = path.join(SESSIONS_DIR, "--pdf--", ARTIFACTS_DIR_NAME, "pdf");
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, name);
    fs.writeFileSync(file, clean + "\n", "utf8");
    return { file, lines: clean.split("\n").length, text: clean };
  } catch {
    return;
  } finally {
    try { fs.unlinkSync(tmp); } catch {}
  }
}

function scrubSession(text: string): string {
  return (
    text
      // "--session","pi-root-xxx",   (inside JSON args arrays)
      .replace(/"--session(?:-name)?"\s*,\s*"pi-root-[A-Za-z0-9_-]+"\s*,?\s*/g, "")
      // ,"sessionName":"pi-root-xxx"  or  "sessionName":"pi-root-xxx",
      .replace(/,?\s*"session(?:Name)?"\s*:\s*"pi-root-[A-Za-z0-9_-]+"\s*,?/g, (m) =>
        m.trim().startsWith(",") && m.trim().endsWith(",") ? "," : ""
      )
      // --session pi-root-xxx  (plain-text command lines)
      .replace(/--session(?:-name)?[ =]pi-root-[A-Za-z0-9_-]+\s*/g, "")
      // any leftover bare session id
      .replace(/pi-root-[A-Za-z0-9_-]+/g, "<managed-by-pi>")
  );
}

// Pull the page text out of a spill .json and save it as readable .txt.
function extractText(obj: any): string | undefined {
  if (typeof obj?.text === "string") return obj.text;
  if (typeof obj?.snapshot === "string") return obj.snapshot;
  if (typeof obj?.result === "string") return obj.result;
  if (typeof obj?.data?.result === "string") return obj.data.result;
  // Fallback: the longest string anywhere in the object.
  let best = "";
  const walk = (v: any) => {
    if (typeof v === "string") { if (v.length > best.length) best = v; }
    else if (v && typeof v === "object") for (const x of Object.values(v)) walk(x);
  };
  walk(obj);
  return best || undefined;
}

function wrapLongLines(text: string): string {
  return text
    .split("\n")
    .flatMap((line) => {
      const out: string[] = [];
      for (let i = 0; i < line.length; i += WRAP_AT) out.push(line.slice(i, i + WRAP_AT));
      return out.length ? out : [""];
    })
    .join("\n");
}

function convertSpill(text: string): string {
  const m = text.match(/(Full output path|Full redacted snapshot path): (\S+\.json)/);
  if (!m) return text;
  const label = m[1];
  const jsonPath = m[2];
  const txtPath = jsonPath.replace(/\.json$/, ".txt");

  let lines = 0;
  try {
    if (!fs.existsSync(txtPath)) {
      const body = extractText(JSON.parse(fs.readFileSync(jsonPath, "utf8")));
      if (!body) return text;
      const clean = wrapLongLines(body.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim());
      fs.writeFileSync(txtPath, clean + "\n", "utf8");
    }
    lines = fs.readFileSync(txtPath, "utf8").split("\n").length;
  } catch {
    return text; // leave the result untouched if anything goes wrong
  }

  return text
    // Point to the .txt instead of the .json.
    .split(jsonPath).join(txtPath)
    .replace(
      `${label}: ${txtPath}`,
      `Full page text (${lines} lines): ${txtPath}\n` +
        `Read it with the read tool. For long files use offset/limit, e.g. offset 1 limit 300, then offset 301.`
    )
    // The "body matched 2 elements" warning sends the model into eval/screenshot detours.
    // The saved text is what we want, so drop the warning and its suggested eval.
    .replace(/Selector text visibility warning:[\s\S]*?Next action:[^\n]*\n?/, "")
    .replace(/,?\{"id":"inspect-visible-text-candidates"[\s\S]*?"tool":"agent_browser"\}/, "")
    // Drop artifact bookkeeping the model doesn't need.
    .replace(/Session artifacts:[^\n]*\n?/, "");
}

// grep's basic regular expressions -> JavaScript: swap the escaped and bare meanings of ( ) | + ? { }.
function breToJs(pattern: string): string {
  let out = "";
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === "\\" && i + 1 < pattern.length) {
      const n = pattern[++i];
      out += "()|+?{}".includes(n) ? n : `\\${n}`;
    } else {
      out += "()|+?{}".includes(c) ? `\\${c}` : c;
    }
  }
  return out;
}

// The model's instinct is `grep -n 'blog' <saved file>`. The sandbox hides the
// file from bash, so do the search in the extension and hand back the lines.
function emulateGrep(cmd: string): string | undefined {
  // flags may take a number: -A 5, -B 2, -C 3, -m 10
  const g = cmd.match(/\bgrep\s+((?:-[A-Za-z]+\d*(?:\s+\d+)?\s+)*)(['"])(.+?)\2/);
  const f = cmd.match(/(\S*pi-agent-browser-(?:output|snapshot)-[A-Za-z0-9]+)\.(json|txt)/);
  if (!g || !f) return;

  let file = f[1].replace(/^["']/, "") + ".txt";
  if (file.startsWith("~/")) file = path.join(os.homedir(), file.slice(2));
  if (!fs.existsSync(file)) {
    const hit = listArtifactFiles().find((x) => path.basename(x.file) === savedName(file));
    if (!hit) return;
    file = hit.file;
  }

  const flags = g[1];
  const ignoreCase = /-[A-Za-z]*i/.test(flags);
  const num = (k: string) => Number(flags.match(new RegExp(`-${k}\\s*(\\d+)`))?.[1] ?? 0);
  const after = Math.min(num("A") || num("C"), 10);
  const before = Math.min(num("B") || num("C"), 10);

  // grep's basic syntax (BRE) in a JS regex: \( \) \| \+ \? \{ \} are the operators and the bare characters are
  // literal; with -E (or egrep) the pattern is already extended. Splitting on "|" broke \(S\|y\|u\) into a bare
  // "y" that matched nearly every line (run 5's B3). Empty alternatives are still dropped ("syu\|").
  const extended = /-[A-Za-z]*E/.test(flags) || /\begrep\b/.test(cmd);
  const source = (extended ? g[3] : breToJs(g[3]))
    .replace(/(?<!\\)\|(?=\||(?<!\\)\)|$)/g, "").replace(/(^|(?<!\\)\()\|/g, "$1");
  if (!source || source === "()") return;
  let re: RegExp;
  try { re = new RegExp(source, ignoreCase ? "i" : ""); } catch { return; }

  const lines = fs.readFileSync(file, "utf8").split("\n");
  const out: string[] = [];
  let matches = 0, lastShown = -1;
  for (let i = 0; i < lines.length && matches < 40; i++) {
    if (!re.test(lines[i])) continue;
    matches++;
    const from = Math.max(i - before, lastShown + 1);
    const to = Math.min(i + after, lines.length - 1);
    if (out.length && from > lastShown + 1) out.push("--");
    for (let j = from; j <= to; j++) out.push(`${j + 1}${j === i ? ":" : "-"} ${lines[j].slice(0, 300)}`);
    lastShown = to;
  }
  return (
    `(Ran this search for you on the saved page.) grep "${g[3]}" in ${file}: ${matches} match(es)` +
    (out.length ? `\n${out.join("\n")}\nUse read with offset to see more around a match.` : ".")
  );
}

// "!gh foo" -> "foo site:github.com": the search services don't know SearXNG bangs.
// Several bangs ("!st !su !gh") use the first one that has a site; bangs without one are removed.
// Only bangs at the start count, so "what is !important in css" is left alone.
function bangsToSite(query: string): string {
  // "\"!gh foo bar\"": the whole query got quoted, bang included. Unquote it.
  const quoted = query.match(/^\s*"(!!?[\w.-]+\s[^"]*)"\s*$/);
  if (quoted) query = quoted[1];
  const lead = query.match(/^\s*(?:!!?[\w.-]+\s*)+/);
  if (!lead) return query;
  const rest = query.slice(lead[0].length).trim();
  if (!rest) return query.replace(/!+/g, "").trim() || query; // only bangs: search their words
  const site = lead[0].trim().split(/\s+/).map((b) => BANG_SITES[b.replace(/^!+/, "").toLowerCase()]).find(Boolean);
  return site && !/(^|\s)site:\S+/i.test(rest) ? `${rest} site:${site}` : rest;
}

function textOf(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((p: any) => (typeof p === "string" ? p : p?.text ?? "")).join("\n");
  return "";
}

const SEARCH_TOPIC_STOP = new Set([
  "the", "and", "for", "with", "from", "that", "this", "what", "when", "where", "why", "who",
  "how", "does", "did", "was", "were", "are", "its", "into", "about", "using", "use",
  "or", "not", "in", "on", "of", "to", "by", "as", "at", "is", "be",
  "site", "com", "org", "net", "io", "ai", "dev", "www", "http", "https",
  "github", "docs", "documentation", "official", "release", "released", "date", "dates", "version", "latest",
  "launch", "launched", "announcement", "announced", "public", "initial", "first", "created", "founded",
  "history", "changelog", "notes", "alpha", "beta", "new", "update", "news", "blog", "post",
  "pricing", "price", "review", "overview", "comparison", "vs",
  "search", "engine", "api", "serp", "web", "service", "services", "tool", "tools", "platform", "app", "software", "website",
]);

function searchTopicTokens(query: string): Set<string> {
  const words = query
    .toLowerCase()
    .replace(/\bsite:[^\s]+/g, " ")
    .split(/[^\p{L}\p{N}]+/u)
    .filter((w) => w.length >= 2 && !SEARCH_TOPIC_STOP.has(w) && !/^(?:19|20)\d{2}$/.test(w));
  return new Set(words);
}

function isSameSearchTopic(a: Set<string>, b: Set<string>): boolean {
  if (!a.size || !b.size) return false;
  let common = 0;
  for (const t of a) if (b.has(t)) common++;
  if (common === 0) return false;
  const union = a.size + b.size - common;
  const jaccard = common / union;
  const overlap = common / Math.min(a.size, b.size);
  return jaccard >= 0.5 || (common >= 2 && overlap >= 0.6);
}

// Terms in the query or user's message that indicate a historical, date-sensitive,
// or era-specific lookup where years in the search query are genuinely needed.
const YEAR_NEEDED_RE = new RegExp("\\b(?:" + [
  // Release / creation / launch dates ("release date"/"released", not bare "release" so "latest release 2024" still drops the year)
  String.raw`release\s+dates?|released|initial\s+release|first\s+release|first\s+version|first\s+commit|initial\s+commit|commit\s+history`,
  String.raw`(?:release|version)\s+history|release\s+notes|change\s*log|launch(?:ed)?|debut(?:ed)?|announc(?:ed|ement)|introduc(?:ed|tion)`,
  String.raw`found(?:ed|ing)|creat(?:ed|ion\s+date)|inception|origin(?:s|ally|al)?|born|started\s+in|fork(?:ed)?|rebrand(?:ed)?|renam(?:ed)?`,
  // Deprecation / lifecycle
  String.raw`deprecat(?:ed|ion)|end\s+of\s+life|eol|discontinu(?:ed|ation)|sunset(?:ted)?|archiv(?:ed|e)|legacy`,
  // Timeline / historical / "when" questions
  String.raw`histor(?:y|ical)|timeline|chronolog(?:y|ical)|retrospective|wayback|era|decade`,
  String.raw`when\s+(?:was|were|did)|what\s+year|which\s+year|since\s+when|how\s+old|earliest|oldest|previously|formerly|back\s+in|at\s+the\s+time`,
  // Dated publications, specs, security advisories, events
  String.raw`publish(?:ed|ing)|publication\s+date|arxiv|rfc\s*\d+|cve-\d+|advisory|breach|outage|incident`,
  String.raw`acquir(?:ed|ing)|acquisition|ipo|patent(?:ed)?|election|olympics|world\s+cup|championship|award|oscar|nobel|treaty|ruling|verdict`,
  // Common non-English equivalents (es, fr, de, pt)
  String.raw`fecha\s+de\s+(?:lanzamiento|salida|creaci[oó]n)|primera\s+versi[oó]n|lanzad[oa]|historia`,
  String.raw`date\s+de\s+(?:sortie|lancement|cr[eé]ation)|premi[eè]re\s+version|histoire`,
  String.raw`ver[oö]ffentlich(?:ung|t)|erstver[oö]ffentlichung|geschichte`,
  String.raw`data\s+de\s+lan[çc]amento|primeira\s+vers[aã]o|hist[oó]ria`,
].join("|") + ")\\b", "i");

function dropUnaskedYears(query: string, userText: string): string {
  if (YEAR_NEEDED_RE.test(query) || YEAR_NEEDED_RE.test(userText)) return query;
  const asked = new Set((userText.match(/\b(19|20)\d{2}\b/g) ?? []));
  const out = query
    .replace(/(^|\s)((?:19|20)\d{2})(?=\s|$)/g, (m, lead, year) => (asked.has(year) ? m : lead))
    .replace(/\s{2,}/g, " ")
    .trim();
  return out || query;
}

// For `cat/head/grep <file under ~/.pi>` (also `cd <dir> && cat <name>`),
// read the file here and return its text (or the grep matches).
function readPiFileForBash(cmd: string): string | undefined {
  const home = os.homedir();
  const expand = (p: string) => p.replace(/^~(?=\/)/, home).replace(/^\$HOME(?=\/)/, home);
  const piPath = /(?:~|\$HOME|\/home\/[^/\s"']+)\/\.pi\/[^\s"'|;&)]*/g;
  const candidates = (cmd.match(piPath) ?? []).map(expand);

  // cd <dir> && cat package.json
  const cd = cmd.match(/\bcd\s+["']?((?:~|\$HOME|\/home\/[^/\s"']+)\/\.pi\/[^\s"';&]*)/);
  const rel = cmd.match(/\b(?:cat|head|tail|less|grep[^|;&]*?)\s+["']?([\w.-]+\.(?:json|md|ts|js|txt|ya?ml))\b/);
  if (cd && rel) candidates.unshift(path.join(expand(cd[1]), rel[1]));

  for (const file of candidates) {
    let text: string;
    try {
      if (!fs.statSync(file).isFile()) continue;
      text = fs.readFileSync(file, "utf8");
    } catch { continue; }

    const g = cmd.match(/\bgrep\s+((?:-[A-Za-z]+\s+)*)(['"])(.+?)\2/);
    if (g) {
      let re: RegExp | undefined;
      try { re = new RegExp(g[3].replace(/\\\|/g, "|"), /i/.test(g[1]) ? "i" : ""); } catch {}
      if (re) {
        const hits = text.split("\n").map((l, i) => `${i + 1}: ${l}`).filter((l) => re!.test(l)).slice(0, 40);
        return `(bash can't see ~/.pi, so here is grep "${g[3]}" on ${file}, run for you:)\n` +
          (hits.length ? hits.join("\n") : "no matches");
      }
    }
    const lines = text.split("\n");
    const head = lines.slice(0, 80).join("\n").slice(0, 6000);
    return `(bash can't see ~/.pi, so here is ${file}, read for you${lines.length > 80 ? `, first 80 of ${lines.length} lines` : ""}:)\n${head}`;
  }
  return undefined;
}

const CAPTCHA_RECAPTCHA_SELECTOR = 'iframe[title="reCAPTCHA"]';
const CAPTCHA_LEGACY_SELECTOR = "#captcha";
const CAPTCHA_IFRAME_SELECTOR = `${CAPTCHA_RECAPTCHA_SELECTOR}, ${CAPTCHA_LEGACY_SELECTOR}`;
const CAPTCHA_READY_SELECTOR = "#recaptcha-token";
const CAPTCHA_ANCHOR_SELECTOR = "#recaptcha-anchor";
const CAPTCHA_DETECT_MS = Number(process.env.PI_CAPTCHA_DETECT_MS) || 2000;
const CAPTCHA_READY_MS = Number(process.env.PI_CAPTCHA_READY_MS) || 120000;
const CAPTCHA_POLL_MS = Number(process.env.PI_CAPTCHA_POLL_MS) || 1000;
const CAPTCHA_REDIRECT_MS = Number(process.env.PI_CAPTCHA_REDIRECT_MS) || 15000;
const CAPTCHA_POST_SOLVE_REDIRECT_MS = Number(process.env.PI_CAPTCHA_POST_SOLVE_REDIRECT_MS) || 3000;
const CAPTCHA_LOAD_MS = Number(process.env.PI_CAPTCHA_LOAD_MS) || 15000;

const TURNSTILE_WIDGET_SELECTOR = '.cf-turnstile';
const TURNSTILE_RESPONSE_SELECTOR = 'input[name="cf-turnstile-response"]';
const TURNSTILE_SCRIPT_RE = /script\[src.*challenges\.cloudflare\.com\/turnstile/;
const TURNSTILE_DETECT_MS = Number(process.env.PI_TURNSTILE_DETECT_MS) || 5000;
const TURNSTILE_READY_MS = Number(process.env.PI_TURNSTILE_READY_MS) || 120000;
const TURNSTILE_POLL_MS = Number(process.env.PI_TURNSTILE_POLL_MS) || 1000;
const TURNSTILE_REDIRECT_MS = Number(process.env.PI_TURNSTILE_REDIRECT_MS) || 15000;
const TURNSTILE_POST_SOLVE_REDIRECT_MS = Number(process.env.PI_TURNSTILE_POST_SOLVE_REDIRECT_MS) || 3000;
const TURNSTILE_LOAD_MS = Number(process.env.PI_TURNSTILE_LOAD_MS) || 15000;
const MANAGED_SESSION_RE = /^(?:pi-root-|piab-)[A-Za-z0-9_-]+$/;
const AGENT_BROWSER_BIN = process.env.AGENT_BROWSER_BIN ?? "agent-browser";

export type AgentBrowserRun = (
  args: string[],
  timeoutMs: number,
) => Promise<{ ok: boolean; stdout: string; stderr: string }>;

function sessionNameOf(event: any): string | undefined {
  const name = event?.details?.sessionName ?? event?.details?.data?.sessionName;
  return typeof name === "string" && MANAGED_SESSION_RE.test(name) ? name : undefined;
}

function hasSessionMarker(dir: string, session: string): boolean {
  return fs.existsSync(path.join(dir, `${session}.sock`)) || fs.existsSync(path.join(dir, `${session}.target`));
}

function findSessionSocketDir(session: string, hintDir?: unknown): string | undefined {
  const candidates: string[] = [];
  if (typeof hintDir === "string" && hintDir) candidates.push(hintDir);
  if (process.env.AGENT_BROWSER_SOCKET_DIR) candidates.push(process.env.AGENT_BROWSER_SOCKET_DIR);
  if (typeof process.getuid === "function") candidates.push(path.join(os.tmpdir(), `piab-${process.getuid()}`));
  for (const dir of candidates) {
    if (hasSessionMarker(dir, session)) return dir;
  }
  // Fallback: locate the running daemon's private socket dir via /proc (Linux).
  try {
    for (const entry of fs.readdirSync("/proc")) {
      if (!/^\d+$/.test(entry)) continue;
      try {
        const environ = fs.readFileSync(`/proc/${entry}/environ`, "utf8");
        if (!environ.includes("AGENT_BROWSER_DAEMON=") || !environ.includes(`AGENT_BROWSER_SESSION=${session}\0`)) continue;
        for (const kv of environ.split("\0")) {
          if (kv.startsWith("AGENT_BROWSER_SOCKET_DIR=")) {
            const dir = kv.slice("AGENT_BROWSER_SOCKET_DIR=".length);
            if (dir && hasSessionMarker(dir, session)) return dir;
          }
        }
      } catch {}
    }
  } catch {}
  return undefined;
}

function runSessionBrowser(
  session: string,
  socketDir: string,
  args: string[],
  timeoutMs: number,
): Promise<{ ok: boolean; stdout: string; stderr: string }> {
  const env = {
    ...process.env,
    AGENT_BROWSER_SOCKET_DIR: socketDir,
    AGENT_BROWSER_DEFAULT_TIMEOUT: String(timeoutMs),
  };
  return new Promise((resolve) => {
    execFile(
      AGENT_BROWSER_BIN,
      ["--session", session, ...args],
      { timeout: timeoutMs + 3000, env },
      (err, stdout, stderr) => {
        resolve({ ok: !err, stdout: String(stdout ?? ""), stderr: String(stderr ?? "") });
      },
    );
  });
}

function parseBrowserField(stdout: string, field: "url" | "title" | "value" | "count"): string {
  const trimmed = stdout.trim();
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed);
      const val =
        parsed?.data?.[field] ??
        parsed?.[field] ??
        (field === "value" ? (parsed?.data?.result ?? parsed?.data?.attr ?? parsed?.result ?? parsed?.attr) : undefined);
      if (typeof val === "string") return val.trim();
      if (typeof val === "number") return String(val);
    } catch {}
  }
  return trimmed;
}

function parseEvalJson<T>(stdout: string): T | undefined {
  const trimmed = stdout.trim();
  if (!trimmed) return undefined;
  try {
    let val: any = JSON.parse(trimmed);
    if (val && typeof val === "object" && "data" in val && val.data && typeof val.data === "object" && "result" in val.data) {
      val = val.data.result;
    }
    if (typeof val === "string") val = JSON.parse(val);
    if (val && typeof val === "object") return val as T;
  } catch {}
  return undefined;
}

const CAPTCHA_MAIN_PROBE_JS =
  `JSON.stringify((() => {` +
  ` const resps = Array.from(document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response'));` +
  ` let token = resps.map(e => (e && typeof e.value === 'string' ? e.value.trim() : '')).find(Boolean) || '';` +
  ` let hasApi = false;` +
  ` try { if (typeof grecaptcha !== 'undefined' && typeof grecaptcha.getResponse === 'function') { hasApi = true; token = token || String(grecaptcha.getResponse() || '').trim(); } } catch {}` +
  ` return { url: String(window.location.href || ''), hasIframe: !!document.querySelector(${JSON.stringify(CAPTCHA_IFRAME_SELECTOR)}), hasHostSignal: resps.length > 0 || hasApi, token };` +
  `})())`;

type CaptchaMainProbe = { url: string; hasIframe: boolean; hasHostSignal: boolean; token: string };

const TURNSTILE_PROBE_JS =
  `JSON.stringify((() => {` +
  ` const url = String(window.location.href || '');` +
  ` const title = String(document.title || '').trim();` +
  ` const resps = Array.from(document.querySelectorAll('input[name="cf-turnstile-response"]'));` +
  ` let token = resps.map(e => (e && typeof e.value === 'string' ? e.value.trim() : '')).find(Boolean) || '';` +
  ` try { if (typeof turnstile !== 'undefined' && typeof turnstile.getResponse === 'function') { token = token || String(turnstile.getResponse() || '').trim(); } } catch {}` +
  ` const hasInterstitial = !!document.querySelector('#challenge-running, #challenge-stage, #challenge-form, #turnstile-wrapper, form[action*="__cf_chl_"], script[src*="/cdn-cgi/challenge-platform/"]') || /[?&]__cf_chl_/.test(url);` +
  ` const hasWidget = resps.length > 0 || !!document.querySelector('.cf-turnstile, script[src*="challenges.cloudflare.com/turnstile"], iframe[src*="challenges.cloudflare.com"]');` +
  ` return { url, title, hasInterstitial, hasWidget, token };` +
  `})())`;

type TurnstileProbe = { url: string; title: string; hasInterstitial: boolean; hasWidget: boolean; token: string };

export async function detectTurnstile(
  run: AgentBrowserRun,
  fallbackUrl?: string,
): Promise<{ present: true; kind: "interstitial" | "widget"; solved: boolean; url: string; title?: string } | undefined> {
  const res = await run(["eval", TURNSTILE_PROBE_JS], 3000);
  if (!res.ok) return undefined;
  const probe = parseEvalJson<TurnstileProbe>(res.stdout);
  if (!probe || (!probe.hasInterstitial && !probe.hasWidget)) return undefined;
  const kind: "interstitial" | "widget" = probe.hasInterstitial ? "interstitial" : "widget";
  const solved = kind === "widget" && probe.token.length > 0;
  return {
    present: true,
    kind,
    solved,
    url: probe.url || fallbackUrl || "",
    title: probe.title || undefined,
  };
}

export async function waitForCaptchaRedirect(
  run: AgentBrowserRun,
  fallbackUrl?: string,
  opts?: { pollMs?: number; readyMs?: number },
): Promise<{ url: string; title?: string; redirected: boolean } | undefined> {
  // 1. Wait briefly for a reCAPTCHA iframe (`iframe[title="reCAPTCHA"]` or `#captcha`) to attach.
  const detected = await run(["wait", CAPTCHA_IFRAME_SELECTOR, "--state", "attached"], CAPTCHA_DETECT_MS);
  if (!detected.ok) return undefined;

  // 2. Capture the current top-level URL and initial host-page reCAPTCHA state.
  const before = await run(["get", "url", "--json"], 5000);
  const initialUrl = (before.ok ? parseBrowserField(before.stdout, "url") : "") || fallbackUrl || "";

  const initProbeRes = await run(["eval", CAPTCHA_MAIN_PROBE_JS], 5000);
  const initProbe = initProbeRes.ok ? parseEvalJson<CaptchaMainProbe>(initProbeRes.stdout) : undefined;
  const initialToken = initProbe?.token ?? "";
  // If the host page already has a non-empty reCAPTCHA token (e.g. a subsequent click after solving), don't block again.
  if (initialToken.length > 0) return undefined;

  let solved = false;
  let detached = false;
  let redirectedDuringPoll = false;
  const pollMs = opts?.pollMs ?? CAPTCHA_POLL_MS;
  const readyMs = opts?.readyMs ?? CAPTCHA_READY_MS;
  const deadline = Date.now() + readyMs;

  if (initProbe?.hasHostSignal) {
    // 3a. Standard cross-origin Google reCAPTCHA v2: agent-browser `frame <css>` cannot enter OOPIFs,
    //     so poll the host page's `textarea[name="g-recaptcha-response"]` / `grecaptcha.getResponse()`,
    //     URL changes, and iframe detachment directly in the main frame.
    while (Date.now() <= deadline) {
      const probeRes = await run(["eval", CAPTCHA_MAIN_PROBE_JS], 5000);
      if (probeRes.ok) {
        const probe = parseEvalJson<CaptchaMainProbe>(probeRes.stdout);
        if (probe) {
          if (probe.url && initialUrl && probe.url !== initialUrl) {
            redirectedDuringPoll = true;
            break;
          }
          if (probe.token.length > 0 && probe.token !== initialToken) {
            solved = true;
            break;
          }
          if (!probe.hasIframe) {
            detached = true;
            break;
          }
        }
      } else if (/destroyed|detached|closed|navigat/i.test(probeRes.stderr)) {
        detached = true;
        break;
      }
      if (Date.now() >= deadline) break;
      if (pollMs > 0) await new Promise((r) => setTimeout(r, pollMs));
    }
  } else {
    // 3b. Fallback for same-origin / custom `#captcha` iframes without host `g-recaptcha-response`:
    //     pick a single concrete selector for `frame`, poll `#recaptcha-anchor` and `#recaptcha-token`,
    //     and always return to `frame main`.
    const recaptchaCount = await run(["get", "count", CAPTCHA_RECAPTCHA_SELECTOR, "--json"], 3000);
    const frameSelector =
      recaptchaCount.ok && Number(parseBrowserField(recaptchaCount.stdout, "count")) > 0
        ? CAPTCHA_RECAPTCHA_SELECTOR
        : CAPTCHA_LEGACY_SELECTOR;
    try {
      const framed = await run(["frame", frameSelector], 5000);
      if (framed.ok) {
        const attached = await run(["wait", CAPTCHA_READY_SELECTOR, "--state", "attached"], 5000);
        if (attached.ok) {
          const initTokRes = await run(["get", "value", CAPTCHA_READY_SELECTOR, "--json"], 3000);
          const initFrameToken = initTokRes.ok ? parseBrowserField(initTokRes.stdout, "value") : "";
          while (Date.now() <= deadline) {
            const checkedRes = await run(["get", "attr", CAPTCHA_ANCHOR_SELECTOR, "aria-checked", "--json"], 3000);
            if (checkedRes.ok && parseBrowserField(checkedRes.stdout, "value") === "true") {
              solved = true;
              break;
            }
            const tokRes = await run(["get", "value", CAPTCHA_READY_SELECTOR, "--json"], 3000);
            if (tokRes.ok) {
              const curToken = parseBrowserField(tokRes.stdout, "value");
              if (curToken.length > 0 && curToken !== initFrameToken) {
                solved = true;
                break;
              }
            } else if (/destroyed|detached|closed|not found/i.test(tokRes.stderr)) {
              detached = true;
              break;
            }
            if (Date.now() >= deadline) break;
            if (pollMs > 0) await new Promise((r) => setTimeout(r, pollMs));
          }
        } else if (/destroyed|detached|closed/i.test(attached.stderr)) {
          detached = true;
        }
      }
    } finally {
      await run(["frame", "main"], 5000);
    }
  }

  // 4. Wait for the top-level URL to change (briefly if solved in-place, or up to CAPTCHA_REDIRECT_MS if detached).
  if (initialUrl && !redirectedDuringPoll && (solved || detached)) {
    const redirectWaitMs = solved ? CAPTCHA_POST_SOLVE_REDIRECT_MS : CAPTCHA_REDIRECT_MS;
    await run(["wait", "--fn", `window.location.href !== ${JSON.stringify(initialUrl)}`], redirectWaitMs);
  }

  // 5. Read the final URL and title.
  const afterUrl = await run(["get", "url", "--json"], 5000);
  const finalUrl = (afterUrl.ok ? parseBrowserField(afterUrl.stdout, "url") : "") || initialUrl;
  const redirected = Boolean(finalUrl && initialUrl && finalUrl !== initialUrl);
  if (redirected) {
    await run(["wait", "--load", "load"], CAPTCHA_LOAD_MS);
  }
  if (!redirected && !solved) return undefined;

  const afterTitle = await run(["get", "title", "--json"], 5000);
  const finalTitle = afterTitle.ok ? parseBrowserField(afterTitle.stdout, "title") : undefined;
  return { url: finalUrl, title: finalTitle || undefined, redirected };
}

// Cloudflare Turnstile waiting:
//   Detects .cf-turnstile widget or cf-turnstile-response field in the light DOM,
//   polls for the hidden input's value to change from "" to a non-empty token string,
//   then waits for any top-level URL redirect before returning control.
export async function waitForTurnstileRedirect(
  run: AgentBrowserRun,
  fallbackUrl?: string,
  opts?: { pollMs?: number; readyMs?: number },
): Promise<{ url: string; title?: string; redirected: boolean } | undefined> {
  // 1. Wait briefly for the Turnstile response field or widget container to appear.
  const detected = await run(
    ["wait", `${TURNSTILE_WIDGET_SELECTOR}, ${TURNSTILE_RESPONSE_SELECTOR}`, "--state", "attached"],
    TURNSTILE_DETECT_MS,
  );
  if (!detected.ok) return undefined;

  // 2. Capture the current top-level URL and initial token state.
  const before = await run(["get", "url", "--json"], 5000);
  const initialUrl = (before.ok ? parseBrowserField(before.stdout, "url") : "") || fallbackUrl || "";

  // Probe: read the hidden response field value in the main frame.
  const TURNSTILE_INIT_PROBE_JS =
    `JSON.stringify((() => {` +
    ` const url = String(window.location.href || '');` +
    ` const resps = Array.from(document.querySelectorAll(${JSON.stringify(TURNSTILE_RESPONSE_SELECTOR)}));` +
    ` let token = resps.map(e => (e && typeof e.value === 'string' ? e.value.trim() : '')).find(Boolean) || '';` +
    ` try { if (typeof turnstile !== 'undefined' && typeof turnstile.getResponse === 'function') { token = token || String(turnstile.getResponse() || '').trim(); } } catch {}` +
    ` const hasWidget = !!document.querySelector(${JSON.stringify(TURNSTILE_WIDGET_SELECTOR)}) || resps.length > 0;` +
    ` return { url, token, hasWidget };` +
    `})())`;

  const initProbeRes = await run(["eval", TURNSTILE_INIT_PROBE_JS], 5000);
  const initProbe = initProbeRes.ok ? parseEvalJson<{ url: string; token: string; hasWidget: boolean }>(initProbeRes.stdout) : undefined;
  const initialToken = initProbe?.token ?? "";

  // If the response field already has a non-empty token, Turnstile is already solved — don't block.
  if (initialToken.length > 0) return undefined;

  let solved = false;
  let widgetGone = false;
  let redirectedDuringPoll = false;
  const pollMs = opts?.pollMs ?? TURNSTILE_POLL_MS;
  const readyMs = opts?.readyMs ?? TURNSTILE_READY_MS;
  const deadline = Date.now() + readyMs;

  // 3. Poll the hidden response field for a token change.
  while (Date.now() <= deadline) {
    const probeRes = await run(["eval", TURNSTILE_INIT_PROBE_JS], 5000);
    if (probeRes.ok) {
      const probe = parseEvalJson<{ url: string; token: string; hasWidget: boolean }>(probeRes.stdout);
      if (probe) {
        // URL changed during poll → redirect happened.
        if (probe.url && initialUrl && probe.url !== initialUrl) {
          redirectedDuringPoll = true;
          break;
        }
        // Token appeared or changed → solved.
        if (probe.token.length > 0 && probe.token !== initialToken) {
          solved = true;
          break;
        }
        // Widget/container gone from DOM → detached.
        if (!probe.hasWidget) {
          widgetGone = true;
          break;
        }
      }
    } else if (/destroyed|detached|closed|navigat/i.test(probeRes.stderr)) {
      widgetGone = true;
      break;
    }
    if (Date.now() >= deadline) break;
    if (pollMs > 0) await new Promise((r) => setTimeout(r, pollMs));
  }

  // 4. Wait for the top-level URL to change (briefly if solved in-place, or up to TURNSTILE_REDIRECT_MS if widget detached).
  if (initialUrl && !redirectedDuringPoll && (solved || widgetGone)) {
    const redirectWaitMs = solved ? TURNSTILE_POST_SOLVE_REDIRECT_MS : TURNSTILE_REDIRECT_MS;
    await run(["wait", "--fn", `window.location.href !== ${JSON.stringify(initialUrl)}`], redirectWaitMs);
  }

  // 5. Read the final URL and title.
  const afterUrl = await run(["get", "url", "--json"], 5000);
  const finalUrl = (afterUrl.ok ? parseBrowserField(afterUrl.stdout, "url") : "") || initialUrl;
  const redirected = Boolean(finalUrl && initialUrl && finalUrl !== initialUrl);
  if (redirected) {
    await run(["wait", "--load", "load"], TURNSTILE_LOAD_MS);
  }
  if (!redirected && !solved) return undefined;

  const afterTitle = await run(["get", "title", "--json"], 5000);
  const finalTitle = afterTitle.ok ? parseBrowserField(afterTitle.stdout, "title") : undefined;
  return { url: finalUrl, title: finalTitle || undefined, redirected };
}