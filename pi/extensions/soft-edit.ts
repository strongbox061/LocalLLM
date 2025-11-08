// soft-edit.ts
// Location: ~/.pi/agent/extensions/soft-edit.ts
//
// Pi's `edit` tool replaces text only if `oldText` matches the file exactly.
// The model often retypes the old text with small differences. This extension checks
// each edit before it runs:
//  - Formatting differences (spaces, table padding, trailing spaces, tabs,
//    quotes): if exactly ONE place in the file matches when those are ignored,
//    it swaps in the file's real text and the edit goes ahead.
//  - Real differences (a word the model remembered wrong) are never guessed. When
//    all edits in a call fail, the edit is blocked with the closest place in
//    the file, the lines that differ, and the file's exact text to copy.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const DEBUG = false;
const MAX_SHOWN_LINES = 40;
const MAX_LINE_CHARS = 300;

// Compare lines ignoring indentation, trailing spaces, repeated spaces and quote styles.
// Compare lines ignoring indentation, trailing spaces, repeated spaces and quote styles.
function norm(line: string): string {
  return line
    .replace(/\r$/, "")
    .replace(/[ \t]/g, " ")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"')
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

export function fixOldText(file: string, oldText: string): string | undefined {
  const filePath = resolvePath(file);
  const content = fs.readFileSync(filePath, "utf-8");
  const fileLines = content.split("\n");
  const want = wantedLines(oldText);

  if (want.length === 0) return undefined;

  const matches = formatMatches(fileLines, want);
  if (matches.length === 1) {
    const start = matches[0];
    const end = start + want.length;
    return fileLines.slice(start, end).join("\n");
  }
  return undefined;
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
  if (line.length <= MAX_LINE_CHARS) return line;
  return line.slice(0, MAX_LINE_CHARS) + "...";
}

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

  if (want.length === 0) return undefined;

  // Find the best matching position
  let bestStart = 0;
  let bestScore = -1;

  for (let i = 0; i <= Math.min(fileLines.length, want.length) - 1; i++) {
    const limit = Math.min(want.length, fileLines.length - i);
    let score = 0;
    for (let j = 0; j < limit; j++) {
      score += lineSimilarity(fileLines[i + j], want[j]);
    }
    if (score > bestScore) {
      bestScore = score;
      bestStart = i;
    }
  }

  const startLine = Math.max(0, bestStart - 2);
  const endLine = Math.min(fileLines.length, bestStart + want.length + 2);
  const shownLines = fileLines.slice(startLine, endLine);

  let explanation = "";
  if (partial) {
    explanation += "Some edits succeeded but this one did not.\n\n";
  }

  explanation += `The \`oldText\` does not match any location in the file.\n\n`;
  explanation += `Closest match around line ${startLine + 1}:\n\n`;

  for (let i = 0; i < shownLines.length; i++) {
    const lineNum = startLine + 1 + i;
    const marker =
      i >= 2 && i < 2 + want.length ? ">>>" : "   ";
    explanation += `${marker} ${lineNum}: ${clip(shownLines[i])}\n`;
  }

  explanation += `\nCopy this exact text for \`oldText\`:\n\n\`\`\`\n`;
  const realText = fileLines.slice(bestStart, bestStart + want.length).join("\n");
  explanation += clip(realText);
  explanation += "\n```";

  return explanation;
}

function resolvePath(p: string, cwd?: string): string {
  if (path.isAbsolute(p)) return p;
  const base = cwd || process.cwd();
  return path.resolve(base, p);
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
