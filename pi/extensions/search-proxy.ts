// search-proxy.ts
// Location: ~/.pi/agent/extensions/search-proxy.ts
//
// Pi's web_search tool. Every search goes to the search gateway
// (services/search_gateway.py, 127.0.0.1:8090, or the helper PC in split mode), which Open WebUI searches
// through too: the same services, keys, usage counts, limits and cache. It
// doesn't need Open WebUI. SearXNG is only the gateway's own last fallback;
// Pi never searches SearXNG, DuckDuckGo or another engine directly.
//
// The gateway enforces per-user caps (daily budget, max queries), tracks usage,
// and caches recent results to avoid redundant calls. It also respects unasked
// years, the search budget).
//

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { Type } from "typebox";

const ENDPOINTS = process.env.LOCALLLM_ENDPOINTS || join(homedir(), ".config/localllm/endpoints.json");

export function gateway(): { url: string; where: string; mode: string } {
  try {
    const endpoints = JSON.parse(readFileSync(ENDPOINTS, "utf-8"));
    if (endpoints.helper && endpoints.helper.searchGateway) {
      return { url: endpoints.helper.searchGateway, where: "helper", mode: "split" };
    }
  } catch { /* fallback below */ }

  return { url: "http://127.0.0.1:8090", where: "local", mode: "standalone" };
}

const TIMEOUT_MS = 90_000;
const MAX_SNIPPET = 500;
const MAX_RETRIES = 2;

const normalise = (q: string) => q.toLowerCase().split(/\s+/).filter(Boolean).sort().join(" ");

function serverDown(g: { url: string; where: string }): string {
  return `Search gateway at ${g.url} (${g.where}) is unreachable`;
}

export default function (pi: any) {
  pi.registerTool({
    name: "web_search",
    description: "Search the web via the local search gateway. Returns a list of results with titles, URLs, and snippets.",
    parameters: Type.Object({
      query: Type.String({ description: "The search query string" }),
    }),
    handler: async ({ query }: { query: string }) => {
      const g = gateway();
      const url = `${g.url}/search?q=${encodeURIComponent(query)}`;

      let attempts = 0;
      while (attempts <= MAX_RETRIES) {
        try {
          const res = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);

          const data = await res.json();
          const results: any[] = (data.results || []).slice(0, 10);
          return results.map((r: any) => ({
            title: (r.title || "").slice(0, MAX_SNIPPET),
            url: r.url || "",
            snippet: (r.snippet || "").slice(0, MAX_SNIPPET),
          }));
        } catch (err: any) {
          attempts++;
          if (attempts > MAX_RETRIES) {
            return { error: serverDown(g), detail: err.message };
          }
        }
      }
    },
  });
}
