// thinking-switch.ts
// Location: ~/.pi/agent/extensions/thinking-switch.ts
//
// Lets the model's own behaviour decide whether a message is a quick question
// or a task:
//
//   1. Clear tasks get reasoning from the start: a URL, code, a file name, a
//      long message, "think:", a request that needs current information or is
//      about an error, or a message starting with an action like "search".
//   2. Everything else starts WITHOUT reasoning.
//      - If the model answers directly, it was a quick question: fast answer.
//      - If the model reaches for a tool (search, browser, bash...), it's a task:

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { ACTION_START, ERROR, LATEST, LOCAL_CURRENT, has, norm } from "./lang-words.ts";

export type Reasoning = "qwen-template" | "reasoning-effort" | "none";

export interface ModelInfo {
  alias: string;
  reasoning: Reasoning;
  reasoningOffEffort: string;
}

const MODEL_JSON = join(homedir(), ".config/localllm/model.json");
const REASONING_TYPES = ["qwen-template", "reasoning-effort", "none"];
const MAX_QUICK_WORDS = 25;
const THINK_PREFIX_RE = /^\s*think\s*:/i;
const DEBUG = false;

function log(...args: any[]) {
  if (DEBUG) console.log("[thinking-switch]", ...args);
}

export function modelInfo(file = process.env.LOCALLLM_MODEL_JSON || MODEL_JSON): ModelInfo {
  const raw = readFileSync(file, "utf-8");
  const data = JSON.parse(raw);
  const entry = data.models?.[0] || data;
  const alias = entry.alias || entry.name || "qwen";

  let reasoning: Reasoning = "none";
  const r = (entry.reasoning as string)?.toLowerCase() || "";
  if (r.includes("template")) reasoning = "qwen-template";
  else if (r.includes("effort") || r.includes("deep")) reasoning = "reasoning-effort";

  const effort = entry.reasoningOffEffort || (reasoning === "reasoning-effort" ? "minimal" : "");

  return { alias, reasoning, reasoningOffEffort: effort };
}

function textOf(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((c: any) => {
        if (c.type === "text") return c.text || "";
        if (c.type === "image_url" && c.image_url?.url) return "[image]";
        return "";
      })
      .join("\n");
  }
  return String(content ?? "");
}

const FINISH_TAG = "[Automatic check, not from the user.";

export function personText(text: string): string {
  if (text.includes(FINISH_TAG)) return text.split(FINISH_TAG)[0].trim();
  return text.trim();
}

function lastUserIndex(messages: any[], person = ""): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    const role = messages[i]?.role || "";
    if (role === "user") {
      if (!person) return i;
      const content = textOf(messages[i].content);
      const sender = messages[i]?.sender || messages[i]?.from || "";
      if (norm(sender).includes(norm(person)) || norm(content).includes(norm(person))) return i;
    }
  }
  return -1;
}

export function clearTask(text: string): string | undefined {
  if (THINK_PREFIX_RE.test(text)) return "asked to think";
  if (/https?:\/\/|www\./i.test(text)) return "has a URL";
  if (LATEST.test(norm(text).replace(LOCAL_CURRENT, ""))) return "needs current information";
  if (has(ERROR, text)) return "about an error";
  if (has(ACTION_START, text)) return "starts with an action";
  if (/```|`[^`]+`/.test(text)) return "has code";
  if (/[\w-]+\.(py|ts|js|json|md|ya?ml|toml|sh|rs|go|c|cpp|h|txt|csv)\b/i.test(text)) return "mentions a file";
  const words = text.split(/\s+/).filter(Boolean).length;
  if (words > MAX_QUICK_WORDS) return `${words} words`;
  return undefined;
}

export function usedTool(messages: any[], from: number): boolean {
  for (let i = from + 1; i < messages.length; i++) {
    const m = messages[i];
    if (m?.role === "tool" || m?.role === "toolResult") return true;
    if (m?.role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) return true;
  }
  return false;
}

export function decide(messages: any[], person = ""): { think: boolean; why: string } {
  const idx = lastUserIndex(messages, person);
  if (idx < 0) return { think: false, why: "no user message" };

  const msg = messages[idx];
  const text = personText(textOf(msg.content));
  log("last user:", text.slice(0, 120));

  const taskReason = clearTask(text);
  if (taskReason) {
    log("clear task:", taskReason);
    return { think: true, why: taskReason };
  }

  // Check if model already used a tool — escalate to reasoning
  if (usedTool(messages, idx)) {
    log("tool used after quick start");
    return { think: true, why: "tool escalation" };
  }

  return { think: false, why: "quick question" };
}

export function piSlot(value = process.env.LOCALLLM_SLOT): number {
  if (value == null) return -1;
  const n = parseInt(String(value), 10);
  return isNaN(n) ? -1 : n;
}

export function shape(payload: any, think: boolean, model: ModelInfo): any {
  const cloned = JSON.parse(JSON.stringify(payload));

  if (think && model.reasoning === "qwen-template") {
    // Use the Qwen thinking template
    if (!cloned.prompt) cloned.prompt = "";
    cloned.prompt = "<think>\nLet me work through this step by step.\n</think>\n" + cloned.prompt;
  } else if (think && model.reasoning === "reasoning-effort") {
    // Use reasoning effort parameter
    cloned.reasoning_effort = "enabled";
  } else if (!think && model.reasoning === "reasoning-effort") {
    // Explicitly disable reasoning when think is false
    cloned.reasoning_effort = "none";
  }

  return cloned;
}

export default function (pi: any) {
  pi.hook("beforePayload", (payload: any, context: any) => {
    const model = modelInfo();
    const messages = payload.messages || [];
    const decision = decide(messages, context?.person || "");

    log("decide:", decision.why, "think:", decision.think);

    if (decision.think || model.reasoning !== "none") {
      return shape(payload, decision.think, model);
    }
    return payload;
  });
}
