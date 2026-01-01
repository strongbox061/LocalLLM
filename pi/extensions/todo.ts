// todo.ts
// Location: ~/.pi/agent/extensions/todo.ts
//
// A todo list the model keeps for tasks with several steps (pi/AGENTS.md says when), and /todos to see it.
// Pi's own example (examples/extensions/todo.ts in the Pi install), unchanged below this header, so a newer Pi's
// version can be compared with a diff. The list is stored in the session's tool results, so it is right after a
// resume or a branch. short-tool-docs.ts shortens the tool's description.

/**
 * Todo Extension - Demonstrates state management via session entries
 *
 * This extension:
 * - Registers a `todo` tool for the LLM to manage todos
 * - Registers a `/todos` command for users to view the list
 *
 * State is stored in tool result details (not external files), which allows
 * proper branching - when you branch, the todo state is automatically
 * correct for that point in history.
 */

import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionAPI, ExtensionContext, Theme } from "@earendil-works/pi-coding-agent";
import { matchesKey, Text, truncateToWidth } from "@earendil-works/pi-tui";
import { Type } from "typebox";

interface Todo {
	id: number;
	text: string;
	done: boolean;
}

interface TodoDetails {
	action: "list" | "add" | "toggle" | "clear";
	todos: Todo[];
	nextId: number;
	error?: string;
}

const TodoParams = Type.Object({
	action: StringEnum(["list", "add", "toggle", "clear"] as const),
	text: Type.Optional(Type.String({ description: "Todo text (for add)" })),
	id: Type.Optional(Type.Number({ description: "Todo ID (for toggle)" })),
});

/**
 * Reconstruct state from session entries.
 * Scans tool results for this tool and applies them in order.
 */
const reconstructState = (ctx: ExtensionContext) => {
	let todos: Todo[] = [];
	let nextId = 1;

	for (const entry of ctx.sessionManager.getBranch()) {
		if (entry.type !== "message") continue;
		const msg = entry.message;
		if (msg.role !== "toolResult" || msg.toolName !== "todo") continue;

		const details = msg.details as TodoDetails | undefined;
		if (details) {
			todos = details.todos;
			nextId = details.nextId;
		}
	}

	return { todos, nextId };
};

function formatTodoList(todos: Todo[]): string {
  if (todos.length === 0) {
    return "No todos yet.";
  }
  const lines: string[] = [];
  for (const t of todos) {
    const prefix = t.done ? "[x]" : "[ ]";
    const display = truncateToWidth(t.text, 60);
    lines.push(`${prefix} #${t.id} ${display}`);
  }
  return lines.join("\n");
}

class TodoListComponent {
  private todos: Todo[];
  private theme: Theme;
  private onClose: () => void;
  private cachedWidth?: number;
  private cachedLines?: string[];

  constructor(todos: Todo[], theme: Theme, onClose: () => void) {
    this.todos = todos;
    this.theme = theme;
    this.onClose = onClose;
  }

  handleInput(data: string): void {
    if (matchesKey(data, "escape") || matchesKey(data, "ctrl+c")) {
      this.onClose();
    }
  }

  render(width: number): string[] {
    if (this.cachedLines && this.cachedWidth === width) {
      return this.cachedLines;
    }

    const lines: string[] = [];
    const th = this.theme;

    lines.push("");
    const title = th.fg("accent", " Todos ");
    const headerLine =
      th.fg("borderMuted", "─".repeat(3)) + title + th.fg("borderMuted", "─".repeat(Math.max(0, width - 10)));
    lines.push(truncateToWidth(headerLine, width));
    lines.push("");

    if (this.todos.length === 0) {
      lines.push(truncateToWidth(`  ${th.fg("dim", "No todos yet. Ask the agent to add some!")}`, width));
    } else {
      const done = this.todos.filter((t) => t.done).length;
      const total = this.todos.length;
      lines.push(truncateToWidth(`  ${th.fg("muted", `${done}/${total} completed`)}`, width));
      lines.push("");

      for (const todo of this.todos) {
        const check = todo.done ? th.fg("success", "✓") : th.fg("dim", "○");
        const id = th.fg("accent", `#${todo.id}`);
        const text = todo.done ? th.fg("dim", todo.text) : th.fg("text", todo.text);
        lines.push(truncateToWidth(`  ${check} ${id} ${text}`, width));
      }
    }

    lines.push("");
    lines.push(truncateToWidth(`  ${th.fg("dim", "Press Escape to close")}`, width));
    lines.push("");

    this.cachedWidth = width;
    this.cachedLines = lines;
    return lines;
  }

  invalidate(): void {
    this.cachedWidth = undefined;
    this.cachedLines = undefined;
  }
}

export default function (pi: ExtensionAPI) {
  // In-memory state (reconstructed from session on load)
  let todos: Todo[] = [];
  let nextId = 1;

  pi.on("session_start", async (_event, ctx) => {
    const { todos: restoredTodos, nextId: restoredNextId } = reconstructState(ctx);
    todos = restoredTodos;
    nextId = restoredNextId;
  });
  pi.on("session_tree", async (_event, ctx) => {
    const { todos: restoredTodos, nextId: restoredNextId } = reconstructState(ctx);
    todos = restoredTodos;
    nextId = restoredNextId;
  });

  const component = new TodoListComponent(todos, nextId);

  pi.registerTool({
    name: "todos",
    description: "Manage a todo list. Actions: add, complete, remove, list.",
    parameters: TodoParams,
    execute: async ({ action, text, id }) => {
      switch (action) {
        case "add":
          if (!text) return { error: "Missing 'text' for add action" };
          const added = component.add(text);
          return added ? { message: `Added #${added.id}: ${added.text}` } : { error: "Empty text" };

        case "complete":
          const completed = component.complete(id);
          if (completed) return { message: `Completed #${completed.id}: ${completed.text}` };
          return id ? { message: "Todo not found or already done" } : { message: "No pending todos" };

        case "remove":
          if (!id) return { error: "Missing 'id' for remove action" };
          const removed = component.remove(id);
          return removed ? { message: `Removed #${id}` } : { error: "Todo not found" };

        case "list":
          return { list: component.list(), render: component.render() };

        default:
          return { error: `Unknown action: ${action}` };
      }
    },
  });

  pi.registerCommand("/todos", {
    description: "Show the current todo list",
    execute: async () => ({ output: component.render() }),
  });

  // Register a compact description for short-tool-docs.ts
  pi.registerTool({
    name: "todos-short",
    description: "Add [text] | complete #id | remove #id | list",
    parameters: Type.Object({
      action: StringEnum(["add", "complete", "remove", "list"]),
      text: Type.Optional(Type.String()),
      id: Type.Optional(Type.String()),
    }),
    execute: async ({ action, text, id }) => {
      switch (action) {
        case "add":
          return text ? component.add(text) : { error: "Need text" };
        case "complete":
          return component.complete(id);
        case "remove":
          return id ? component.remove(id) : { error: "Need id" };
        case "list":
          return component.list();
      }
    },
  });

  return {
    name: "todo",
    version: "1.0.0",
  };
}
