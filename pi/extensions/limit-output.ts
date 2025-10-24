// limit-output.ts
// Location: ~/.pi/agent/extensions/limit-output.ts
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

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const SHARE_OF_WINDOW = 0.2; // characters kept, as a share of the context window in tokens
const DEFAULT_WINDOW = 57344;
const MIN_CAP = 4000;

const NODE_API_RE =
  /(?:node|npm|npx|yarn|pnpm|git|docker|curl|wget|ssh|rsync|make|gcc|python|pip)\b/i;

export const CODEMODE_HINT =
  "The full text has been saved to a file. Read it with `read` if you need more.";

const IMAGE_MAGIC = [/^\x89PNG/, /^\xff\xd8\xff/, /^GIF8/, /^RIFF[\s\S]{4}WEBP/];

function kindOf(start: string): [string, string] {
  for (const re of IMAGE_MAGIC) {
    if (re.test(start)) return ["image", "image data"];
  }
  return ["text", "text"];
}

const REPLACEMENT = String.fromCharCode(0xfffd);
const CONTROL_RE = new RegExp(`[\\x00-\\x08\\x0e-\\x1f${REPLACEMENT}]`, "g");

function isBinaryLine(line: string): boolean {
  const clean = line.replace(CONTROL_RE, "");
  if (clean.length === 0) return false;
  let controlChars = 0;
  for (const ch of line) {
    const code = ch.charCodeAt(0);
    if ((code >= 0x00 && code <= 0x08) || (code >= 0x0e && code <= 0x1f)) {
      controlChars++;
    }
  }
  return controlChars / clean.length > 0.3;
}

export function collapseBinary(text: string): string | undefined {
  const lines = text.split("\n");
  let firstBinary = -1;
  for (let i = 0; i < lines.length; i++) {
    if (isBinaryLine(lines[i])) {
      firstBinary = i;
      break;
    }
  }
  if (firstBinary === -1) return undefined;
  const before = lines.slice(0, firstBinary).join("\n");
  const remaining = lines.length - firstBinary;
  return `${before}\n\n[... ${remaining} binary lines omitted ...]`;
}

function expand(p: string, cwd: string): string {
  if (p.startsWith("~")) {
    return path.join(os.homedir(), p.slice(1));
  }
  return path.isAbsolute(p) ? p : path.resolve(cwd, p);
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
  const dir = path.join(os.tmpdir(), "pi-extensions");
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  const safeTool = tool.replace(/[^a-zA-Z0-9_-]/g, "_");
  const filePath = path.join(dir, `full-${safeTool}-${Date.now()}.txt`);
  try {
    fs.writeFileSync(filePath, text, "utf-8");
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

  // read: keep start at line boundary
  const endMarker = "\n\n---\nUse offset=N to continue reading.";
  let cutAt = cap - endMarker.length;
  if (cutAt > 0 && cutAt < text.length) {
    const lastNewline = text.lastIndexOf("\n", cutAt);
    cutAt = lastNewline > 0 ? lastNewline : cutAt;
  }
  return `${text.slice(0, cutAt)}${endMarker}${hint}`;
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
