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

// What the person sent, from Pi's `input` event: typed text, `pi -p`, RPC, or an extension's sendUserMessage (the