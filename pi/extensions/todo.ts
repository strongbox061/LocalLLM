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
 */
const reconstructState = (ctx: ExtensionContext): Todo[] => {
  let todos: Todo[] = [];
  for (const entry of ctx.sessionManager.getBranch()) {
    if (entry.type !== "message") continue;
    const msg = entry.message;
    if (msg.role !== "toolResult" || msg.toolName !== "todo") continue;
    const details = msg.details as TodoDetails | undefined;
    if (details) {
      todos = details.todos;
    }
  }
  return todos;
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

  constructor(initialTodos?: Todo[]) {
    this.todos = initialTodos || [];
  }

  get count(): number {
    return this.todos.length;
  }

  get doneCount(): number {
    return this.todos.filter((t) => t.done).length;
  }

  get pendingCount(): number {
    return this.todos.length - this.doneCount;
  }

  add(text: string): Todo | null {
    const trimmed = text.trim();
    if (!trimmed) return null;
    const max = this.todos.reduce((acc, t) => {
      const n = parseInt(t.id, 10);
      return isNaN(n) ? acc : Math.max(acc, n);
    }, 0);
    const todo: Todo = { id: max + 1, text: trimmed, done: false };
    this.todos.push(todo);
    return todo;
  }

  complete(id?: string): Todo | null {
    if (id) {
      const todo = this.todos.find((t) => t.id === id);
      if (todo && !todo.done) {
        todo.done = true;
        return todo;
      }
    } else {
      // Complete the first pending one
      const pending = this.todos.find((t) => !t.done);
      if (pending) {
        pending.done = true;
        return pending;
      }
    }
    return null;
  }

  remove(id?: string): boolean {
    if (!id) return false;
    const before = this.todos.length;
    this.todos = this.todos.filter((t) => t.id !== id);
    return this.todos.length < before;
  }

  list(): TodoDetails {
    return {
      todos: this.todos,
      total: this.todos.length,
      done: this.doneCount,
      pending: this.pendingCount,
    };
  }

  render(): string {
    const info = this.list();
    const header = `Todos (${info.done}/${info.total} done, ${info.pending} pending)`;
    const body = formatTodoList(info.todos);
    return `${header}\n${body}`;
  }
}

export default function (pi: ExtensionAPI) {
  let todos: Todo[] = [];

  pi.on("session_start", async (_event, ctx) => {
    todos = reconstructState(ctx);
  });
  pi.on("session_tree", async (_event, ctx) => {
    todos = reconstructState(ctx);
  });

  const component = new TodoListComponent(todos);

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
