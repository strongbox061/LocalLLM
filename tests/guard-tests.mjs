// guard-tests.mjs: checks the rules in Pi's extensions without the model (seconds, no search credits).
//
//   node --experimental-strip-types tests/guard-tests.mjs
//
// Each extension is loaded with a fake `pi` that records its handlers; the checks then send it the tool calls, results
// and requests the model would. The Context7 ID cache goes to a temporary file (PI_CONTEXT7_ID_CACHE), and web_search's
// HTTP calls go to a fake fetch, so nothing real is touched.

import { mkdtempSync, mkdirSync, rmSync, symlinkSync, copyFileSync, cpSync, existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const EXT = join(REPO, "pi", "extensions");
const TMP = mkdtempSync(join(tmpdir(), "pi-guard-"));
process.env.PI_CONTEXT7_ID_CACHE = join(TMP, "context7-ids.json");

let passed = 0, failed = 0;
function check(name, ok, detail = "") {
  if (ok) { passed++; console.log(`  ok    ${name}`); }
  else { failed++; console.log(`  FAIL  ${name}${detail ? `\n        ${detail}` : ""}`); }
}

// A fake Pi: records handlers and tools; say() is a message from the person (Pi's `input` event, then a request with
// it); inject() is a hidden message another extension adds (the Plannotator Inbox reminder, an end-of-answer check):
// a user message in the request, no `input` event.
function fakePi() {
  const handlers = {}, tools = {}, messages = [];
  const pi = { on: (name, fn) => (handlers[name] ??= []).push(fn), registerTool: (t) => (tools[t.name] = t),
               registerCommand() {}, getActiveTools: () => [], setActiveTools() {} };
  const send = async () => { for (const fn of handlers.before_provider_request ?? []) await fn({ payload: { messages: [...messages] } }); };
  return {
    pi, tools, messages, handlers,
    async say(text) {
      for (const fn of handlers.input ?? []) await fn({ text, source: "interactive" });
      messages.push({ role: "user", content: text });
      await send();
    },
    async inject(text) {
      messages.push({ role: "user", content: text });
      await send();
    },
    async call(toolName, input, cwd = TMP) {
      for (const fn of handlers.tool_call ?? []) {
        const r = await fn({ toolName, input }, { cwd });
        if (r?.block) return { blocked: true, reason: r.reason, input };
      }
      return { blocked: false, input };
    },
    async result(toolName, input, text, isError = false, cwd = TMP) {
      let last;
      for (const fn of handlers.tool_result ?? []) last = (await fn({ toolName, input, content: [{ type: "text", text }], isError }, { cwd })) ?? last;
      return last;
    },
    async settle(answerText, ctx) {
      for (const fn of handlers.agent_end ?? []) await fn({ messages: [{ role: "assistant", content: [{ type: "text", text: answerText }] }] });
      let out;
      for (const fn of handlers.agent_before_settle ?? []) out = (await fn({ entries: [] }, ctx)) ?? out;
      return (out?.entries ?? []).map((e) => e.content).join("\n");
    },
    async event(name, event, ctx) {
      for (const fn of handlers[name] ?? []) await fn(event, ctx);
    },
    async request(payload) {
      for (const fn of handlers.before_provider_request ?? []) payload = (await fn({ payload })) ?? payload;
      return payload;
    },
  };
}

const load = async (file) => (await import(pathToFileURL(file).href));

// ---------------------------------------------------------------- agent-browser-fix.ts
console.log("agent-browser-fix.ts: web_search queries");
{
  const p = fakePi();
  (await load(join(EXT, "agent-browser-fix.ts"))).default(p.pi);
  await p.say("Find the pi-agent-browser-native repository");
  let r = await p.call("web_search", { query: "!gh pi-agent-browser-native", backend: "duckduckgo", compact: true });
  check("bang !gh becomes site:github.com", r.input.query === "pi-agent-browser-native site:github.com", r.input.query);
  check("backend and compact are dropped", !("backend" in r.input) && !("compact" in r.input), JSON.stringify(r.input));
  r = await p.call("web_search", { query: "!ddg kokoro tts" });
  check("an engine bang (!ddg) is removed", r.input.query === "kokoro tts", r.input.query);
  r = await p.call("web_search", { query: "what is !important in css" });
  check("a ! in the middle of the query is left alone", r.input.query === "what is !important in css", r.input.query);
  // dropUnaskedYears: strip unasked years on current/latest queries, keep them on historical/date-sensitive ones
  await p.say("What is the latest version of Vite?");
  r = await p.call("web_search", { query: "vite latest release 2024 2025" });
  check("unasked years dropped on latest/current queries", r.input.query === "vite latest release", r.input.query);
  r = await p.call("web_search", { query: "open-webui first commit created date 2022 2023" });
  check("years kept when query asks for historical/creation dates", r.input.query === "open-webui first commit created date 2022 2023", r.input.query);
  await p.say("When were Open WebUI and Pi released?");
  r = await p.call("web_search", { query: "aider coding agent 2023 2024" });
  check("years kept when user's question is historical (released)", r.input.query === "aider coding agent 2023 2024", r.input.query);
}

const PKG = join(TMP, "pkg");

const log = (o) => appendFileSync(join(TMP, "log.jsonl"), JSON.stringify({ t: Date.now(), ...o }) + "\n");

setTimeout(() => {
  rmSync(TMP, { recursive: true, force: true });
}, 60000);

// --- Extension test helpers ---

function loadExtension(name) {
  const extPath = join(EXT, name);
  if (!existsSync(extPath)) {
    throw new Error(`Extension not found: ${name} at ${extPath}`);
  }
  return load(extPath);
}

// --- Test runners ---

async function runTests() {
  console.log("Running guard tests...\n");

  // Test Context7 extension
  try {
    const ctx7Ext = await loadExtension("context7");
    const pi = fakePi();
    const handlers = ctx7Ext.install(pi, { repo: REPO, tmp: TMP });

    check("context7: install registers handlers", !!handlers);
    check("context7: has resolve-id handler", typeof handlers["context7/resolve-id"] === "function");
  } catch (e) {
    check("context7: loads without error", false, e.message);
  }

  // Test todo extension
  try {
    const todoExt = await loadExtension("todo");
    const pi = fakePi();
    const handlers = todoExt.install(pi, { repo: REPO, tmp: TMP });

    check("todo: install registers handlers", !!handlers);
    check("todo: has update-todo handler", typeof handlers["todo/update-todo"] === "function");
  } catch (e) {
    check("todo: loads without error", false, e.message);
  }

  // Test mcp-adapter extension
  try {
    const mcpExt = await loadExtension("mcp-adapter");
    const pi = fakePi();
    const handlers = mcpExt.install(pi, { repo: REPO, tmp: TMP });

    check("mcp-adapter: install registers handlers", !!handlers);
    check("mcp-adapter: has list-tools handler", typeof handlers["mcp/list-tools"] === "function");
  } catch (e) {
    check("mcp-adapter: loads without error", false, e.message);
  }

  // Test soft-edit extension
  try {
    const editExt = await loadExtension("soft-edit");
    const pi = fakePi();
    const handlers = editExt.install(pi, { repo: REPO, tmp: TMP });

    check("soft-edit: install registers handlers", !!handlers);
    check("soft-edit: has apply-patch handler", typeof handlers["soft-edit/apply-patch"] === "function");
  } catch (e) {
    check("soft-edit: loads without error", false, e.message);
  }

  console.log(`\nResults: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

runTests().catch(e => {
  console.error("Test runner failed:", e);
  process.exit(1);
});
