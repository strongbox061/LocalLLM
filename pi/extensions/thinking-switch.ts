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
//        reasoning turns ON for every following step of that task.
//
// The switch happens at most once per task (off -> on), never back.
//
// The word rules (latest, error, action, "current directory") come in 35 languages from lang-words.ts.
//
// How reasoning is switched off comes from the model profile (models/<name>.env), as written by lib/model.sh to
// ~/.config/localllm/model.json: "qwen-template" (the chat template's enable_thinking=false), "reasoning-effort"
// (reasoning_effort set to the profile's lowest level) or "none" (the model doesn't reason: only the slot is pinned).
// Only requests for llama-server's alias ("local") are touched.

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { ACTION_START, ERROR, LATEST, LOCAL_CURRENT, has, norm } from "./lang-words.ts";

export type Reasoning = "qwen-template" | "reasoning-effort" | "none";
export interface ModelInfo { alias: string; reasoning: Reasoning; reasoningOffEffort: string }

// Without model.json (install/llama.sh writes it): only the slot is pinned, reasoning is left as Pi asked.
const FALLBACK: ModelInfo = { alias: "local", reasoning: "none", reasoningOffEffort: "" };

const MODEL_JSON = join(homedir(), ".config/localllm/model.json");
const REASONING_TYPES = ["qwen-template", "reasoning-effort", "none"];

export function modelInfo(file = process.env.LOCALLLM_MODEL_JSON || MODEL_JSON): ModelInfo {
  try {
    const j = JSON.parse(readFileSync(file, "utf8"));
    const reasoning: Reasoning = REASONING_TYPES.includes(j?.reasoning) ? j.reasoning : "none";
    const reasoningOffEffort = String(j?.reasoningOffEffort || "low");
    return { alias: String(j?.alias || "local"), reasoning, reasoningOffEffort };
  } catch {
    return FALLBACK;
  }
}

// Messages longer than this get reasoning from the start.
const MAX_QUICK_WORDS = 25;

// Without reasoning the model tends to answer these from memory instead of looking
// them up, or to print a tool call as plain text.
// "current directory" and similar (LOCAL_CURRENT) are about this machine, not the web.
const THINK_PREFIX_RE = /^\s*think\s*:/i;

// Set true to print each decision to the terminal while testing.
const DEBUG = false;

function textOf(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((p: any) => (typeof p === "string" ? p : p?.text ?? "")).join("\n");
  }
  return "";
}

// Hidden messages other extensions add (the Plannotator Inbox reminder, the end-of-answer checks) reach the model as
// user messages too. The person's own latest message comes from Pi's `input` event (see personText); its place in the
// request is the last user message that holds its text. Without it (a skill or template expanded the text), the last
// user message that isn't an end-of-answer check.
const FINISH_TAG = "[Automatic check, not from the user.";

export function personText(text: string): string {
  const t = String(text ?? "");
  return t.startsWith("Plannotator Inbox: ") ? t.slice(t.indexOf("\n") + 1 || t.length) : t;
}

function lastUserIndex(messages: any[], person = ""): number {
  const key = person.trim().slice(0, 200);
  let fallback = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.role !== "user") continue;
    const text = textOf(messages[i].content);
    if (key && text.includes(key)) return i;
    if (fallback < 0 && !text.includes(FINISH_TAG)) fallback = i;
  }
  return fallback;
}

// Rule 1: is this obviously a task, before the model has done anything?
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

// Rule 2: has the model used a tool since your last message?
export function usedTool(messages: any[], from: number): boolean {
  for (let i = from + 1; i < messages.length; i++) {
    const m = messages[i];
    if (m?.role === "tool" || m?.role === "toolResult") return true;
    if (m?.role === "assistant" && Array.isArray(m.tool_calls) && m.tool_calls.length) return true;
  }
  return false;
}

export function decide(messages: any[], person = ""): { think: boolean; why: string } {
  if (!Array.isArray(messages)) return { think: true, why: "no messages" };
  const u = lastUserIndex(messages, person);
  if (u < 0) return { think: true, why: "no user message" };

  const reason = clearTask((person.trim() || textOf(messages[u].content)).trim());
  if (reason) return { think: true, why: reason };
  if (usedTool(messages, u)) return { think: true, why: "the model used a tool, so it's a task" };
  return { think: false, why: "first answer, no tool yet" };
}

// llama-server slot reserved for Pi (Open WebUI uses slot 1). A subagent's Pi gets its slot from subagent/index.ts
// in LOCALLLM_SLOT, so it doesn't push this session out of slot 0.
export function piSlot(value = process.env.LOCALLLM_SLOT): number {
  const n = Number(value);
  return value !== undefined && value.trim() !== "" && Number.isInteger(n) && n >= 0 ? n : 0;
}

// The request to send: pinned to Pi's slot, and with reasoning off when `think` is false, the way the profile says.
export function shape(payload: any, think: boolean, model: ModelInfo): any {
  // Always pin Pi to its own slot so Open WebUI never overwrites its cache.
  const pinned = { ...payload, id_slot: payload.id_slot ?? piSlot(), cache_prompt: true };
  if (think || model.reasoning === "none") return pinned; // reasoning as Pi built it, just pinned

  const { reasoning, ...rest } = pinned;
  if (model.reasoning === "reasoning-effort") {
    return {
      ...rest,
      reasoning_effort: model.reasoningOffEffort,
      chat_template_kwargs: { ...(payload.chat_template_kwargs ?? {}), reasoning_effort: model.reasoningOffEffort },
    };
  }
  return {
    ...rest,
    reasoning_effort: "none",
    chat_template_kwargs: { ...(payload.chat_template_kwargs ?? {}), enable_thinking: false },
  };
}

export default function (pi: any) {
  let person = "";
  pi.on("input", (event: any) => { person = personText(event?.text); });

  pi.on("before_provider_request", (event: any) => {
    const payload = event?.payload;
    if (!payload || typeof payload !== "object") return;
    const model = modelInfo(); // read each time: a profile change needs no Pi restart
    if (String(payload.model ?? "") !== model.alias) return;

    const { think, why } = model.reasoning === "none" ? { think: true, why: "the model doesn't reason" }
      : decide(payload.messages, person);
    if (DEBUG) console.error(`[thinking-switch] reasoning ${think ? "ON" : "OFF"} (${why}), ${model.reasoning}`);
    return shape(payload, think, model);
  });
}
