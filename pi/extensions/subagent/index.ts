// subagent/index.ts
// Location: ~/.pi/agent/extensions/subagent/index.ts (with agents.ts, Pi's example unchanged)
//
// The `subagent` tool: runs a task in a separate `pi -p` process with its own clean context, so only the answer
// comes back into this conversation. Agents are pi/agents/*.md (installed to ~/.pi/agent/agents), workflows
// pi/prompts/*.md (/implement, /find-and-plan, /implement-and-review).

import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { AgentToolResult, ThinkingLevel } from "@earendil-works/pi-agent-core";
import type { Message } from "@earendil-works/pi-ai";
import { StringEnum } from "@earendil-works/pi-ai";
import { Container, Markdown, Spacer, Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";
import { type AgentConfig, type AgentScope, discoverAgents } from "./agents.ts";
import { personText } from "../edit-guard.ts";

/** Read the number of slots from a local.llm model.json file. */
export function modelSlots(
  file = process.env.LOCALLLM_MODEL_JSON || path.join(os.homedir(), ".config/localllm/model.json"),
): number {
  try {
    const raw = fs.readFileSync(file, "utf-8");
    const cfg = JSON.parse(raw) as Record<string, unknown>;
    const n = Number(cfg.gpu_layers ?? cfg.n_gpu_layers ?? cfg.layers ?? cfg.n_layers);
    if (Number.isFinite(n) && n > 0) return Math.min(n, 128);
  } catch { /* fall through */ }
  return 4; // conservative default
}

const SLOTS = modelSlots();

/** How many subagents may run in parallel — read from local.env or model.json. */
export function subagentsAtOnce(
  file = process.env.LOCALLLM_MODEL_JSON || path.join(os.homedir(), ".config/localllm/model.json"),
): number {
  const env = process.env.SUBAGENTS_AT_ONCE;
  if (env) {
    const n = Number(env);
    if (Number.isFinite(n) && n > 0) return Math.min(n, SLOTS);
  }
  try {
    const raw = fs.readFileSync(file, "utf-8");
    const cfg = JSON.parse(raw) as Record<string, unknown>;
    const n = Number((cfg as any).subagentsAtOnce ?? 1);
    if (Number.isFinite(n) && n > 0) return Math.min(n, SLOTS);
  } catch { /* fall through */ }
  return 1; // one after another by default
}

/** Queue that maps tasks to free GPU slots. */
export class SlotQueue {
  private readonly size: number;
  private readonly totalSlots: number;
  private busy = 0;
  private readonly waiting: Array<() => void> = [];

  constructor(size: number, totalSlots: number) {
    this.size = size;
    this.totalSlots = totalSlots;
  }

  /** Reserve a slot; returns a release callback. */
  acquire(): () => void {
    if (this.busy < this.size) {
      this.busy++;
      return () => { this.busy--; this._drain(); };
    }
    return new Promise<void>((resolve) => {
      this.waiting.push(resolve);
    }).then(() => {
      this.busy++;
      return () => { this.busy--; this._drain(); };
    });
  }

  private _drain(): void {
    while (this.busy < this.size && this.waiting.length > 0) {
      const fn = this.waiting.shift()!;
      this.busy++;
      fn();
    }
  }

  available(): number { return this.size - this.busy; }
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

const AT_ONCE = subagentsAtOnce();
const MAX_PER_MESSAGE = subagentsMax();

let startedForMessage = 0; // LocalLLM: subagents started since the person's latest message
const QUEUE = new SlotQueue(AT_ONCE, SLOTS);

/** Markdown annotation shown when a subagent is dispatched. */
export const SUBAGENT_NOTE = [
  "A subagent task has been dispatched.",
  "",
  "This runs in its own process with a clean context. The agent's full answer will be folded back here.",
];

let personMessage = process.env.LOCALLLM_PERSON_MESSAGE ?? "";
const MAX_PARALLEL_TASKS = 8;
const MAX_CONCURRENCY = AT_ONCE; // LocalLLM: was 4 (QUEUE holds them to AT_ONCE anyway)
const COLLAPSED_ITEM_COUNT = 10;
const PER_TASK_OUTPUT_CAP = 12 * 1024; // LocalLLM: was 50 KB
const MODEL_OUTPUT_LIMITS = { maxLines: 400, maxBytes: 12 * 1024 }; // LocalLLM: was Pi's 2,000 lines / 50 KB
const TRUNCATION_NOTICE_RESERVE = 256;

function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${(count / 1000).toFixed(1)}k`;
  return `${(count / 1_000_000).toFixed(2)}M`;
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
