// edit-guard.ts
// Location: ~/.pi/agent/extensions/edit-guard.ts
//
// Pi may only change files when your latest message asks for a change.
// Otherwise `edit`, `write`, and file-changing bash commands are blocked,
// and the model is told to describe the change and ask you first.
// Reply "yes" / "go ahead" / "do it" (or ask for the change) to allow it.
// In a subagent (subagent/index.ts), the task text was written by the model, so it doesn't count: the child gets
// your latest message from the main session in LOCALLLM_PERSON_MESSAGE and goes by that.
// `write` replaces a whole file and, unlike `edit`, checks nothing against what's there: on a file that exists it is
// allowed only after this session read all of it (or wrote it), so a file can't be rewritten from memory or from a
// subagent's summary. Otherwise the model is told to use edit, or read the file first.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { QUESTION_START, has } from "./lang-words.ts";

// Words in your message that count as permission to change files.
const ALLOW_RE =
  /\b(edit|change|modify|fix|update|write|create|add|remove|delete|rename|move|refactor|implement|apply|perform|execute|carry out|replace|install|save|patch|rewrite|put|insert|append|include|document|record|note down|fill( in)?|draft|generate|convert|format|reorgani[sz]e|clean up|set up|go with|let'?s|make (it|the|a|this|these|those)|yes|yep|ok(ay)?|go ahead|do (it|them|that|(the|those|these) (changes|edits|updates|fixes))|proceed|continue|resume|keep going|try again|retry|sure)\b/i;
// A message that names a file (plan.md, @src/app.ts) and isn't a question
// is treated as an instruction about that file. A question: ends with ? ¿ ؟ ？ or starts with a question word,
// in 35 languages (QUESTION_START, from lang-words.ts).
const FILE_RE = /@?[\w./-]+\.(md|txt|ts|tsx|js|jsx|json|ya?ml|toml|py|sh|css|html|env|sql)\b/i;

// so asking for a plan lets the model save PLAN.md without unlocking edits to other files.
const PLAN_ASK_RE = /\b(?:plan(?:s|ned|ning)?|planea\w*|planifi\w*|planej\w*|pianific\w*|planen)\b/i;

export function allowsChanges(text: string): boolean {
  const t = text.trim();
  if (ALLOW_RE.test(t)) return true;
  return FILE_RE.test(t);
}

// bash commands that change files.
const BASH_WRITE_RE =
  /(^|[;&|]\s*)(rm|mv|cp|mkdir|rmdir|touch|chmod|chown|ln|truncate|tee|patch|git\s+(commit|checkout|reset|apply|restore|rm|mv|stash))\b|\bsed\s+(-[a-zA-Z]*i|--in-place)|(^|[^>&0-9])>>?\s*[^\s&|]|\b(npm|pnpm|yarn|pip3?)\s+(install|uninstall|add|remove)\b/;

const DEBUG = false;

// Pi's read tool ends a partial result with "Use offset=N to continue".
const PARTIAL_READ_RE = /Use offset=\d+ to continue/;

export function filePath(input: any, cwd: string): string | undefined {
  const p = input?.path ?? input?.file_path;
  if (typeof p !== "string" || !p) return undefined;
  const expanded = p === "~" || p.startsWith("~/") ? path.join(os.homedir(), p.slice(1)) : p;
  return path.resolve(cwd || process.cwd(), expanded);
}

// What the person sent, from Pi's `input` event: typed text, `pi -p`, RPC, or an extension's sendUserMessage (the
// end-of-answer checks) reach the model as user messages too, but never fire `input`. An Inbox reply's first line
export function personText(text: string): string {
  const t = String(text ?? "");
  if (PLAN_ASK_RE.test(t)) return t;
  if (ALLOW_RE.test(t) || FILE_RE.test(t)) return t;
  return "";
}

export default function (pi: any) {
  const fromParent = process.env.LOCALLLM_PERSON_MESSAGE;
  let lastUser = fromParent ?? "";

  // Remember your latest message (not the hidden ones other extensions add; see personText). Not in a subagent.
  pi.on("input", (event: any) => { if (fromParent === undefined) lastUser = personText(event?.text); });

  // Files this session has seen whole (read without offset/limit, not cut short) or written itself.
  const known = new Set<string>();
  pi.on("tool_result", (event: any, ctx: any) => {
    const tool = event?.toolName;
    if (event?.isError || (tool !== "read" && tool !== "write")) return;
    // Inside a codemode script, read isn't truncated and the script may only return a filtered slice to the model,
    // so don't treat it as having read the whole file for a later `write`.
    if (tool === "read" && event?.parentToolCallId) return;
    const file = filePath(event.input, ctx?.cwd);
    if (!file) return;
    const text = (event.content ?? []).map((p: any) => (typeof p?.text === "string" ? p.text : "")).join("\n");
    const whole = tool === "write" || (event.input?.offset === undefined && event.input?.limit === undefined && !PARTIAL_READ_RE.test(text));
    if (whole) known.add(file);
  });

  pi.on("tool_call", (event: any, ctx: any) => {
    const tool = event?.toolName;
    let changesFiles = tool === "edit" || tool === "write";
    if (tool === "bash") {
      // ignore output redirects to /dev/null and 2>&1
      const cmd = String(event.input?.command ?? "").replace(/\d?>>?\s*\/dev\/null|\d>&\d/g, "");
      changesFiles = BASH_WRITE_RE.test(cmd);
    }
    if (!changesFiles) return;

    const file = tool === "edit" || tool === "write" ? filePath(event.input, ctx?.cwd) : undefined;
    const isPlanWrite = Boolean(file && path.basename(file) === "PLAN.md" && PLAN_ASK_RE.test(lastUser));
    const allowed = allowsChanges(lastUser) || isPlanWrite;
    if (DEBUG) console.error(`[edit-guard] ${tool} ${allowed ? "allowed" : "BLOCKED"}`);
    if (!allowed) {
      return {
        block: true,
        reason:
          "The user hasn't asked for file changes. Don't change anything. " +
          "Briefly summarize the changes you would make (file and a few short bullets, no full diffs), then ask the user whether to apply them.",
      };
    }

    if (tool === "write") {
      if (file && !known.has(file) && fs.existsSync(file)) {
        const inCodemode = Boolean(event?.parentToolCallId);
        return {
          block: true,
          reason: inCodemode
            ? `${event.input?.path ?? file} already exists, so tools.write would replace the whole file from what you ` +
              "think it contains. Change it with tools.edit (exact oldText) instead."
            : `${event.input?.path ?? file} already exists and you haven't read all of it in this session, so write ` +
              "would replace it with what you think it contains. Change it with edit (exact old text), or read the " +
              "whole file first and then write it.",
        };
      }
    }
  });
}
