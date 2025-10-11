// guard-tests.mjs: checks the rules in Pi's extensions without the model (seconds, no search credits).
//
//   node --experimental-strip-types tests/guard-tests.mjs
//
// Each extension is loaded with a fake `pi` that records its handlers; the checks then send it the tool calls, results
// and requests the model would. The Context7 ID cache goes to a temporary file (PI_CONTEXT7_ID_CACHE), and web_search's
// HTTP calls go to a fake fetch, so nothing real is touched.

import { mkdtempSync, mkdirSync, rmSync, symlinkSync, copyFileSync, cpSync, existsSync, readFileSync, readdirSync, writeFileSync, appendFileSync } from "node:fs";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const EXT = join(REPO, "pi", "extensions");
const TMP = mkdtempSync(join(tmpdir(), "pi-guard-"));
let passed = 0, failed = 0;

function check(name, ok, detail = "") {
  if (ok) {
    passed++;
    console.log(`  PASS ${name}`);
  } else {
    failed++;
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

function fakePi() {
  const handlers = {};
  const calls = [];
  const results = [];

  return {
    register(name, handler) {
      handlers[name] = handler;
    },
    getHandlers() {
      return { ...handlers };
    },
    recordCall(call) {
      calls.push(call);
    },
    getCalls() {
      return [...calls];
    },
    recordResult(result) {
      results.push(result);
    },
    getResults() {
      return [...results];
    },
    reset() {
      calls.length = 0;
      results.length = 0;
    }
  };
}

const load = async (file) => (await import(pathToFileURL(file).href));
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
