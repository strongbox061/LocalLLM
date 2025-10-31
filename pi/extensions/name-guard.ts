// name-guard.ts
// Location: ~/.pi/agent/extensions/name-guard.ts
//
// The model sometimes writes file names into notes and plans that exist nowhere: a log file pattern it made up
// ("<date>-log.md"), a doc it thinks the project should have. AGENTS.md asks it not to, but that doesn't hold, so
// this extension checks each `edit` and `write` of a text document (.md, .txt, ...) before it runs.
//
// Every file name the change adds (one that isn't in the file already) must be one of:
//  - a file that exists: next to the edited file, under the working directory, or with that name anywhere in the
//    project (git ls-files, tracked or untracked);
//  - the file being written;
//  - a name from your messages or from a tool result in this session (a file the model listed, read or searched);
//  - a name the model proposed in its last answer, when you replied just "yes" / "ok" / "go ahead".
// Otherwise the change is blocked and the model is told to use a real name or ask you what to call the file.
//
// Code files are not checked: code that creates new files by name is normal. Nor is PLAN.md: a plan proposes
// files that don't exist yet (code, config, logs, docs) and goes to you for review before anything is built.

import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const DEBUG = false;

// Documents whose new file names are checked.
const CHECKED_DOC_RE = /\.(md|markdown|txt|rst|adoc|org)$/i;

// Plans propose files that don't exist yet and are reviewed by you before anything is built.
const PLAN_RE = /(?:^|\/)PLAN\.md$/i;

// A file name: a path or name ending in a known extension. Not part of a URL, not ".md" alone, not glued to a
// template's `${...}` or `*`.
const EXT = "md|markdown|txt|rst|adoc|org|log|csv|ts|tsx|js|mjs|cjs|jsx|json|jsonl|ya?ml|toml|ini|conf|cfg|py|sh|css|html|env|sql|xml|service|timer";
const NAME_RE = new RegExp(`(?<![\\w./~<>$}*@-])@?([\\w./~<>-]*[\\w<>-]\\.(?:${EXT}))(?![\\w.-]*[\\w])`, "gi");
const URL_RE = /\b[a-z][\w+.-]*:\/\/\S+/gi;
// Library and product names that look like file names.
const NOT_FILES_RE = /^(node|next|vue|nuxt|express|three|chart|d3|p5|alpine|ember|backbone|react|socket\.io|deno|bun)\.js$/i;

// A short "yes" to the model's proposal: the names in that proposal count as yours.
const AGREE_RE = /^\s*(yes|yep|yeah|ok(ay)?|sure|go ahead|do it|apply( it)?|proceed|sounds good|lgtm|looks good)\b/i;

export function fileNames(text: string): string[] {
  const out = new Set<string>();
  const t = String(text ?? "").replace(URL_RE, " ");
  for (const m of t.matchAll(NAME_RE)) {
    const name = m[1].replace(/^\.\//, "");
    if (!NOT_FILES_RE.test(name)) out.add(name);
  }
  return [...out];
}

function textOf(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((p) => (typeof p === "string" ? p : textOf(p?.text ?? p?.content))).join("\n");
  return "";
}

// Base names of the project's files (tracked and untracked, not ignored), cached for a few seconds.
let projectCache: { cwd: string; at: number; names: Set<string> } | undefined;
function projectNames(cwd: string): Set<string> {
  if (projectCache && projectCache.cwd === cwd && Date.now() - projectCache.at < 10_000) return projectCache.names;
  const names = new Set<string>();
  try {
    const out = execFileSync("git", ["ls-files", "-co", "--exclude-standard"], { cwd, encoding: "utf8", timeout: 3000, maxBuffer: 64 << 20, stdio: ["ignore", "pipe", "ignore"] });
    for (const f of out.split("\n")) if (f) names.add(path.basename(f));
  } catch {}
  projectCache = { cwd, at: Date.now(), names };
  return names;
}

export function fileNames(text: string): string[] {
  const out = new Set<string>();
  const t = String(text ?? "").replace(URL_RE, " ");
  for (const m of t.matchAll(NAME_RE)) {
    const name = m[1].replace(/^\.\//, "");
    if (!NOT_FILES_RE.test(name)) out.add(name);
  }
  return [...out];
}

// The names in `added` that nothing backs up.
export function unknownNames(added: string[], known: Set<string>, opts: { cwd: string; target: string }): string[] {
  const targetAbs = resolvePath(opts.target, opts.cwd);
  const dirs = [path.dirname(targetAbs), opts.cwd];
  return added.filter((name) => {
    const bare = name.replace(/^@/, "");
    const base = path.basename(bare);
    if (known.has(bare) || known.has(base)) return false;
    if (base === path.basename(targetAbs)) return false;
    if (!/[<>]/.test(bare) && dirs.some((d) => { try { return fs.existsSync(resolvePath(bare, d)); } catch { return false; } })) return false;
    if (projectNames(opts.cwd).has(base)) return false;
    return true;
  });
}

export default function (pi: any) {
  const known = new Set<string>();
  let lastUser = "";
  const learn = (text: string) => { for (const n of fileNames(text)) { known.add(n); known.add(path.basename(n)); } };

  pi.on("session_start", () => { known.clear(); lastUser = ""; });

  // What tools returned (a listing, a file read, a search).
  pi.on("tool_result", (event: any) => { if (!event?.isError) learn(textOf(event?.content)); });

  // Also everything in the request except the model's own text (covers resumed sessions), and the names in the model's last
  // answer when your reply to it is a plain "yes".
  pi.on("before_provider_request", (event: any) => {
    const msgs: any[] = event?.payload?.messages ?? [];
    for (const m of msgs) if (m?.role !== "assistant" && m?.role !== "system") learn(textOf(m?.content));
    if (!AGREE_RE.test(lastUser) || lastUser.length > 80) return;
    const i = msgs.map((m) => m?.role === "user" && textOf(m?.content).trim() === lastUser.trim()).lastIndexOf(true);
    for (let j = i - 1; j >= 0; j--) {
      if (msgs[j]?.role !== "assistant") continue;
      const text = textOf(msgs[j]?.content);
      if (text.trim()) { learn(text); break; }
    }
  });

  pi.on("tool_call", (event: any, ctx: any) => {
    const tool = event?.toolName;
    if (tool !== "edit" && tool !== "write") return;
    const input = event.input ?? {};
    const target = input.path ?? input.file_path;
    if (typeof target !== "string" || !CHECKED_DOC_RE.test(target) || PLAN_RE.test(target)) return;
    const cwd = ctx?.cwd ?? process.cwd();

    let current = "";
    try { current = fs.readFileSync(resolvePath(target, cwd), "utf8"); } catch {}
    const already = new Set(fileNames(current));

    const newTexts: string[] = [];
    if (tool === "write") newTexts.push(String(input.content ?? ""));
    else for (const e of Array.isArray(input.edits) ? input.edits : [input]) {
      for (const key of ["newText", "new_text", "new_string"]) if (typeof e?.[key] === "string") newTexts.push(e[key]);
    }
    const added = fileNames(newTexts.join("\n")).filter((n) => !already.has(n));
    const bad = unknownNames(added, known, { cwd, target });
    if (DEBUG) console.error(`[name-guard] ${tool} ${target}: added ${added.join(", ") || "-"}; unknown ${bad.join(", ") || "-"}`);
    if (bad.length === 0) return;

    const lookupHow = event?.parentToolCallId ? "tools.ls or tools.find" : "ls or find";
    return {
      block: true,
      reason:
        `Not applied. ${bad.map((n) => `"${n}"`).join(", ")} ${bad.length === 1 ? "is" : "are"} not a file in the project, ` +
        "and the user and the tool results never named it: you made it up. Don't invent file names. " +
        `Use an existing file's exact name (look it up with ${lookupHow} first), or leave the name out and ask the user ` +
        "what the file should be called.",
    };
  });
}
