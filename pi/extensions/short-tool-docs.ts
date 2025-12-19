// short-tool-docs.ts
// Location: ~/.pi/agent/extensions/short-tool-docs.ts
//
// Tool descriptions are sent to the model with every request. The docs servers (Context7, GitMCP,
// through pi-mcp-adapter) bring long ones written for large general-purpose models: selection
// steps, response formats, examples in every parameter. This swaps them for short versions just
// before each request (about 1,700 tokens down to about 300). The rules they stated are kept where
// they already live in the system prompt.
//
// Our own todo tool (todo.ts, Pi's example kept unchanged) and subagent tool (subagent/) get their short texts here
// too. The subagent tool loses agentScope and confirmProjectAgents: only our own agents (~/.pi/agent/agents) run, never
// prompts from the project folder.

const DROPPED_TOOLS = new Set(["tool_search"]);

type Short = { description: string; params?: Record<string, string>; drop?: string[]; bare?: string[] };

const SHORT: Record<string, Short> = {
  todo: {
    description:
      "Your todo list for this task. Use it first for any task that changes more than one file or ends with running " +
      "tests: add each stage, toggle each right after its step (one at a time). Actions: add (text), toggle (id: done/not done), list, clear.",
    params: { text: "The step, for add.", id: "The item's number, for toggle." },
  },
  subagent: {
    description:
      "Run a task in a separate agent with a clean context; only its final answer comes back. Agents: code-finder (finds " +
      "and summarizes code), web-researcher (researches on the web, answers with links), planner (writes a plan), " +
      "reviewer (reviews changes), worker (makes changes). Give the task everything it needs: the agent hasn't seen " +
      "this conversation. One mode: agent+task, tasks (several; they take turns as the setup allows), or chain (in " +
      "order; {previous} in a task is the step before's answer).",
    params: {
      agent: "Agent name, for one task.",
      task: "The task, for one task.",
      tasks: "Several {agent, task}; as many run at once as the setup allows, the rest wait.",
      chain: "{agent, task} steps run in order.",
      cwd: "Folder to work in (default: the current one).",
    },
    drop: ["agentScope", "confirmProjectAgents"],
    bare: ["tasks", "chain"],
  },
  "context7_resolve-library-id": {
    description: "Resolve a library name to its Context7-compatible ID for documentation lookup.",
    params: {
      "libraryName": "Name of the library (e.g. 'react', 'express')"
    }
  },
  "context7_query-docs": {
    description: "Query documentation for a resolved library ID. Returns relevant excerpts from the library docs.",
    params: {
      "context7LibraryId": "The Context7-compatible library ID",
      "topic": "What to look up in the docs"
    }
  },
  "gitmcp": {
    description: "Query a GitHub repository's documentation, changelog, or source code via GitMCP.",
    params: {
      "repo": "Owner/repo (e.g. 'microsoft/vscode')",
      "query": "What to search for"
    }
  },
  "web_search": {
    description: "Search the web for information, documentation, or code.",
    params: {
      "query": "Search query"
    }
  },
  "read": {
    description: "Read the contents of a file. Supports text files and images.",
    params: {
      "path": "Path to the file to read",
      "offset": "Line number to start from (1-indexed)",
      "limit": "Maximum lines to read"
    }
  },
  "edit": {
    description: "Make precise text replacements in a file. Each edit matches exact text regions.",
    params: {
      "path": "Path to the file to edit",
      "edits": "Array of {oldText, newText} pairs"
    }
  },
  "write": {
    description: "Write content to a file. Creates the file if it doesn't exist.",
    params: {
      "path": "Path to the file to write",
      "content": "Content to write"
    }
  },
  "bash": {
    description: "Run a shell command and return its output.",
    params: {
      "command": "Shell command to execute"
    }
  }
};

function shorten(def: any): boolean {
  const short = SHORT[def.name];
  if (!short) return false;

  // Schema-based: update parameter descriptions directly on the JSON schema
  const schema = def.parameters ?? def.input_schema ?? def.inputSchema;
  if (schema && typeof schema === "object" && short.params) {
    delete schema.$schema;
    for (const [name, text] of Object.entries(short.params)) {
      if (schema.properties?.[name]) schema.properties[name].description = text;
    }
    for (const name of short.drop ?? []) {
      if (schema.properties) delete schema.properties[name];
      if (Array.isArray(schema.required)) schema.required = schema.required.filter((r: string) => r !== name);
    }
    for (const name of short.bare ?? []) {
      for (const item of Object.values<any>(schema.properties?.[name]?.items?.properties ?? {})) delete item.description;
    }
    return true;
  }

  // Text-based fallback for older tools that lack a structured schema
  def.description = short.description;
  if (short.drop) {
    for (const drop of short.drop) {
      def.description = def.description.split(drop).join("");
    }
  }
  return true;
}

export default function (pi: any) {
  pi.on("before-tool-calls", async (context: any) => {
    if (!context?.toolCalls) return;

    for (const call of context.toolCalls) {
      const toolName = call.tool?.name || call.tool_name;
      if (!toolName) continue;

      if (DROPPED_TOOLS.has(toolName)) {
        call.tool = null;
        continue;
      }

      if (call.tool?.description && SHORT[toolName]) {
        shorten(call.tool);
      }
    }
  });
}
