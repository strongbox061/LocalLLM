// mcp-sign-in-key.ts
// Location: ~/.pi/agent/extensions/mcp-sign-in-key.ts
//
// pi-mcp-adapter keeps the Context7 sign-in in its own encrypted file (settings.oauthCredentialStore =
// "encrypted-file" in mcp-adapter.json). Its key, PI_MCP_ADAPTER_OAUTH_FILE_KEY, is made once by install/pi-mcp.sh
// and kept in ~/.config/localllm/local.env. The adapter reads it from Pi's environment whenever it opens that file,
// so this sets it when Pi starts: no login session, desktop or shell setup is needed for Pi to have it.
// An environment variable that is already set wins. The key is never printed.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const KEY_NAME = "PI_MCP_ADAPTER_OAUTH_FILE_KEY";
const LOCAL_ENV = path.join(os.homedir(), ".config", "localllm", "local.env");

// The value of KEY_NAME in a shell-style env file (KEY="value" or KEY=value), or "" when it isn't there.
export function keyFromEnvFile(text: string): string {
  const m = text.match(new RegExp(`^\\s*${KEY_NAME}=(?:"([^"\\n]*)"|'([^'\\n]*)'|([^\\s#]*))`, "m"));
  return (m?.[1] ?? m?.[2] ?? m?.[3] ?? "").trim();
}

function loadKey(): void {
  if (process.env[KEY_NAME]) return;
  try {
    const key = keyFromEnvFile(fs.readFileSync(LOCAL_ENV, "utf8"));
    if (key.length > 0) process.env[KEY_NAME] = key;
  } catch {
    // No local.env yet: install/pi-mcp.sh makes the key; until then Context7 asks for a sign-in.
  }
}

// As early as possible (module load), so the key is there before any MCP server connects.
loadKey();

export default function (_pi: any) {
  loadKey();
}
