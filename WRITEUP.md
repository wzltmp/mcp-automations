# Building a production-grade MCP server

A reflection on the design decisions in [mcp-automations](https://github.com/wzltmp/mcp-automations) — a Model Context Protocol server that's live in three places (a [Streamlit playground](https://mcp-automations-5vgea2ynuyrvbzkcxm6yoh.streamlit.app/), a [Fly.io HTTPS endpoint](https://mcp-automations.fly.dev/mcp), and the [Official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers?search=mcp-automations) as `io.github.wzltmp/mcp-automations`) and what I'd tell another engineer who wanted to build one in 2026.

## Why MCP at all

Most AI portfolio projects are *applications* — a chat UI over a RAG pipeline, an agent loop that does research, a copilot. They demonstrate that you can wire an LLM into a product. The thing that almost no one's resume shows is the layer underneath: **building the tools an LLM calls, not just calling them**.

The Model Context Protocol is Anthropic's open standard for that layer. It's transport-agnostic — the same server can be plugged into Claude Desktop as a subprocess (stdio transport) or hosted publicly behind HTTPS (streamable-HTTP transport) and any spec-conformant client can talk to either. By early 2026 the SDK is pulling ~97M monthly downloads, but the population of engineers who've shipped a server is still small. That asymmetry is the entire reason this project exists.

The pitch I'd make to a hiring manager: *projects 01 and 02 in my portfolio are LLM-powered apps; this one is infrastructure those kinds of apps consume.*

## Two transports, one codebase

The original spec call here was to support stdio only — Claude Desktop is the canonical MCP client, it runs servers as local subprocesses, and that's enough for a demo. I picked up streamable-HTTP because it changes what the project can *prove*.

```python
# mcp_server/server.py
def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
```

That's the entire transport branch. `FastMCP` reads `host`/`port` from constructor args wired to env vars, which means a Docker container on Fly.io can set `MCP_HOST=0.0.0.0` and `PORT=$PORT` without any code change. Same Python image runs both ways.

What this buys: a recruiter can `curl -X POST https://mcp-automations.fly.dev/mcp` from their laptop *right now*. The "two integration shapes from one codebase" claim isn't theoretical, it's something you can verify in 30 seconds. That's a different artifact than "here's a server that works on my machine."

The cost was small — about 10 extra lines plus a `Dockerfile` and `fly.toml`. The signal it produces is much bigger than the effort.

## Cost telemetry as a first-class return value

Every tool in this server returns a Pydantic model that includes a `Cost` field with model name, input/output tokens, and USD:

```python
class Cost(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0

class SummaryResponse(BaseModel):
    url: str
    n_bullets: int
    summary: str
    cost: Cost
```

Most demos return a string and lose this signal entirely. I made cost a first-class return value because once you're routing across multiple models, you can't reason about your bill without it.

That cost field shows up in the Streamlit playground sidebar as a running session total ("Total spend: $0.018, 4 calls this session"). It would also flow through the streamable-HTTP transport to any client that wanted to display it. The whole `_chat()` helper is built around the pattern:

```python
def _chat(model: str, prompt: str, max_tokens: int = 1024) -> tuple[str, Cost]:
    msg = _get_anthropic().messages.create(...)
    cost = Cost(
        model=model,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        usd=_usd_cost(model, msg.usage.input_tokens, msg.usage.output_tokens),
    )
    return text, cost
```

Per-tool code stays under 10 lines because the helper handles the bookkeeping.

## Cost-aware model routing

Two of the tools (`summarize_url`, `daily_digest`) are short, well-defined tasks where Haiku 4.5 at $1/M input is plenty. The other two (`repurpose_content`, `find_competitors`) need higher writing quality and structured-JSON adherence, so they go to Sonnet 4.6 at $3/M input and $15/M output.

That's the kind of thinking AI-at-scale teams care about — it's the difference between a demo that costs $0.0005 per call and one that costs $0.005. At low volume, irrelevant. At any kind of real usage, the routing is the entire margin.

The trick is keeping it explicit. The constants live at the top of `server.py`:

```python
CHEAP_MODEL = "claude-haiku-4-5-20251001"
WRITER_MODEL = "claude-sonnet-4-6"
PRICING: dict[str, dict[str, float]] = {
    CHEAP_MODEL: {"input": 1.00, "output": 5.00},
    WRITER_MODEL: {"input": 3.00, "output": 15.00},
}
```

Each tool literally writes `CHEAP_MODEL` or `WRITER_MODEL` in its call to `_chat()`. There's no clever auto-routing layer because there doesn't need to be — the routing happens at design time, not at runtime, and it's right there in the code review for anyone who wants to argue with the call.

## A domain-specific exception hierarchy

Instead of catching `Exception` and printing it, this server raises typed errors:

```python
class MCPToolError(Exception): ...
class UpstreamAPIError(MCPToolError): ...        # Anthropic, Tavily, or httpx failed
class EmptyLLMResponseError(MCPToolError): ...   # model returned no text block
class ExtractionError(MCPToolError): ...         # trafilatura couldn't find article text
```

This matters in three places:
- **Logs**: a fetch failure looks different from an LLM refusal looks different from an empty extraction. Each routes to a different debugging path.
- **The Streamlit playground**: the catch is `except MCPToolError as exc: st.error(f"{type(exc).__name__}: {exc}")`. Errors render as friendly inline messages, not Python tracebacks.
- **Future MCP clients**: a remote client could route differently on these (retry on `UpstreamAPIError`, give up on `ExtractionError`).

It's three small classes. The value isn't the code, it's that errors become *self-documenting* — anyone reading a log can tell instantly what kind of failure they're looking at.

## Pydantic outputs save the next person from re-parsing

The MCP spec lets tools return arbitrary JSON, but most demos return strings and force the downstream client to re-parse them with the LLM. This server returns typed Pydantic models:

```python
class CompetitorsResponse(BaseModel):
    domain: str
    competitors: list[Competitor]
    cost: Cost

class Competitor(BaseModel):
    name: str
    url: str
    rationale: str
```

A client invoking `find_competitors` gets back a structured object. It can iterate `result.competitors`, read `c.url`, render `c.rationale` — no extra LLM call to "extract competitor list from this text." For repeated calls in a real pipeline, that's both faster and dramatically cheaper.

The downstream parser still has to handle Sonnet occasionally wrapping its JSON in prose ("Here are the competitors: {…}"), which is what `_parse_competitors_json` does — strict parse first, then a brace-slice fallback that grabs the first `{` to the last `}`. Returning an empty list when both fail is better than crashing the tool call.

## The Streamlit playground is a "faithful preview"

The playground calls the tool functions *directly*, not through the MCP transport:

```python
from mcp_server.server import (
    daily_digest, find_competitors,
    repurpose_content, summarize_url,
)
```

This was a deliberate choice. The MCP transport layer is just a wrapper — `FastMCP` decorates Python functions with `@mcp.tool()` and handles the JSON-RPC serialization, but the functions themselves are pure callables. Calling them directly from Streamlit gives me a UI that's a faithful preview of what a Claude client would see: the same Pydantic models, the same exceptions, the same costs.

The alternative would have been to spin up the streamable-HTTP transport in-process and have Streamlit hit it over loopback. That'd be more "real" in some pedantic sense and would let me verify the JSON-RPC layer end-to-end from the browser, but it adds a transport hop and serialization roundtrip for zero practical benefit. Direct call wins on simplicity and on debuggability.

## Public-app cost protection

Once the Streamlit playground became publicly reachable, anyone with the URL could burn down my Anthropic credits. The protection ended up being two layers:

**Layer A (the real backstop): a $2/month hard cap on the Anthropic console.** Once spend hits the cap, the API returns 429s for the rest of the month. Code-level limits are bypassable by an attacker who reads the source; the console cap is not.

**Layer B (deters casual abuse): per-session call and spend caps in the playground.** A `_guard()` helper refuses calls after 20/session or $0.50/session, with the remaining headroom shown in the sidebar. Bypassable by clearing cookies, but it makes the existence of the limit obvious to honest users and stops scraper bots that don't rotate sessions.

I skipped IP-level rate limiting on Streamlit Cloud — the deployment doesn't natively support it, and Layers A+B cover the actual risk for a portfolio app. If I were running this for a real customer, Layer C would be a Redis counter keyed on `X-Forwarded-For`. For a recruiter-facing demo, the math doesn't justify it.

## Honest tradeoffs

What this project doesn't yet have:

- **Production traffic.** It's a portfolio piece, not a system serving real users. The cost model and exception hierarchy will get more interesting under actual load.
- **MCP sampling.** The spec lets servers ask the *client* to make an LLM call on the server's behalf (so the client's API key gets billed, not the server's). I haven't added a tool that uses it yet. Doing so would demo that I've read the full spec, not just the tools section.
- **An evaluation harness.** I have 23 offline unit tests, which prove the code runs. I don't yet have evals that measure LLM-output quality across N cases. For a tool catalog meant to be invoked at scale, evals would matter more than unit tests.

If I started over, I'd write the playground *first*, not last. I built the server with the production-grade refactor in mind (Pydantic, costs, exceptions, two transports) and then bolted the UI on top. A real product team would have pushed me to ship the playground on day one with a single tool, even if everything else was rough, because the playground is what produces the screenshot that goes on the project landing page. The server can be polished after.

## What I'd tell another engineer in 2026

If you're building your first MCP server:

- **Pick FastMCP over the lower-level Python SDK** unless you have a specific reason. FastMCP's decorator API gets you 90% of what you need with 10% of the code.
- **Use all three primitives, not just tools.** A resource that exposes your tool catalog as JSON costs nothing to add and signals you read the spec. A prompt that chains two tools demonstrates the composition story. Both are rare in demos.
- **Return Pydantic models, not strings.** It's almost free and it's the difference between "useful" and "the next person has to re-parse this."
- **Attach cost data to your responses.** You can't justify model-routing decisions without it, and once you've built the helper it's free per call.
- **Make the server runnable two ways from day one.** Stdio + streamable-HTTP is one branch in `main()`. The HTTP path unlocks Fly.io / Cloudflare Workers / wherever later — don't paint yourself into the stdio corner.
- **Submit to the [Official MCP Registry](https://registry.modelcontextprotocol.io/).** `mcp-publisher init && publish` takes 10 minutes if you have a Fly URL ready. Searchable proof you built something other people can use.

The hard part isn't the code — it's the design decisions you make before you write any. This writeup is mostly about those.
