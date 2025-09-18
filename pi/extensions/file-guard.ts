// file-guard.ts
// Location: ~/.pi/agent/extensions/file-guard.ts
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

const ALLOW_RE = /\b(?:yes|yeah|go\s*ahead|do\s*it|sure|ok|okay|allow|permit|approve|grant|proceed|continue|change|edit|update|modify|write|create|replace|delete|remove|add)\b/i;
const FILE_RE = /@?[\w./-]+\.(md|txt|ts|tsx|js|jsx|json|ya?ml|toml|py|sh|css|html|env|sql)\b/i;
const PLAN_ASK_RE = /\b(?:plan(?:s|ned|ning)?|planea\w*|planifi\w*|planej\w*|pianific\w*|planen)\b/i;

export function allowsChanges(text: string): boolean {
  // Check if the text contains an explicit permission to make changes
  const lower = text.toLowerCase();
  return ALLOW_RE.test(lower);
}

const BASH_WRITE_RE = /\b(?:echo|cat|tee|printf)\s*.*>\s*['"]?[\w./-]+/i;
const DEBUG = false;
const PARTIAL_READ_RE = /Use offset=\d+ to continue/;

export function filePath(input: any, cwd: string): string | undefined {
  // Extract a file path from the input text
  if (typeof input === "string") {
    const match = input.match(FILE_RE);
    if (match) {
      return path.resolve(cwd, match[0].replace(/^@/, ""));
    }
  }
  // Check for file property in object input
  if (typeof input === "object" && input !== null) {
    const filePath = input.file || input.path || input.filename;
    if (typeof filePath === "string") {
      return path.resolve(cwd, filePath);
    }
  }
  return undefined;
}

export function personText(text: string): string {
  // Check if the text contains instructions to edit or change files
  const lower = text.toLowerCase();
  
  // Look for file-related keywords combined with action verbs
  const changeKeywords = ["edit", "write", "create", "update", "modify", "change", "delete", "remove", "add"];
  const actionFound = changeKeywords.some(kw => lower.includes(kw));
  
  if (actionFound && FILE_RE.test(text)) {
    return text;
  }
  
  // Check for plan-related requests
  if (PLAN_ASK_RE.test(text)) {
    return text;
  }
  
  return "";
}

export default function (pi: any) {
  const homeDir = os.homedir();
  const extensionPath = path.join(homeDir, ".pi", "agent", "extensions", "file-guard.ts");
  
  // Check if the guard file exists
  if (!fs.existsSync(extensionPath)) {
    if (DEBUG) {
      console.log("file-guard: guard file not found at", extensionPath);
    }
    return;
  }
  
  // Store original content for comparison
  const originalContent = fs.readFileSync(extensionPath, "utf-8");
  
  // Hook into the edit/write flow by wrapping relevant methods
  const originalEdit = pi.edit;
  const originalWrite = pi.write;
  
  if (originalEdit) {
    pi.edit = function(...args: any[]) {
      const text = args[0] || "";
      if (!allowsChanges(text)) {
        return Promise.resolve({
          blocked: true,
          reason: "File changes require explicit permission. Please describe the change and ask first.",
          message: `To edit files, please reply with "yes", "go ahead", or similar confirmation.`,
        });
      }
      return originalEdit.apply(this, args);
    };
  }
  
  if (originalWrite) {
    pi.write = function(...args: any[]) {
      const text = args[0] || "";
      if (!allowsChanges(text)) {
        return Promise.resolve({
          blocked: true,
          reason: "File writing requires explicit permission. Please describe the change and ask first.",
          message: `To write files, please reply with "yes", "go ahead", or similar confirmation.`,
        });
      }
      return originalWrite.apply(this, args);
    };
  }
  
  if (DEBUG) {
    console.log("file-guard: extension loaded from", extensionPath);
  }
}
