// search-tool.ts
// Location: ~/.pi/agent/extensions/search-tool.ts
//
// Pi's web_search tool. Every search goes to the search gateway
// (services/search_gateway.py, 127.0.0.1:8090, or the helper PC in split mode), which Open WebUI searches
// through too: the same services, keys, usage counts, limits and cache. It
// doesn't need Open WebUI. SearXNG is only the gateway's own last fallback;
// Pi never searches SearXNG, DuckDuckGo or another engine directly.
//
// The gateway enforces per-user caps (daily budget, max queries), tracks usage,
// and caches recent results to avoid redundant calls.
//
// agent-browser-fix.ts corrects queries before they get here (site: filters,
// unasked years, the search budget).
//
// Repeats: the same words searched again in one task mean the results weren't
// useful, so the repeat skips the cache and every service that answered them
// before, plus the rest of their group (the gateway's retry_groups setting). After
// MAX_RETRIES the same words are refused.
//

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { Type } from "typebox";

// Where the gateway runs: ~/.config/localllm/endpoints.json (lib/endpoints.py; on the helper PC in split mode), read
// at every search so ./localllm.sh switch needs no restart of Pi. SEARCH_GATEWAY_URL overrides it.
const ENDPOINTS = process.env.LOCALLLM_ENDPOINTS || join(homedir(), ".config/localllm/endpoints.json");
export function gateway(): { url: string; where: string; mode: string } {
  let url = "http://127.0.0.1:8090", where = "main", mode = "single";
  try {
    const data = JSON.parse(readFileSync(ENDPOINTS, "utf8"));
    const s = data?.services?.["search-gateway"];
    if (s?.url) { url = String(s.url); where = String(s.where || where); mode = String(data.mode || mode); }
  } catch { /* no endpoints.json yet: the single-mode address */ }
  if (process.env.SEARCH_GATEWAY_URL) url = process.env.SEARCH_GATEWAY_URL;
  return { url: url.replace(/\/+$/, ""), where, mode };
}

const TIMEOUT_MS = 90_000;
const MAX_SNIPPET = 500;
const MAX_RETRIES = 2;

const normalise = (q: string) => q.toLowerCase().split(/\s+/).filter(Boolean).sort().join(" ");

function serverDown(g: { url: string; where: string }): string {
  const fix = g.where === "helper"
    ? "it runs on the helper PC: tell the user to start it there, " +
      "or to run ./localllm.sh switch single"
    : "tell the user to run ./start.sh in the LocalLLM folder";
  return `The Search Gateway server (${g.url}) isn't running, so web_search can't search. ` +
    `Don't search another way (no browser, no curl); ${fix}, then stop.`;
}

export default function (pi: any) {
  // Services that answered each query in this task.
  let answered = new Map<string, string[]>();
  pi.on?.("input", () => { answered = new Map(); });

  pi.registerTool({
    name: "web_search",
    label: "Web Search",
    description:
      "Search the web through the user's search gateway (the same search services and cache as Open WebUI). " +
      "Returns titles, URLs and snippets. Use site:<domain> in the query to search one site.",
    promptSnippet: "Search the web through the search gateway",
    promptGuidelines: [
      "Use web_search when you need up-to-date information, facts, or documentation from the web.",
      "web_search is the only way to search. Never open a search engine's results page in the browser.",
      "If the results aren't useful, the same search again goes to a different kind of search service.",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "Search query. Add site:<domain> to search one site." }),
      numResults: Type.Optional(Type.Number({ description: "Number of results (1-20, default 10)" })),
    }),

    async execute(_id: string, params: any, signal?: AbortSignal, _onUpdate?: any, ctx?: any) {
      const query = String(params.query ?? "").trim();
      if (!query) throw new Error("web_search needs a query.");
      const count = Math.max(1, Math.min(Number(params.numResults) || 10, 20));
      const key = normalise(query);
      const before = answered.get(key) ?? [];
      if (before.length > MAX_RETRIES) {
        return {
          content: [{ type: "text", text:
            `You already searched these words ${before.length} times, each time on a different search service ` +
            `(${before.join(", ")}). Use those results, or try different keywords.` }],
          details: { service: "none", resultCount: 0 },
        };
      }

      const g = gateway();
      const ask = new URLSearchParams({ q: query, format: "json" });
      if (before.length > 0) ask.set("retry_after", before.join(","));

      ctx?.ui?.setStatus?.("search", `🔍 Search Gateway: ${before.length > 0 ? "searching again on another service" : "searching"}...`);
      let attempts = 0;
      while (attempts <= MAX_RETRIES) {
        try {
          const timeout = AbortSignal.timeout(TIMEOUT_MS);
          const res = await fetch(`${g.url}/search?${ask}`, {
            headers: { Accept: "application/json" },
            signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);

          const data = await res.json();
          const results: any[] = (Array.isArray(data?.results) ? data.results : [])
            .filter((r: any) => /^https?:\/\//i.test(String(r?.url ?? "")))
            .slice(0, count);
          const service = String(data?.service || results[0]?.engine || "none");

          if (results.length) answered.set(key, [...before, service.split(",")[0].trim()]);
          ctx?.ui?.setStatus?.("search", `🔍 Search Gateway (${service}): ${results.length} results`);

          const safeQuery = query.replace(/[\n\r]/g, " ");
          const lines = [`## Search Results: "${safeQuery}"`, `Service: ${service}  ·  Results: ${results.length}`];
          if (before.length > 0) {
            lines.push(results.length
              ? `A different search service than your earlier search with these words (${before.join(", ")}).`
              : "No other search service found anything for these words. Use the earlier results, or try different keywords.");
          }
          lines.push("");
          results.forEach((r, i) => {
            lines.push(`### ${i + 1}. ${r.title || "Untitled"}`);
            lines.push(`   URL: ${r.url}`);
            const text = String(r.content ?? r.snippet ?? "");
            if (text) lines.push(`   ${text.slice(0, MAX_SNIPPET)}${text.length > MAX_SNIPPET ? "..." : ""}`);
            if (r.publishedDate) lines.push(`   Date: ${r.publishedDate}`);
            lines.push("");
          });
          if (!results.length && !before.length && (data?.problems?.length)) {
            lines.push(`No results. The tool reported: ${(data.problems as string[]).slice(0, 6).join("; ")}`);
          }

          return {
            content: [{ type: "text", text: lines.join("\n") }],
            details: { service, resultCount: results.length },
          };
        } catch (err: any) {
          attempts++;
          if (attempts > MAX_RETRIES) {
            if (signal?.aborted) throw err;
            return {
              content: [{ type: "text", text: serverDown(g) }],
              details: { service: "none", resultCount: 0 },
            };
          }
        }
      }
    },
  });
}
