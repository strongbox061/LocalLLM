// cap-output.ts
// Location: ~/.pi/agent/extensions/cap-output.ts
//
// One tool result could fill half the context. Pi's own limit for bash and read is 2000 lines or 50 KB, written for
// models with 200k-token windows; here the window is 57,344 tokens and compaction starts at about 45,000. A plain
// `find -exec ls -l` over the repo (with .git) came back at Pi's 50 KB, which was 30,000 tokens (paths are about
// 1.7 characters per token), and the next turn compacted the whole session.
//
// So every result the model sees is cut to a fifth of the window in characters (11,468 for 57,344 tokens, so
// between about 3,000 and 7,000 tokens), and the full text is saved to a file named in the result:
// - bash keeps its first quarter and its end (errors and summaries are at the end);
// - read keeps the start, cut at a line, and ends with Pi's own "Use offset=N to continue", so the model reads on
//   and edit-guard.ts (loaded after this file) sees that the file wasn't read whole;
// - other tools keep the start and the last quarter.
// Calls made inside a codemode script (parentToolCallId set) are left alone: the script gets everything and filters it.
//
// codemode scripts run in QuickJS, not Node: the local model sometimes writes require("child_process") anyway, then
// gives up on codemode and falls back to a raw bash call (which is how the 30,000 tokens above came in). When a
// script fails that way, the result says how to do it with tools.bash / tools.read instead.
//
// read on a binary file (a database, an index, an archive) returns its bytes as text: about 2,500 tokens of noise
// for "the first 10 lines" of three backup databases. So read is refused before it runs on a file whose first 8 KB
// hold a NUL byte, with its size, what it looks like (SQLite, ZIP...) and how to look inside instead. Images (PNG,
// JPEG, GIF, WebP) are still read: Pi shows them to the model as pictures.
// The same bytes also come in through bash (head or cat on a database, often from inside a codemode script). In bash
// output, each run of binary lines (a NUL byte, or mostly control characters) becomes one line saying what it looks
// like and how to look inside; text around it (head's "==> file <==" headers, readable schema lines) stays. This
// applies inside codemode scripts too, so the script never prints the bytes.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const SHARE_OF_WINDOW = 0.2; // characters kept, as a share of the context window in tokens
const DEFAULT_WINDOW = 57344;
const MIN_CAP = 4000;

const NODE_API_RE =
  /\b(require|process|module|exports|Buffer|__dirname|__filename|fetch|setTimeout|setInterval) is not defined|Cannot find module|import (declarations|statements?) may only|Cannot use import statement/;

export const CODEMODE_HINT =
  "\n\n[codemode runs in a sandbox with no Node APIs: no require, import, fs, child_process, process or fetch. " +
  "Call the tools instead: const r = await tools.bash({ command: \"ls -la\" }) gives { output, exit_code, truncated }; " +
  "await tools.read({ path: \"file\", limit: 10 }) gives the text. Filter in the script and return only what you need. " +
  "Fix the script and run codemode again.]";

const IMAGE_MAGIC = [/^\x89PNG/, /^\xff\xd8\xff/, /^GIF8/, /^RIFF[\s\S]{4}WEBP/];

const KINDS: [RegExp, string, string][] = [
  [/^SQLite format 3\x00/, "an SQLite database", "sqlite3 -readonly FILE .tables (then .schema TABLE)"],
  [/^PK\x03\x04/, "a ZIP archive (or docx/xlsx/jar)", "unzip -l FILE"],
  [/^\x1f\x8b/, "a gzip file", "tar -tzf FILE | head, or zcat FILE | head"],
  [/^\x7fELF/, "a program (ELF)", "file FILE"],
  [/^%PDF/, "a PDF", "pdftotext FILE - | head -50"],
];

function kindOf(start: string): [string, string] {
  const k = KINDS.find(([re]) => re.test(start));
  return k ? [k[1], k[2]] : ["a binary file", "file FILE, or xxd FILE | head -20"];
}

// U+FFFD, what invalid UTF-8 bytes turn into.
const REPLACEMENT = String.fromCharCode(0xfffd);
const CONTROL_RE = new RegExp(`[\\x00-\\x08\\x0e-\\x1f${REPLACEMENT}]`, "g");
function isBinaryLine(line: string): boolean {
  if (line.includes("\x00")) return true;
  const bad = line.match(CONTROL_RE)?.length ?? 0;
  if (line.length < 8) return bad > 0;
  return bad / line.length > 0.1;
}

// bash output with each run of binary lines replaced by one line; undefined when there is none.
export function collapseBinary(text: string): string | undefined {
  if (!text.includes("\x00") && !text.includes(REPLACEMENT)) return undefined;
  const lines = text.split("\n");
  const out: string[] = [];
  let changed = false;
  for (let i = 0; i < lines.length; ) {
    if (!isBinaryLine(lines[i])) { out.push(lines[i++]); continue; }
    const start = i;
    while (i < lines.length && isBinaryLine(lines[i])) i++;
    const run = lines.slice(start, i).join("\n");
    const [kind, how] = kindOf(run);
    const n = i - start;
    out.push(`[binary data left out (${n.toLocaleString("en")} line${n === 1 ? "" : "s"}, ${run.length.toLocaleString("en")} characters): ` +
      `it looks like ${kind}. Not text, so head or cat only show bytes. Look inside with: ${how.replace(/FILE/g, "<file>")}]`);
    changed = true;
  }
  return changed ? out.join("\n") : undefined;
}

function expand(p: string, cwd: string): string {
  const e = p === "~" || p.startsWith("~/") ? path.join(os.homedir(), p.slice(1)) : p;
  return path.resolve(cwd || process.cwd(), e);
}

// Why read should not run on this file, or undefined for text, images and files that can't be opened (read then
// reports those itself).
export function binaryReason(shown: string, file: string): string | undefined {
  let head: Buffer, size: number;
  try {
    const fd = fs.openSync(file, "r");
    try {
      size = fs.fstatSync(fd).size;
      head = Buffer.alloc(Math.min(8192, size));
      fs.readSync(fd, head, 0, head.length, 0);
    } finally { fs.closeSync(fd); }
  } catch { return undefined; }
  const latin = head.toString("latin1");
  if (IMAGE_MAGIC.some((re) => re.test(latin))) return undefined;
  if (!head.includes(0)) return undefined;
  const [kind, how] = kindOf(latin);
  const mb = size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${size.toLocaleString("en")} bytes`;
  return `${shown} is ${kind} (${mb}), not text: read would only show its bytes. ` +
    `Look inside with bash instead: ${how.replace(/FILE/g, JSON.stringify(shown))}. Or describe it from its name, size and location.`;
}

export function capFor(contextWindow: unknown): number {
  const w = typeof contextWindow === "number" && contextWindow > 0 ? contextWindow : DEFAULT_WINDOW;
  return Math.max(MIN_CAP, Math.floor(w * SHARE_OF_WINDOW));
}

function saveFull(text: string, tool: string): string | undefined {
  try {
    const dir = path.join(os.tmpdir(), "pi-cap-output");
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${tool.replace(/[^\w-]/g, "_")}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.txt`);
    fs.writeFileSync(file, text);
    return file;
  } catch {
    return undefined;
  }
}

// Returns the text the model should see, or undefined when it fits.
export function capText(tool: string, input: any, text: string, cap: number, save = saveFull): string | undefined {
  if (text.length <= cap) return undefined;
  const file = save(text, tool);
  const where = file ? ` Full text: ${file}` : "";
  const cut = text.length;

  if (tool === "read") {
    let head = text.slice(0, cap);
    const nl = head.lastIndexOf("\n");
    if (nl > cap / 2) head = head.slice(0, nl);
    // Pi's read ends a partial result with "[... Use offset=N to continue.]"; drop that line from the kept start.
    head = head.replace(/\n*\[[^\]]*Use offset=\d+ to continue[^\]]*\]\s*$/, "");
    const lines = head.split("\n").length;
    const next = (Number(input?.offset) > 0 ? Number(input.offset) : 1) + lines;
    return `${head}\n\n[Cut to ${head.length} of ${cut} characters to keep the context small.${where} ` +
      `Read on with a smaller limit, or search the file with rg first. Use offset=${next} to continue.]`;
  }

  const headShare = tool === "bash" ? 0.25 : 0.75;
  const headLen = Math.floor(cap * headShare);
  const tailLen = cap - headLen;
  const head = text.slice(0, headLen);
  const tail = text.slice(cut - tailLen);
  const hint = tool === "bash"
    ? "Narrow the command instead (head, tail, grep, rg -l, wc -l, ! -path '*/.git/*'), or filter it in a codemode script."
    : "Ask for less, or filter the result in a codemode script.";
  return `${head}\n\n[... ${cut - headLen - tailLen} of ${cut} characters cut to keep the context small.${where} ${hint}]\n\n${tail}`;
}

export default function (pi: any) {
  pi.on("tool_call", (event: any, ctx: any) => {
    if (event?.toolName !== "read") return;
    const p = event.input?.path ?? event.input?.file_path;
    if (typeof p !== "string" || !p) return;
    const reason = binaryReason(p, expand(p, ctx?.cwd));
    if (reason) return { block: true, reason };
  });

  pi.on("tool_result", (event: any, ctx: any) => {
    const tool = String(event?.toolName ?? "");
    const parts: any[] = Array.isArray(event?.content) ? event.content : [];
    const texts = parts.filter((p) => p?.type === "text" && typeof p.text === "string");

    // bash, also inside codemode scripts (which get structuredContent.output, not the text).
    let structured = event?.structuredContent;
    let binaryOut: string | undefined;
    if (tool === "bash") {
      if (typeof structured?.output === "string") {
        const o = collapseBinary(structured.output);
        if (o !== undefined) structured = { ...structured, output: o };
      }
      if (texts.length) binaryOut = collapseBinary(texts.map((p) => p.text).join("\n"));
    }

    if (event?.parentToolCallId) {
      if (structured === event?.structuredContent && binaryOut === undefined) return;
      const others = parts.filter((p) => !texts.includes(p));
      return {
        content: binaryOut === undefined ? parts : [{ type: "text", text: binaryOut }, ...others],
        ...(structured !== undefined ? { structuredContent: structured } : {}),
      };
    }
    if (!texts.length) return;

    let text = binaryOut ?? texts.map((p) => p.text).join("\n");
    let changed = binaryOut !== undefined;

    if (tool === "codemode" && /Script failed/.test(text) && NODE_API_RE.test(text)) {
      text += CODEMODE_HINT;
      changed = true;
    }

    const capped = capText(tool, event.input, text, capFor(ctx?.model?.contextWindow));
    if (capped !== undefined) { text = capped; changed = true; }
    if (!changed) return;

    // Images (read of a picture, codemode's image()) stay; the text parts become one.
    const others = parts.filter((p) => !texts.includes(p));
    return { content: [{ type: "text", text }, ...others] };
  });
}
