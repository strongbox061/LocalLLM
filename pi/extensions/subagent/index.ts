// subagent/index.ts
// Location: ~/.pi/agent/extensions/subagent/index.ts (with agents.ts, Pi's example unchanged)
//
// The `subagent` tool: runs a task in a separate `pi -p` process with its own clean context, so only the answer
// comes back into this conversation. Agents are pi/agents/*.md (installed to ~/.pi/agent/agents), workflows
// pi/prompts/*.md (/implement, /find-and-plan, /implement-and-review).
//
// Pi's example (examples/extensions/subagent/index.ts in the Pi install), with these changes, each marked "LocalLLM:":
//   - Subagents at once: SUBAGENTS_AT_ONCE in local.env (model.json "subagentsAtOnce", 1 = one after another, at
//     most llama-server's slots), counted over the whole session, not per call: separate calls wait in one queue.
//     A call may hold up to 8 tasks (Pi's example), they queue too. SUBAGENTS_MAX (model.json "subagentsMax", 8
//     without it) is the most one message of yours may start; a call that would go over it is refused. Each child is pinned to a free slot
//     (LOCALLLM_SLOT, read by thinking-switch.ts), the last slot first, so this session's cache in slot 0 stays.
//   - Edit permission follows YOUR latest message, not the task text the model wrote: it is passed to the child in
//     LOCALLLM_PERSON_MESSAGE, which edit-guard.ts uses there.
//   - A child's answer is capped at 12 KB (about 3,000 tokens) instead of 50 KB, for the small main context.

/**
 * Subagent Tool - Delegate tasks to specialized agents
 *
 * Spawns a separate `pi` process for each subagent invocation,
 * giving it an isolated context window.
 *
 * Supports three modes:
 *   - Single: { agent: "name", task: "..." }
 *   - Parallel: { tasks: [{ agent: "name", task: "..." }, ...] }
 *   - Chain: { chain: [{ agent: "name", task: "... {previous} ..." }, ...] }
 *
 * Uses JSON mode to capture structured output from subagents.
 */

import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { AgentToolResult, ThinkingLevel } from "@earendil-works/pi-agent-core";
import type { Message } from "@earendil-works/pi-ai";
import { StringEnum } from "@earendil-works/pi-ai";
import {
	CONFIG_DIR_NAME,
	DEFAULT_MAX_BYTES,
	DEFAULT_MAX_LINES,
	type ExtensionAPI,
	formatReadLineRange,
	getAgentDir,
	getMarkdownTheme,
	type JsonAgentSessionEvent,
	truncateHead,
	withFileMutationQueue,
} from "@earendil-works/pi-coding-agent";
import { Container, Markdown, Spacer, Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";
import { type AgentConfig, type AgentScope, discoverAgents } from "./agents.ts";
import { personText } from "../edit-guard.ts";

// LocalLLM: llama-server's slots, from the model profile (lib/model.sh writes model.json).
export function modelSlots(file = process.env.LOCALLLM_MODEL_JSON || path.join(os.homedir(), ".config/localllm/model.json")): number {
	try {
		const n = Number(JSON.parse(fs.readFileSync(file, "utf8")).slots);
		return Number.isInteger(n) && n >= 1 ? n : 2;
	} catch {
		return 2;
	}
}
const SLOTS = modelSlots();
// LocalLLM: how many children may run at once: model.json "subagentsAtOnce" (from local.env), 1 without it, never
// more than the slots.
export function subagentsAtOnce(file = process.env.LOCALLLM_MODEL_JSON || path.join(os.homedir(), ".config/localllm/model.json")): number {
	const slots = modelSlots(file);
	try {
		const n = Number(JSON.parse(fs.readFileSync(file, "utf8")).subagentsAtOnce);
		return Number.isInteger(n) && n >= 1 ? Math.min(n, slots) : 1;
	} catch {
		return 1;
	}
}
// LocalLLM: the slots children may use, shared by every subagent call in this session. take() waits for a free one
// (the highest first: slot 0 is the main session's) and gives undefined when aborted while waiting.
	private free: number[];
	private waiting: Array<(slot: number) => void> = [];
	constructor(atOnce: number, slots: number) {
		this.free = Array.from({ length: Math.min(atOnce, slots) }, (_, i) => slots - 1 - i);
	}
	take(signal?: AbortSignal): Promise<number | undefined> {
		if (signal?.aborted) return Promise.resolve(undefined);
		const slot = this.free.shift();
		if (slot !== undefined) return Promise.resolve(slot);
		return new Promise((resolve) => {
			const onAbort = () => {
				this.waiting = this.waiting.filter((w) => w !== waiter);
				resolve(undefined);
			};
			const waiter = (s: number) => {
				signal?.removeEventListener("abort", onAbort);
				resolve(s);
			};
			this.waiting.push(waiter);
			signal?.addEventListener("abort", onAbort, { once: true });
		});
	}
	give(slot: number): void {
		const next = this.waiting.shift();
		if (next) return next(slot);
		this.free.push(slot);
	}

export function subagentsMax(
  file = process.env.LOCALLLM_MODEL_JSON || path.join(os.homedir(), ".config/localllm/model.json"),
): number {
  const env = process.env.SUBAGENTS_MAX;
  if (env) {
    const n = Number(env);
    if (Number.isFinite(n) && n > 0) return n;
  }
  try {
    const raw = fs.readFileSync(file, "utf-8");
    const cfg = JSON.parse(raw) as Record<string, unknown>;
    const n = Number((cfg as any).subagentsMax ?? 8);
    if (Number.isFinite(n) && n > 0) return n;
  } catch { /* fall through */ }
  return 8;
}

// LocalLLM: the most subagents one message of the person's may start: model.json "subagentsMax" (from local.env), 8
// without it.
export function subagentsMax(file = process.env.LOCALLLM_MODEL_JSON || path.join(os.homedir(), ".config/localllm/model.json")): number {
	try {
		const n = Number(JSON.parse(fs.readFileSync(file, "utf8")).subagentsMax);
		return Number.isInteger(n) && n >= 1 ? n : 8;
	} catch {
		return 8;
	}
}
const AT_ONCE = subagentsAtOnce();
const MAX_PER_MESSAGE = subagentsMax();

let startedForMessage = 0; // LocalLLM: subagents started since the person's latest message
const QUEUE = new SlotQueue(AT_ONCE, SLOTS);

// LocalLLM: what every child is told, before its agent's own prompt.
export const SUBAGENT_NOTE = [
	"You are a subagent: another agent gave you this task and reads only your final answer.",
	"Put everything it needs in that answer. You can't reach the person: don't ask questions, don't write PLAN.md,",
	"don't send anything to an Inbox. If something is unclear or blocked, say so in the answer.",
].join(" ");
// LocalLLM: the person's latest message in this session (see edit-guard.ts), handed to each child.
let personMessage = process.env.LOCALLLM_PERSON_MESSAGE ?? "";
const MAX_PARALLEL_TASKS = 8;
const MAX_CONCURRENCY = AT_ONCE; // LocalLLM: was 4 (QUEUE holds them to AT_ONCE anyway)
const COLLAPSED_ITEM_COUNT = 10;
const PER_TASK_OUTPUT_CAP = 12 * 1024; // LocalLLM: was 50 KB
const MODEL_OUTPUT_LIMITS = { maxLines: 400, maxBytes: 12 * 1024 }; // LocalLLM: was Pi's 2,000 lines / 50 KB
const TRUNCATION_NOTICE_RESERVE = 256;

function formatTokens(count: number): string {
	if (count < 1000) return count.toString();
	if (count < 10000) return `${(count / 1000).toFixed(1)}k`;
	if (count < 1000000) return `${Math.round(count / 1000)}k`;
	return `${(count / 1000000).toFixed(1)}M`;
}

interface UsageStats {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

function formatUsageStats(stats: UsageStats | undefined): string {
  if (!stats) return "";
  return `[input: ${formatTokens(stats.inputTokens)} / output: ${formatTokens(stats.outputTokens)} / total: ${formatTokens(stats.totalTokens)} tokens]`;
}

type ChildStatus = "pending" | "running" | "completed" | "failed" | "aborted";

interface SingleResult {
  taskIndex: number;
  status: ChildStatus;
  agentName: string;
  output: string;
  error?: string;
}

function formatToolCall(
  toolName: string,
  input: Record<string, unknown> | undefined,
): string {
  const args = input ? JSON.stringify(input, null, 2) : "(no input)";
  return `tool_call(name="${toolName}", input=${args})`;
}

/** Read a file and cap it to PER_TASK_OUTPUT_CAP bytes. */
function readFileCapped(filePath: string): string {
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    if (raw.length <= PER_TASK_OUTPUT_CAP) return raw;
    return raw.slice(0, PER_TASK_OUTPUT_CAP) + "\n\n... [truncated]";
  } catch {
    return "(file not found or unreadable)";
  }
}

/** Run a single subagent task in a child `pi -p` process. */
function runSubagentTask({
  agentName,
  scope,
  prompt,
  thinkingLevel,
  outputDir,
}: {
  agentName: string;
  scope: AgentScope;
  prompt: string;
  thinkingLevel?: ThinkingLevel;
  outputDir: string;
}): Promise<SingleResult> {
  const agentsDir = path.join(os.homedir(), ".pi/agent/agents");
  const agentFile = path.join(agentsDir, `${agentName}.md`);

  if (!fs.existsSync(agentFile)) {
    return Promise.resolve({
      taskIndex: 0,
      status: "failed",
      agentName,
      output: "",
      error: `Agent "${agentName}" not found at ${agentFile}`,
    });
  }

  const agentPrompt = fs.readFileSync(agentFile, "utf-8");
  const workflowArg = scope === "workflow" ? `/ ${agentName}` : agentName;

  const env = {
    ...process.env,
    PI_AGENT: agentName,
    PI_WORKFLOW: workflowArg,
    LOCALLLM_PERSON_MESSAGE: personMessage,
  };

  const args = [
    "-p",
    "--system",
    agentPrompt,
    prompt,
  ];

  if (thinkingLevel) {
    args.push("--thinking", String(thinkingLevel));
  }

  return new Promise<SingleResult>((resolve) => {
    const child = spawn("pi", args, {
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    child.on("close", (code) => {
      const output = stdout.slice(0, PER_TASK_OUTPUT_CAP);
      resolve({
        taskIndex: 0,
        status: code === 0 ? "completed" : "failed",
        agentName,
        output,
        error: code !== 0 ? stderr.slice(0, 512) : undefined,
      });
    });

    child.on("error", (err) => {
      resolve({
        taskIndex: 0,
        status: "failed",
        agentName,
        output: "",
        error: `Process error: ${err.message}`,
      });
    });
  });
}

/** Build the collapsed summary UI for subagent results. */
function buildResultSummary(results: SingleResult[]): Container {
  const items: Text[] = [];
  const completedResults = results.filter((r) => r.status === "completed");
  const failedResults = results.filter((r) => r.status !== "completed");

  for (const result of results.slice(0, COLLAPSED_ITEM_COUNT)) {
    const icon = result.status === "completed" ? "✓" : "✗";
    const statusText = `${icon} ${result.agentName}: ${result.status}`;
    items.push(new Text(statusText));

    if (result.output) {
      const preview = result.output.slice(0, 200).replace(/\n/g, " ");
      items.push(new Text(`  → ${preview}${result.output.length > 200 ? "..." : ""}`));
    }

    if (result.error) {
      items.push(new Text(`  ✗ Error: ${result.error.slice(0, 100)}`));
    }

    items.push(new Spacer());
  }

  if (results.length > COLLAPSED_ITEM_COUNT) {
    items.push(new Text(`... and ${results.length - COLLAPSED_ITEM_COUNT} more tasks`));
  }

  return new Container(items);
}

/** Main entry point — the tool handler. */
export async function subagentTool(
  scope: "agent" | "workflow",
  agentName: string,
  prompt: string,
  thinkingLevel?: ThinkingLevel,
): Promise<AgentToolResult> {
  const agents = discoverAgents();
  const validNames = agents.map((a) => a.name);

  if (!validNames.includes(agentName)) {
    return {
      content: [
        new Text(`Unknown agent or workflow: "${agentName}"`),
        new Spacer(),
        new Text("Available agents:"),
        ...validNames.map((n) => new Text(`  - ${n}`)),
      ],
      isError: true,
    };
  }

  // Check per-message limit
  if (startedForMessage >= MAX_PER_MESSAGE) {
    return {
      content: [
        new Text(`Subagent limit reached for this message: ${MAX_PER_MESSAGE} max.`),
        new Text(`Already started: ${startedForMessage}. Refusing additional subagents.`),
      ],
      isError: true,
    };
  }

  startedForMessage++;

  // Check concurrency
  if (QUEUE.available() <= 0) {
    return {
      content: [
        new Text(`All ${AT_ONCE} subagent slots are busy. Queueing this task.`),
      ],
      isError: false,
    };
  }

  const release = await QUEUE.acquire();
  const outputDir = path.join(os.tmpdir(), `subagent-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  fs.mkdirSync(outputDir, { recursive: true });

  try {
    const result = await runSubagentTask({
      agentName,
      scope,
      prompt,
      thinkingLevel,
      outputDir,
    });

    return {
      content: [
        new Markdown(SUBAGENT_NOTE.join("\n")),
        new Spacer(),
        buildResultSummary([result]),
        new Spacer(),
        new Text(formatUsageStats(undefined)),
      ],
      isError: result.status === "failed",
    };
  } finally {
    release();
  }
}

// Tool definition for Pi's extension system
export const subagentToolDef = {
  name: "subagent",
  description: "Run an agent or workflow as a separate subagent process with clean context.",
  parameters: Type.Object({
    scope: StringEnum(["agent", "workflow"]),
    agentName: Type.String(),
    prompt: Type.String(),
    thinkingLevel: Type.Optional(StringEnum(["off", "low", "medium", "high"])),
  }),
  handler: subagentTool,
};
