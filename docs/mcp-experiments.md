# MCP Server Integration Notes

## Overview

Exploring Model Context Protocol (MCP) as a standard interface for connecting local LLMs to external tools and data sources. This document tracks early experiments with setting up an MCP server for the LocalLLM pipeline.

## Why MCP?

- Standardized protocol for tool/function calling between LLMs and external services
- Reduces custom integration code for each new data source
- Enables hot-swapping tools without restarting the server

## Experiment Setup

```bash
# Install MCP server dependencies
pip install mcp-server

# Start the local MCP server
python -m mcp_server --port 8765 --models qwen2.5-14b-instruct

# Test connection
curl -X POST http://localhost:8765/initialize \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
```

## Tool Endpoints Tested

| Tool | Purpose | Status |
|------|---------|--------|
| `file_search` | Local filesystem search via grep/ripgrep | Working |
| `web_fetch` | HTTP fetch with caching | Working |
| `code_index` | SQLite-backed code symbol lookup | In progress |

## Challenges & Lessons

- Tool call parsing is fragile when descriptions are too verbose; prefer short, imperative names
- JSON-RPC 2.0 error codes need consistent mapping (e.g., -32601 for unknown method)
- The server must handle connection drops gracefully — restarts without state loss

## Observations

- Qwen2.5-14B-Instruct handles MCP tool schemas well with proper context window (32k)
- GPU offload (partial, ~20 layers on GPU) keeps inference at ~8 tok/s
- Tool descriptions must be concise (<200 chars) for best LLM comprehension
- Error responses from MCP tools should include structured JSON for better recovery

## Next Steps

- Add `git_diff` tool for repository-aware changes
- Benchmark latency vs. direct function calling (no MCP overhead)
- Document tool schema conventions for team use
- Experiment with streaming tool results back through the MCP transport
