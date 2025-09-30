// mcp-sign-in-key.ts
// Location: ~/.pi/agent/extensions/mcp-sign-in-key.ts
//
// pi-mcp-adapter keeps the Context7 sign-in in its own encrypted file (settings.oauthCredentialStore =
// "encrypted-file" in mcp-adapter.json). Its key, PI_MCP_ADAPTER_OAUTH_FILE_KEY, is made once by install/pi-mcp.sh
// and kept in ~/.config/localllm/local.env. The adapter reads it from Pi's environment whenever it opens that file,
// so this extension ensures the key is available to Pi's process when the MCP server starts.

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const KEY_NAME = "PI_MCP_ADAPTER_OAUTH_FILE_KEY";
const LOCAL_ENV = path.join(os.homedir(), ".config", "localllm", "local.env");

export function keyFromEnvFile(text: string): string {
  const match = text.match(new RegExp(`^${KEY_NAME}=(.+)$`, "m"));
  return match?.[1]?.trim() ?? "";
}

function loadKey(): void {
  if (!fs.existsSync(LOCAL_ENV)) {
    console.error("[mcp-sign-in-key] local.env not found at", LOCAL_ENV);
    return;
  }

  const text = fs.readFileSync(LOCAL_ENV, "utf-8");
  const key = keyFromEnvFile(text);

  if (!key) {
    console.error("[mcp-sign-in-key] Key not found in local.env");
    return;
  }

  process.env[KEY_NAME] = key;
}

export default function (_pi: any) {
  loadKey();
}
