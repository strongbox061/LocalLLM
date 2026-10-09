// truncate-output.ts
// Location: ~/.pi/agent/extensions/truncate-output.ts
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

export function binaryReason(shown: string, file: string): string | undefined {
  if (shown.includes("[binary") || shown.includes("[...")) {
    return `Full content saved to ${file}`;
  }
  return undefined;
}

export function capFor(contextWindow: unknown): number {
  const win = typeof contextWindow === "number" && contextWindow > 0
    ? contextWindow
    : DEFAULT_WINDOW;
  return Math.max(MIN_CAP, Math.floor(win * SHARE_OF_WINDOW));
}

function saveFull(text: string, tool: string): string | undefined {
  try {
    const dir = path.join(os.tmpdir(), "pi-cap-output");
    fs.mkdirSync(dir, { recursive: true });
    const safeTool = tool.replace(/[^a-zA-Z0-9_-]/g, "_");
    const filePath = path.join(dir, `${safeTool}-${Date.now()}.txt`);
    fs.writeFileSync(filePath, text);
    return filePath;
  } catch {
    return undefined;
  }
}

export function capText(
  tool: string,
  input: any,
  text: string,
  cap: number,
  save = saveFull,
): string | undefined {
  if (text.length <= cap) return undefined;

  const savedPath = save(text, tool);
  const hint = savedPath ? ` Full text saved to: ${savedPath}` : "";

  // bash: keep start and end
  if (tool === "bash") {
    const keepStart = Math.floor(cap * 0.25);
    const kept = text.slice(0, keepStart) + "\n\n[... truncated ...]\n\n" + text.slice(-Math.floor(cap * 0.75));
    return `${kept}\n\n${CODEMODE_HINT}${hint}`;
  }

  // read: keep start at line boundary, drop Pi's partial-result marker if present
  let head = text.slice(0, cap);
  const nl = head.lastIndexOf("\n");
  if (nl > cap / 2) head = head.slice(0, nl);
  head = head.replace(/\n*\[[^\]]*Use offset=\d+ to continue[^\]]*\]\s*$/, "");
  const lines = head.split("\n").length;
  const next = (Number(input?.offset) > 0 ? Number(input.offset) : 1) + lines;
  return `${head}\n\n[Cut to ${head.length} of ${text.length} characters to keep the context small.${hint} Read on with a smaller limit, or search the file with rg first. Use offset=${next} to continue.]`;
}

export default function (pi: any) {
  const windowTokenCount = pi?.config?.contextWindow || DEFAULT_WINDOW;
  const cap = capFor(windowTokenCount);

  // Wrap bash tool
  if (pi?.tools?.bash) {
    const originalBash = pi.tools.bash;
    pi.tools.bash = async function (args: any) {
      const result = await originalBash(args);
      if (result.output && typeof result.output === "string") {
        const capped = capText("bash", args, result.output, cap);
        if (capped) {
          result.output = capped;
        }
      }
      return result;
    };
  }

  // Wrap read tool
  if (pi?.tools?.read) {
    const originalRead = pi.tools.read;
    pi.tools.read = async function (args: any) {
      const result = await originalRead(args);
      if (result.content && typeof result.content === "string") {
        const capped = capText("read", args, result.content, cap);
        if (capped) {
          result.content = capped;
        }
      }
      return result;
    };
  }
}
