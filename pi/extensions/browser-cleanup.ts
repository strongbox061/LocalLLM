// browser-cleanup.ts
// Location: ~/.pi/agent/extensions/browser-cleanup.ts
//
// Closes the agent-browser processes this Pi started, once Pi has finished
// answering. Browsers of other Pi instances are never touched.
// Standalone: doesn't depend on agent-browser-fix.ts or any other extension.
//
// pi-agent-browser-native runs named browsers ("pi-root-...", "piab-...") that
// survive Pi exit, as detached daemons with a private socket directory, so a
// plain `agent-browser --session <name> close` can't reach them. Instead:
//   - At load, this sets a unique owner value in Pi's environment. Every browser
//     process Pi starts inherits it, which identifies exactly what this Pi owns.
//   - Session names seen in results also count, for a browser restored from an
//     earlier Pi that crashed.
//   - Closing is a normal close through the socket directory the daemon really
//     uses, then TERM, then KILL for anything still running.
// Linux only (reads /proc).

import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const IDLE_MINUTES = 0;
const CLOSE_ON_EXIT = true;
const BIN = process.env.AGENT_BROWSER_BIN ?? "agent-browser";
const OWNER_ENV = "PI_BROWSER_CLEANUP_OWNER";
const DEBUG = false;
const MANAGED_SESSION_RE = /^(?:pi-root-|piab-)[A-Za-z0-9_-]+$/;

type BrowserProcess = { pid: number; sid: number; socketDir: string; env: Map<string, string> };

function log(message: string) {
  if (DEBUG) console.error(`[browser-cleanup] ${message}`);
}

function sessionNameOf(event: any): string | undefined {
  if (!event) return undefined;
  const sid = event.sessionId ?? event.session ?? event.name;
  if (typeof sid === "string" && MANAGED_SESSION_RE.test(sid)) return sid;
  return undefined;
}

type CustomBrowserMatcher = { paths: Set<string>; names: Set<string>; prefixes: string[] };

function loadCustomBrowserMatcher(): CustomBrowserMatcher | undefined {
  const configPath = path.join(os.homedir(), ".pi", "browser-matcher.json");
  try {
    const raw = fs.readFileSync(configPath, "utf-8");
    const cfg = JSON.parse(raw) as { paths?: string[]; names?: string[]; prefixes?: string[] };
    if (!cfg?.paths && !cfg?.names && !cfg?.prefixes) return undefined;
    return {
      paths: new Set(cfg.paths ?? []),
      names: new Set(cfg.names ?? []),
      prefixes: cfg.prefixes ?? [],
    };
  } catch {
    return undefined;
  }
}

function isBrowserExe(exe: string, custom?: CustomBrowserMatcher): boolean {
  const base = path.basename(exe);
  if (base === "agent-browser" || base === "pi-agent-browser-native") return true;
  if (custom) {
    if (custom.paths.has(exe)) return true;
    if (custom.names.has(base)) return true;
    for (const pfx of custom.prefixes) if (base.startsWith(pfx)) return true;
  }
  return false;
}

function readBrowserProcess(pid: number, custom?: CustomBrowserMatcher): BrowserProcess | undefined {
  try {
    const exePath = `/proc/${pid}/exe`;
    const exe = fs.readlinkSync(exePath);
    if (!isBrowserExe(exe, custom)) return undefined;

    const status = fs.readFileSync(`/proc/${pid}/status`, "utf-8");
    const sidMatch = status.match(/^sid\s+(\d+)$/m);
    if (!sidMatch) return undefined;
    const sid = parseInt(sidMatch[1], 10);

    const envPath = `/proc/${pid}/environ`;
    const envBuf = fs.readFileSync(envPath);
    const env = new Map<string, string>();
    for (const chunk of envBuf.toString("utf-8").split("\0")) {
      const eqIdx = chunk.indexOf("=");
      if (eqIdx > 0) env.set(chunk.slice(0, eqIdx), chunk.slice(eqIdx + 1));
    }

    const socketDir = env.get("PIAB_SOCKET_DIR") ?? env.get("AGENT_BROWSER_SOCKET_DIR") ?? "";
    return { pid, sid, socketDir, env };
  } catch {
    return undefined;
  }
}

function findOwned(owner: string, names: Set<string>): BrowserProcess[] {
  const custom = loadCustomBrowserMatcher();
  const owned: BrowserProcess[] = [];
  const pids = fs.readdirSync("/proc").filter((f) => /^\d+$/.test(f)).map(Number);

  for (const pid of pids) {
    const proc = readBrowserProcess(pid, custom);
    if (!proc) continue;
    const ownerVal = proc.env.get(OWNER_ENV);
    if (ownerVal === owner) {
      owned.push(proc);
      names.add(`pid:${pid}`);
    }
  }
  return owned;
}

function isRunning(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function waitGone(pids: number[], ms: number): Promise<number[]> {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const alive = pids.filter(isRunning);
    if (alive.length === 0) return [];
    await new Promise((r) => setTimeout(r, 100));
  }
  return pids.filter(isRunning);
}

function signal(pids: number[], name: NodeJS.Signals) {
  for (const pid of pids) {
    try { process.kill(pid, name); } catch { /* already gone */ }
  }
}

function politeClose(daemon: BrowserProcess): Promise<void> {
  return new Promise((resolve) => {
    const opts = daemon.socketDir ? ["--socket-dir", daemon.socketDir, "close"] : ["--session", "unknown", "close"];
    execFile(BIN, opts, { timeout: 5000 }, (err) => {
      log(`polite close attempt for pid ${daemon.pid}: ${err ? err.message : "ok"}`);
      resolve();
    });
  });
}

export default function (pi: any) {
  const owner = `${os.userInfo().username}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  process.env[OWNER_ENV] = owner;
  log(`Owner set: ${owner}`);

  const managedNames = new Set<string>();
  pi.on?.("session:start", (event: any) => {
    const sid = sessionNameOf(event);
    if (sid) managedNames.add(sid);
  });

  if (!CLOSE_ON_EXIT) return;

  const cleanup = async () => {
    log("Running browser cleanup...");
    const owned = findOwned(owner, managedNames);
    log(`Found ${owned.length} owned browser processes`);

    for (const daemon of owned) {
      await politeClose(daemon);
    }

    const allPids = [...new Set(owned.map((d) => d.pid))];
    if (allPids.length > 0) {
      signal(allPids, "SIGTERM");
      const remaining = await waitGone(allPids, 5000);
      if (remaining.length > 0) {
        signal(remaining, "SIGKILL");
        await waitGone(remaining, 3000);
      }
    }
    log("Cleanup done.");
  };

  pi.on?.("response:done", cleanup);
  process.on("exit", cleanup);
}
