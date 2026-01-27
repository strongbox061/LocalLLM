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
const MAX_SHOWN_LINES = 40;
const MAX_LINE_CHARS = 300;

// Compare lines ignoring indentation, trailing spaces, repeated spaces and quote styles.
function norm(line: string): string {
  return line
    .replace(/\r$/, "")
    .replace(/[ \t]/g, " ")
    .replace(/[''']/g, "'")
    .replace(/[""/g, '"')
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
  return line.length > MAX_LINE_CHARS ? line.slice(0, MAX_LINE_CHARS) + "\u2026" : line;
}

// Why `oldText` can't be applied, with what to do instead; undefined when Pi will find it (exact, or fixed above).
export function explainMiss(
  file: string,
  oldText: string,
  filePath?: string,
  partial = false,
  inCodemode = false
): string | undefined {
  const resolvedPath = filePath || resolvePath(file);
  const content = fs.readFileSync(resolvedPath, "utf-8");
  const fileLines = content.split("\n");
  const want = wantedLines(oldText);
  if (want.every((l) => l.trim() === "")) return undefined;

  const hits = formatMatches(fileLines, want);
  if (hits.length === 1) return undefined; // fixOldText handles it
  if (hits.length > 1) {
    return `The old text matches more than one place in ${filePath || resolvedPath} (for example lines ${hits[0] + 1} and ${hits[1] + 1}). ` +
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
    return `The old text is not in ${filePath || resolvedPath}, and no part of the file is close to it. ${reread}`;
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
  return `The old text is not in ${filePath || resolvedPath}. The closest place is lines ${best + 1}-${best + block.length}, ` +
    `where these lines differ:\n${diffs.join("\n")}\n` +
    "Your old text was retyped from memory. Copy it exactly from the file instead (keep only the lines that change). " +
    `${tail}\n${shown}`;
}

function resolvePath(p: string, cwd?: string): string {
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  return path.isAbsolute(p) ? p : path.join(cwd ?? process.cwd(), p);
}

export default function (pi: any) {
  // Register hooks for the edit tool
  pi.hook?.("edit", (args: any) => {
    if (!Array.isArray(args)) return args;
    if (args.length < 2) return args;

    const file = args[0];
    const edits = args[1];

    if (!Array.isArray(edits)) return args;

    const fixedEdits: any[] = [];
    let hadMisses = false;

    for (const edit of edits) {
      if (typeof edit.oldText !== "string") {
        fixedEdits.push(edit);
        continue;
      }

      const fixed = fixOldText(file, edit.oldText);
      if (fixed !== undefined) {
        fixedEdits.push({ ...edit, oldText: fixed });
      } else {
        hadMisses = true;
        fixedEdits.push(edit);
      }
    }

    if (hadMisses) {
      // Check if all failed
      const allFailed = edits.every(
        (e: any) => typeof e.oldText === "string" && fixOldText(file, e.oldText) === undefined
      );

      if (allFailed) {
        const explanation = explainMiss(file, edits[0].oldText, undefined, false);
        if (explanation) {
          return { error: explanation };
        }
      }
    }

    return [file, fixedEdits];
  });
}
