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
//  - a name from your messages or from a tool result in this session.

import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

const DEBUG = false;
const CHECKED_DOC_RE = /\.(md|markdown|txt|rst|adoc|org)$/i;
const EXT = "md|markdown|txt|rst|adoc|log|csv|ts|tsx|js|json|ya?ml|toml|py|sh|css|html";
const NAME_RE = new RegExp(
  `(?<![\\w./~<>$}*@-])@?([\\w./~<>-]*[\\w<>-]\\.(?:${EXT}))(?![\\w.-]*[\\w])`,
  "gi",
);

function textOf(content: any): string {
  if (typeof content === "string") return content;
  if (Buffer.isBuffer(content)) return content.toString("utf-8");
  if (content?.type === "text") return String(content.text ?? "");
  return "";
}

function resolvePath(p: string, base: string): string {
  if (p.startsWith("/")) return p;
  return path.resolve(base, p);
}

function projectNames(cwd: string): Set<string> {
  const names = new Set<string>();
  try {
    const out = execFileSync("git", ["ls-files", "-o", "--exclude-standard"], { cwd }).toString();
    for (const line of out.trim().split("\n")) {
      if (line) names.add(line.split("/").pop()!);
    }
    const tracked = execFileSync("git", ["ls-files"]).toString();
    for (const line of tracked.trim().split("\n")) {
      if (line) names.add(line.split("/").pop()!);
    }
  } catch {
    // not a git repo or git failed — fall back to scanning cwd
    const entries = fs.readdirSync(cwd, { recursive: true });
    for (const entry of entries) {
      if (typeof entry === "string") {
        const name = entry.split("/").pop()!;
        if (/\.\w+$/.test(name)) names.add(name);
      }
    }
  }
  return names;
}

export function fileNames(text: string): string[] {
  const matches = text.match(NAME_RE);
  return matches ? [...new Set(matches.map((m) => m.replace(/^@/, "")))] : [];
}

export function unknownNames(
  added: string[],
  known: Set<string>,
  opts: { cwd: string; target: string },
): string[] {
  const project = projectNames(opts.cwd);
  const targetBase = path.basename(opts.target);
  return added.filter((name) => {
    // the file being written is always ok
    if (name === targetBase) return false;
    if (known.has(name)) return false;
    if (project.has(name)) return false;
    // check sibling directory
    const siblingPath = path.join(path.dirname(opts.target), name);
    if (fs.existsSync(siblingPath)) return false;
    return true;
  });
}

export default function (pi: any) {
  pi.on("beforeEdit", (params: any) => {
    const text = textOf(params.content);
    if (!CHECKED_DOC_RE.test(params.file)) return;

    const cwd = params.cwd || process.cwd();
    const addedNames = fileNames(text);
    // known names from the existing file content
    const existingContent = textOf(params.existingContent ?? "");
    const existingNames = new Set(fileNames(existingContent));

    const unknowns = unknownNames(addedNames, existingNames, { cwd, target: params.file });
    if (unknowns.length > 0 && DEBUG) {
      pi.log.warn(`name-guard: unknown file names in ${params.file}: ${unknowns.join(", ")}`);
    }
  });

  pi.on("beforeWrite", (params: any) => {
    const text = textOf(params.content);
    if (!CHECKED_DOC_RE.test(params.file)) return;

    const cwd = params.cwd || process.cwd();
    const addedNames = fileNames(text);
    const known = projectNames(cwd);

    const unknowns = unknownNames(addedNames, known, { cwd, target: params.file });
    if (unknowns.length > 0 && DEBUG) {
      pi.log.warn(`name-guard: unknown file names in write ${params.file}: ${unknowns.join(", ")}`);
    }
  });
}
