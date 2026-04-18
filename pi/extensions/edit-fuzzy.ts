// edit-fuzzy.ts
// Location: ~/.pi/agent/extensions/edit-fuzzy.ts
//
// Pi's `edit` tool replaces text only if `oldText` matches the file exactly.
// The model often retypes the old text with small differences. This extension checks
// each edit before it runs:
//  - Formatting differences (spaces, table padding, trailing spaces, tabs,
//    quotes): if exactly ONE place in the file matches when those are ignored,
//    it swaps in the file's real text and the edit goes ahead.
//  - Real differences (a word the model remembered wrong) are never guessed. When
//    all edits in a call fail, the edit is blocked with the closest place in the
//    file, the lines that differ, and the file's exact text to copy. In a multi-edit
//    call where some edits match and others don't, the valid edits are allowed to
//    apply and the skipped ones are reported in the tool result so the model only
//    retries what failed.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const DEBUG = false;
// How much of the file's text the "closest match" message may show.
const MAX_SHOWN_LINES = 40;
const MAX_LINE_CHARS = 300;

// Compare lines ignoring indentation, trailing spaces, repeated spaces and quote styles.
function norm(line: string): string {
  return line
    .replace(/\r$/, "")
    .replace(/[ \t]/g, " ")
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/ {2,}/g, " ")
    .trim();
}

// The lines of `oldText`, without the blank first/last lines the model sometimes adds.
function wantedLines(oldText: string): string[] {
  const want = oldText.replace(/\r/g, "").split("\n");
  while (want.length > 1 && want[0].trim() === "") want.shift();
  while (want.length > 1 && want[want.length - 1].trim() === "") want.pop();
  return want;
}

// Start lines of every place in the file that matches `want` when formatting is ignored (stops at 2).
function formatMatches(fileLines: string[], want: string[]): number[] {
  const w = want.map(norm);
  const hits: number[] = [];
  for (let i = 0; i + w.length <= fileLines.length; i++) {
    let ok = true;
    for (let j = 0; j < w.length; j++) {
      if (norm(fileLines[i + j]) !== w[j]) { ok = false; break; }
    }
    if (ok) hits.push(i);
    if (hits.length > 1) break;
  }
  return hits;
}

// Find the file's real text for `oldText`, or undefined.
export function fixOldText(file: string, oldText: string): string | undefined {
  if (file.includes(oldText)) return undefined; // already exact
  const fileLines = file.split("\n");
  const want = wantedLines(oldText);
  if (want.every((l) => l.trim() === "")) return undefined;
  const hits = formatMatches(fileLines, want);
  if (hits.length !== 1) return undefined; // none, or ambiguous: don't guess
  return fileLines.slice(hits[0], hits[0] + want.length).join("\n");
}

// How alike two lines are, 0..1: 1 when equal ignoring formatting, else the share of shared words.
function lineSimilarity(a: string, b: string): number {
  const na = norm(a), nb = norm(b);
  if (na === nb) return 1;
  const wa = new Set(na.toLowerCase().split(/\W+/).filter(Boolean));
  const wb = new Set(nb.toLowerCase().split(/\W+/).filter(Boolean));
  if (wa.size === 0 || wb.size === 0) return 0;
  let shared = 0;
  for (const x of wa) if (wb.has(x)) shared++;
  return shared / Math.max(wa.size, wb.size);
}

function clip(line: string): string {
  return line.length > MAX_LINE_CHARS ? line.slice(0, MAX_LINE_CHARS) + "…" : line;
}

// Why `oldText` can't be applied, with what to do instead; undefined when Pi will find it (exact, or fixed above).
export function explainMiss(file: string, oldText: string, filePath: string, partial = false, inCodemode = false): string | undefined {
  if (file.includes(oldText)) return undefined;
  const fileLines = file.split("\n");
  const want = wantedLines(oldText);
  if (want.every((l) => l.trim() === "")) return undefined;

  const hits = formatMatches(fileLines, want);
  if (hits.length === 1) return undefined; // fixOldText handles it
  if (hits.length > 1) {
    return `The old text matches more than one place in ${filePath} (for example lines ${hits[0] + 1} and ${hits[1] + 1}). ` +
      "Add a nearby line that is different in each place, so it matches only one.";
  }

  // The closest block of the same number of lines.
  let best = -1, bestScore = 0, bestCount = 0;
  if (want.length <= fileLines.length) {
    for (let i = 0; i + want.length <= fileLines.length; i++) {
      let score = 0;
      for (let j = 0; j < want.length; j++) score += lineSimilarity(fileLines[i + j], want[j]);
      if (score > bestScore + 1e-9) { best = i; bestScore = score; bestCount = 1; }
      else if (Math.abs(score - bestScore) <= 1e-9) bestCount++;
    }
  }
  const readHow = inCodemode ? "tools.read" : "the read tool";
  const editHow = inCodemode ? "tools.edit" : "a new edit call";
  const reread = `Read the lines you want to change again with ${readHow} and copy the old text exactly from that output. ` +
    "Keep the old text short: only the lines that change, plus one unchanged line around them if needed.";
  if (best < 0 || bestCount > 1 || bestScore < want.length * 0.5) {
    return `The old text is not in ${filePath}, and no part of the file is close to it. ${reread}`;
  }

  const block = fileLines.slice(best, best + want.length);
  const diffs: string[] = [];
  for (let j = 0; j < want.length && diffs.length < 3; j++) {
    if (norm(block[j]) !== norm(want[j])) {
      diffs.push(`  line ${best + j + 1}\n    file has:  ${clip(block[j])}\n    you wrote: ${clip(want[j])}`);
    }
  }
  const shown = block.length <= MAX_SHOWN_LINES
    ? `The file's exact text there (lines ${best + 1}-${best + block.length}):\n${block.map(clip).join("\n")}`
    : `Read lines ${best + 1}-${best + block.length} again to copy them.`;
  const tail = partial
    ? `This edit was not applied, so fix its old text and send it in ${editHow}.`
    : "Nothing was changed, so fix the old text and try again.";
  return `The old text is not in ${filePath}. The closest place is lines ${best + 1}-${best + block.length}, ` +
    `where these lines differ:\n${diffs.join("\n")}\n` +
    "Your old text was retyped from memory. Copy it exactly from the file instead (keep only the lines that change). " +
    `${tail}\n${shown}`;
}

function resolvePath(p: string, cwd?: string): string {
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  return path.isAbsolute(p) ? p : path.join(cwd ?? process.cwd(), p);
}

export default function (pi: any) {
  const pendingById = new Map<string, string>();
  const pendingByInput = new WeakMap<object, string>();

  pi.on("session_start", () => { pendingById.clear(); });

  pi.on("tool_call", (event: any, ctx: any) => {
    if (event?.toolName !== "edit") return;
    const input = event.input;
    const p = input?.path ?? input?.file_path;
    if (typeof p !== "string") return;

    let file: string;
    try { file = fs.readFileSync(resolvePath(p, ctx?.cwd), "utf8"); } catch { return; }

    // Both shapes: { oldText, newText } and { edits: [{ oldText, newText }] }
    const many = Array.isArray(input.edits);
    const items: any[] = many ? input.edits : [input];
    const kept: any[] = [];
    const missed: { index: number; oldText: string }[] = [];

    items.forEach((e: any, i: number) => {
      let badOld: string | undefined;
      for (const key of ["oldText", "old_text", "old_string"]) {
        if (typeof e?.[key] !== "string") continue;
        const real = fixOldText(file, e[key]);
        if (real !== undefined) {
          if (DEBUG) console.error(`[edit-fuzzy] fixed ${key} in ${p}`);
          e[key] = real;
          continue;
        }
        if (explainMiss(file, e[key], p)) {
          badOld = e[key];
          break;
        }
      }
      if (badOld !== undefined) missed.push({ index: i, oldText: badOld });
      else kept.push(e);
    });

    if (missed.length === 0) return;

    const partial = many && kept.length > 0;
    const inCodemode = Boolean(event?.parentToolCallId);
    const problems = missed.map(({ index, oldText }) => {
      const why = explainMiss(file, oldText, p, partial, inCodemode) ?? "";
      return many ? `edits[${index}]: ${why}` : why;
    });

    if (partial) {
      input.edits = kept;
      const applied = kept.length;
      const total = items.length;
      const skipped = missed.length;
      const note =
        `Only ${applied} of ${total} edits were applied. ` +
        `${skipped} ${skipped === 1 ? "edit was" : "edits were"} NOT applied because the old text wasn't found. ` +
        `You must still apply ${skipped === 1 ? "this remaining edit" : "these remaining edits"} now ` +
        `(do not re-send the ${applied} already applied):\n\n` +
        problems.join("\n\n");
      if (typeof event.toolCallId === "string") pendingById.set(event.toolCallId, note);
      if (input && typeof input === "object") pendingByInput.set(input, note);
      if (DEBUG) console.error(`[edit-fuzzy] kept ${applied}/${total} edit(s) in ${p}, skipped ${skipped}`);
      return;
    }

    if (DEBUG) console.error(`[edit-fuzzy] blocked ${problems.length} edit(s) in ${p}`);
    return {
      block: true,
      reason: (many && items.length > 1
        ? `None of the ${items.length} edits were applied, because ${problems.length} of them can't be found. ` +
          "Fix those and send all the edits again.\n\n"
        : "") + problems.join("\n\n"),
    };
  });

  pi.on("tool_result", (event: any) => {
    if (event?.toolName !== "edit") return;
    let note: string | undefined;
    if (typeof event.toolCallId === "string") {
      note = pendingById.get(event.toolCallId);
      pendingById.delete(event.toolCallId);
    }
    if (event.input && typeof event.input === "object") {
      const byInput = pendingByInput.get(event.input);
      pendingByInput.delete(event.input);
      note ??= byInput;
    }
    if (!note || event.isError) return;
    const parts: any[] = Array.isArray(event.content) ? event.content : [];
    return { content: [...parts, { type: "text", text: `\n\n${note}` }] };
  });
}
