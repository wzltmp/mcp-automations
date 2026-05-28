# MCP Automations

A production-grade Model Context Protocol server in Python — four LLM-callable tools, two transports, deployed two different ways.

| | URL |
|---|---|
| **Source** | https://github.com/wzltmp/mcp-automations |
| **Playground (browser demo)** | https://mcp-automations-5vgea2ynuyrvbzkcxm6yoh.streamlit.app/ |
| **MCP HTTP server** | https://mcp-automations.fly.dev/mcp |

```bash
# 30-second proof the server is up:
curl -X POST https://mcp-automations.fly.dev/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"initialize","id":1,
       "params":{"protocolVersion":"2024-11-05",
                 "capabilities":{},
                 "clientInfo":{"name":"curl","version":"1"}}}'
```

## What this is

Most "AI engineer" portfolio projects are *applications* (a RAG chatbot, an agent that does research). This project is the **layer underneath** — the typed tools an LLM can call and the transport plumbing that exposes them. MCP is the emerging standard for LLM tool use (~97M monthly SDK downloads as of early 2026); building one — not just consuming one — is the rare skill.

For a deeper look at the design decisions — why two transports, how cost telemetry works, the exception hierarchy, what I'd do differently — see [WRITEUP.md](WRITEUP.md).

## Tools

| Tool | Model | What it does |
|---|---|---|
| `summarize_url(url, n_bullets)` | Haiku 4.5 | Fetch a page, extract clean text with trafilatura, return an N-bullet summary |
| `repurpose_content(text, format)` | Sonnet 4.6 | Turn long-form text into a twitter thread, linkedin post, or newsletter |
| `daily_digest(topic, n_results)` | Haiku 4.5 | Tavily news search + ~200-word digest with citations |
| `find_competitors(domain, n)` | Sonnet 4.6 | Identify N plausible competitors for a company by domain |

Plus one **MCP resource** (`automations://catalog`) and one **MCP prompt** (`daily_brief`) — using all three MCP primitives, not just tools.

Every tool returns a typed Pydantic model with **per-call token usage and dollar cost** attached. Cheap tasks route to Haiku 4.5 ($1/M in, $5/M out), writing-heavy tasks to Sonnet 4.6 ($3/M in, $15/M out).

## Connect Claude Desktop to this server

Add one of these to `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows), then restart Claude Desktop.

**Option A — local stdio** (no network, runs the server as a subprocess):

```jsonc
{
  "mcpServers": {
    "mcp-automations": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/mcp-automations",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "TAVILY_API_KEY": "tvly-..."
      }
    }
  }
}
```

**Option B — remote HTTP** (talks to the live Fly server, no local setup):

```jsonc
{
  "mcpServers": {
    "mcp-automations": {
      "url": "https://mcp-automations.fly.dev/mcp",
      "transport": "http"
    }
  }
}
```

Then ask Claude something like *"summarize https://www.paulgraham.com/greatwork.html in 3 bullets"* — it'll call `summarize_url` automatically.

## Run locally

```bash
pip install -r requirements.txt

# Stdio (for Claude Desktop):
python -m mcp_server.server

# HTTP server (defaults to 0.0.0.0:8765):
MCP_TRANSPORT=http python -m mcp_server.server

# Streamlit playground:
streamlit run playground/app.py
```

Requires Python 3.13. Needs `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` in `.env` (see `.env.example`).

## Architecture

```
┌────────────────┐     stdio      ┌──────────────────────┐
│ Claude Desktop ├───────────────►│                      │
└────────────────┘                │                      │
                                  │   mcp_server/        │
┌────────────────┐    HTTP/JSON   │   server.py          │
│ Remote client  ├───────────────►│   (FastMCP)          │
└────────────────┘   (Fly.io)     │                      │
                                  │   4 tools            │
┌────────────────┐  direct call   │   1 resource         │
│ Streamlit UI   ├───────────────►│   1 prompt           │
└────────────────┘                └──────────┬───────────┘
                                             │
                                  ┌──────────┴───────────┐
                                  │ Anthropic + Tavily   │
                                  │ (lazy clients)       │
                                  └──────────────────────┘
```

The same Python callables back all three entry points. The transport is just a wrapper.

## What's in this repo

```
mcp-automations/
├── mcp_server/
│   ├── server.py        # FastMCP server: 4 tools + 1 resource + 1 prompt
│   ├── models.py        # Pydantic I/O schemas (incl. per-call Cost telemetry)
│   └── exceptions.py    # MCPToolError + UpstreamAPIError / EmptyLLMResponseError / ExtractionError
├── playground/
│   └── app.py           # Streamlit UI with per-session call + spend caps
├── tests/               # offline unit tests (httpx/anthropic/tavily all mocked)
├── Dockerfile           # python:3.13-slim, MCP_TRANSPORT=http for Fly
├── fly.toml             # shared-cpu-1x, 256mb, auto-stop when idle
└── .github/workflows/   # ruff + strict mypy + pytest on every push
```

## Production touches worth noting

- **Cost telemetry on every tool response** (`models.Cost`) — token counts and USD attached so a client doesn't have to re-derive it.
- **Cost-aware model routing** — cheap tasks → Haiku, writing tasks → Sonnet.
- **Domain-specific exception hierarchy** — `UpstreamAPIError`, `EmptyLLMResponseError`, `ExtractionError` each route differently in logs and the Streamlit UI.
- **Two transports, one codebase** — `MCP_TRANSPORT=stdio|http` env switch; HTTP host/port from env so the same image runs on Fly.
- **Per-session abuse caps in the playground** — 20 calls / $0.50 max per session; backed by a $2/mo hard cap on the Anthropic console.
- **Strict mypy + ruff + pytest in CI** on every push (`.github/workflows/ci.yml`).

## Why MCP

MCP is transport-agnostic, so one server serves both a local Claude Desktop user (stdio subprocess) and a hosted multi-tenant deployment (HTTPS). It also exposes three primitives that most demos skip:

- **Tools** — functions the model decides to call (4 of them here)
- **Resources** — read-only data the client can fetch by URI (`automations://catalog` returns the tool list as JSON)
- **Prompts** — server-side templates the user explicitly invokes (`daily_brief` chains `daily_digest` + `repurpose_content`)

Using all three is a signal of reading the spec, not just a quickstart.

## Status

✅ Code on GitHub, CI green
✅ Public playground on Streamlit Cloud
✅ Public MCP HTTP server on Fly.io
✅ Cost protection (per-session caps + monthly Anthropic cap)
✅ Real test coverage (23 offline unit tests)
✅ [Listed on the Official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers?search=mcp-automations) as `io.github.wzltmp/mcp-automations`
✅ [Long-form writeup](WRITEUP.md) of design decisions
🚧 Demo gif + screenshots (planned)
🚧 n8n self-host via docker-compose (planned)

## License

MIT.
