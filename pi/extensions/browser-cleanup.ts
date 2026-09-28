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

type BrowserProcess = { pid: number; sid: number; env: Map<string, string> };

function log(message: string) {
  if (DEBUG) console.error(`[browser-cleanup] ${message}`);
}

function sessionNameOf(event: any): string | undefined {
  const name = event?.details?.sessionName ?? event?.details?.data?.sessionName;
  return typeof name === "string" && MANAGED_SESSION_RE.test(name) ? name : undefined;
}

type CustomBrowserMatcher = { paths: Set<string>; names: Set<string>; prefixes: string[] };

function loadCustomBrowserMatcher(): CustomBrowserMatcher | undefined {
  let configured = process.env.AGENT_BROWSER_EXECUTABLE_PATH?.trim() ?? "";
  if (!configured) {
    try {
      const cfgPath = process.env.AGENT_BROWSER_CONFIG || path.join(os.homedir(), ".agent-browser", "config.json");
      const raw = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
      if (typeof raw?.executablePath === "string") configured = raw.executablePath.trim();
    } catch {
      // No config file or unreadable; only built-in agent-browser paths apply.
    }
  }
  if (!configured) return undefined;
  const paths = new Set<string>([configured]);
  const names = new Set<string>([path.basename(configured)]);
  const prefixes: string[] = [];
  try {
    const real = fs.realpathSync(configured);
    paths.add(real);
    names.add(path.basename(real));
    // A Nix wrapper (e.g. /nix/store/<drv>/bin/foo) may exec a binary deeper in the same derivation.
    const nixMatch = real.match(/^(\/nix\/store\/[^/]+\/)/);
    if (nixMatch) prefixes.push(nixMatch[1]);
  } catch {
    // Target may not exist on disk; exact path and basename still apply.
  }
  return { paths, names, prefixes };
}

function isBrowserExe(exe: string, custom?: CustomBrowserMatcher): boolean {
  if (/\/agent-browser[^/]*$/.test(exe) || exe.includes("/.agent-browser/browsers/")) return true;
  if (!custom) return false;
  if (custom.paths.has(exe) || custom.names.has(path.basename(exe))) return true;
  return custom.prefixes.some((p) => exe.startsWith(p));
}

function readBrowserProcess(pid: number, custom?: CustomBrowserMatcher): BrowserProcess | undefined {
  try {
    const exe = fs.readlinkSync(`/proc/${pid}/exe`);
    if (!isBrowserExe(exe, custom)) return;
    const env = new Map<string, string>();
    for (const entry of fs.readFileSync(`/proc/${pid}/environ`, "utf8").split("\0")) {
      const eq = entry.indexOf("=");
      if (eq > 0) env.set(entry.slice(0, eq), entry.slice(eq + 1));
    }
    // /proc/<pid>/stat: "pid (comm) state ppid pgrp session ...". comm may contain spaces.
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    const sid = Number(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[3]);
    return { pid, sid, env };
  } catch {
    return; // exited, or not ours to read
  }
}

function findOwned(owner: string, names: Set<string>): BrowserProcess[] {
  const custom = loadCustomBrowserMatcher();
  const all: BrowserProcess[] = [];
  for (const entry of fs.readdirSync("/proc")) {
    const pid = Number(entry);
    if (!Number.isInteger(pid) || pid === process.pid) continue;
    const found = readBrowserProcess(pid, custom);
    if (found) all.push(found);
  }
  const isNamedDaemon = (p: BrowserProcess) =>
    p.env.has("AGENT_BROWSER_DAEMON") && names.has(p.env.get("AGENT_BROWSER_SESSION") ?? "");
  // A named daemon runs in its own process session, with its Chrome processes.
  const namedSessions = new Set(all.filter(isNamedDaemon).map((p) => p.sid));
  return all.filter((p) => p.env.get(OWNER_ENV) === owner || namedSessions.has(p.sid));
}

function isRunning(pid: number): boolean {
  try {
    // A zombie keeps its /proc entry but has already exited.
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    return stat.slice(stat.lastIndexOf(")") + 2, stat.lastIndexOf(")") + 3) !== "Z";
  } catch {
    return false;
  }
}

async function waitGone(pids: number[], ms: number): Promise<number[]> {
  const deadline = Date.now() + ms;
  let alive = pids.filter(isRunning);
  while (alive.length && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 200));
    alive = alive.filter(isRunning);
  }
  return alive;
}

function signal(pids: number[], name: NodeJS.Signals) {
  for (const pid of pids) {
    try { process.kill(pid, name); } catch { /* already gone */ }
  }
}

function politeClose(daemon: BrowserProcess): Promise<void> {
  const session = daemon.env.get("AGENT_BROWSER_SESSION");
  if (!session) return Promise.resolve();
  const socketDir = daemon.env.get("AGENT_BROWSER_SOCKET_DIR");
  // Without its socket, `close` would start a new daemon instead of failing.
  if (!socketDir || !fs.existsSync(path.join(socketDir, `${session}.sock`))) return Promise.resolve();
  const env = { ...process.env, AGENT_BROWSER_SOCKET_DIR: socketDir };
  return new Promise((resolve) => {
    execFile(BIN, ["--session", session, "close"], { timeout: 10000, env }, () => resolve());
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
