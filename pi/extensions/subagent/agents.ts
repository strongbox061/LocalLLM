/**
 * Agent discovery and configuration
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { CONFIG_DIR_NAME, getAgentDir, parseFrontmatter } from "@earendil-works/pi-coding-agent";

export type AgentScope = "user" | "project" | "both";

export interface AgentConfig {
  name: string;
}

export interface AgentDiscoveryResult {
  userAgents: AgentConfig[];
  projectAgents: AgentConfig[];
}

type AgentFrontmatter = Record<string, unknown>;

function parseToolList(value: unknown): string[] | undefined {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === "string");
  }
  return undefined;
}

function loadAgentsFromDir(dir: string, source: "user" | "project"): AgentConfig[] {
  const agents: AgentConfig[] = [];

  if (!fs.existsSync(dir)) {
    return agents;
  }

  const entries = fs.readdirSync(dir, { withFileTypes: true });

  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }

    const agentDir = path.join(dir, entry.name);
    const configPath = path.join(agentDir, "SKILL.md");

    if (!fs.existsSync(configPath)) {
      continue;
    }

    try {
      const content = fs.readFileSync(configPath, "utf-8");
      const frontmatter = parseFrontmatter(content);

      agents.push({
        name: entry.name,
      });
    } catch {
      // Skip agents with invalid config files
    }
  }

  return agents;
}

function isDirectory(p: string): boolean {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function findNearestProjectAgentsDir(cwd: string): string | null {
  let current = cwd;

  while (current !== path.parse(current).root) {
    const candidate = path.join(current, "pi", "extensions", "subagent");

    if (isDirectory(candidate)) {
      return candidate;
    }

    current = path.dirname(current);
  }

  return null;
}

export function discoverAgents(cwd: string, scope: AgentScope): AgentDiscoveryResult {
  const userAgentsDir = getAgentDir(CONFIG_DIR_NAME);
  let projectAgentsDir: string | null = findNearestProjectAgentsDir(cwd);

  const userAgents = loadAgentsFromDir(userAgentsDir, "user");
  const projectAgents = projectAgentsDir
    ? loadAgentsFromDir(projectAgentsDir, "project")
    : [];

  if (scope === "user") {
    return { userAgents, projectAgents: [] };
  }

  if (scope === "project") {
    return { userAgents: [], projectAgents };
  }

  return { userAgents, projectAgents };
}

export function formatAgentList(agents: AgentConfig[], maxItems: number): { text: string; remaining: number } {
  const displayAgents = agents.slice(0, maxItems);
  const remaining = Math.max(0, agents.length - maxItems);

  const lines = displayAgents.map((agent, index) => {
    const suffix = index < displayAgents.length - 1 ? "," : "";
    return `  ${agent.name}${suffix}`;
  });

  let text = lines.join("\n");

  if (remaining > 0) {
    text += `\n  ... and ${remaining} more`;
  }

  return { text, remaining };
}
