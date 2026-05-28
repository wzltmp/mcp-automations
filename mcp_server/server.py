"""MCP server exposing useful tools to LLM clients (Claude Desktop, the Streamlit playground, etc.).

Run:
    python -m mcp_server.server              # stdio transport for Claude Desktop
    python -m mcp_server.server --http :8765 # streamable-HTTP transport for remote clients

Each tool returns a typed Pydantic model with per-call token usage and $ cost.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
import trafilatura
from anthropic import Anthropic
from anthropic.types import TextBlock
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse
from tavily import TavilyClient

from mcp_server.exceptions import EmptyLLMResponseError, ExtractionError, UpstreamAPIError
from mcp_server.models import (
    Competitor,
    CompetitorsResponse,
    Cost,
    DigestItem,
    DigestResponse,
    RepurposedContent,
    SummaryResponse,
    ToolMeta,
)

load_dotenv()
log = logging.getLogger(__name__)

# ---- Models ----
CHEAP_MODEL = "claude-haiku-4-5-20251001"
WRITER_MODEL = "claude-sonnet-4-6"

# ---- Tunables ----
DEFAULT_BULLETS = 5
DEFAULT_DIGEST_RESULTS = 5
DEFAULT_COMPETITORS = 5
CONTENT_CHAR_LIMIT = 8000
FETCH_TIMEOUT_SEC = 15

# ---- Pricing (per million tokens, USD) ----
PRICING: dict[str, dict[str, float]] = {
    CHEAP_MODEL: {"input": 1.00, "output": 5.00},
    WRITER_MODEL: {"input": 3.00, "output": 15.00},
}


# ---- Lazy clients ----
_anthropic: Anthropic | None = None
_tavily: TavilyClient | None = None


def _get_anthropic() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic()
    return _anthropic


def _get_tavily() -> TavilyClient:
    global _tavily
    if _tavily is None:
        _tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily


# ---- Helpers ----
def _usd_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING.get(model, {"input": 0.0, "output": 0.0})
    return round(
        (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000, 6
    )


def _chat(model: str, prompt: str, max_tokens: int = 1024) -> tuple[str, Cost]:
    """Single-turn chat returning text + cost telemetry."""
    msg = _get_anthropic().messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = ""
    for block in msg.content:
        if isinstance(block, TextBlock):
            text = block.text
            break
    if not text:
        raise EmptyLLMResponseError(
            f"no text block in response (model={model}, stop_reason={msg.stop_reason!r})"
        )
    cost = Cost(
        model=model,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        usd=_usd_cost(model, msg.usage.input_tokens, msg.usage.output_tokens),
    )
    return text, cost


def _fetch_clean_text(url: str) -> str:
    """Fetch URL + trafilatura extract. Raises UpstreamAPIError on network failure, ExtractionError if empty."""
    try:
        response = httpx.get(url, timeout=FETCH_TIMEOUT_SEC, follow_redirects=True)
        response.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        raise UpstreamAPIError(f"fetch failed for {url}: {exc}") from exc
    extracted = trafilatura.extract(response.text)
    if not extracted or not extracted.strip():
        raise ExtractionError(f"no extractable text from {url}")
    return extracted[:CONTENT_CHAR_LIMIT]


# ---- MCP server ----
# Host/port are constructor args (not run-time args) for FastMCP — read from env
# so Fly.io can inject PORT and we can bind 0.0.0.0 in containers.
_HTTP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
_HTTP_PORT = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8765")))
mcp: FastMCP[Any] = FastMCP("mcp-automations", host=_HTTP_HOST, port=_HTTP_PORT)


@mcp.tool()
def summarize_url(url: str, n_bullets: int = DEFAULT_BULLETS) -> SummaryResponse:
    """Fetch a URL, extract clean article text, and return an N-bullet summary."""
    log.info("summarize_url: %s (n_bullets=%d)", url, n_bullets)
    text = _fetch_clean_text(url)
    summary, cost = _chat(
        CHEAP_MODEL,
        f"Summarize the following in {n_bullets} short bullets:\n\n{text}",
        max_tokens=500,
    )
    return SummaryResponse(url=url, n_bullets=n_bullets, summary=summary, cost=cost)


@mcp.tool()
def repurpose_content(text: str, format: str = "twitter_thread") -> RepurposedContent:
    """Repurpose long-form text into ``twitter_thread``, ``linkedin_post``, or ``newsletter``."""
    log.info("repurpose_content: format=%s len=%d", format, len(text))
    prompt = (
        f"Repurpose the content below into a high-quality {format.replace('_', ' ')}. "
        "Keep the facts. Trim filler. Match the platform's voice.\n\n"
        f"Content:\n{text[:CONTENT_CHAR_LIMIT]}"
    )
    content, cost = _chat(WRITER_MODEL, prompt, max_tokens=1200)
    return RepurposedContent(
        format=format,
        content=content,
        word_count=len(content.split()),
        cost=cost,
    )


@mcp.tool()
def daily_digest(topic: str, n_results: int = DEFAULT_DIGEST_RESULTS) -> DigestResponse:
    """Search the web for recent news on ``topic`` and return a digest with citations."""
    log.info("daily_digest: %s (n_results=%d)", topic, n_results)
    try:
        results = _get_tavily().search(topic, max_results=n_results, topic="news")
    except Exception as exc:  # tavily-python doesn't expose a typed exception hierarchy
        raise UpstreamAPIError(f"Tavily search failed: {exc}") from exc

    items = [
        DigestItem(
            title=r["title"],
            url=r["url"],
            snippet=(r.get("content") or "")[:300],
        )
        for r in results.get("results", [])
    ]
    bullets = "\n".join(f"- {it.title} ({it.url}): {it.snippet}" for it in items)
    summary, cost = _chat(
        CHEAP_MODEL,
        f"Topic: {topic}\n\nResults:\n{bullets}\n\nWrite a ~200-word digest citing each item by title.",
        max_tokens=600,
    )
    return DigestResponse(topic=topic, items=items, summary=summary, cost=cost)


@mcp.tool()
def find_competitors(domain: str, n: int = DEFAULT_COMPETITORS) -> CompetitorsResponse:
    """Identify ``n`` plausible competitors for a company at ``domain`` (e.g., 'stripe.com')."""
    log.info("find_competitors: %s (n=%d)", domain, n)
    try:
        search = _get_tavily().search(
            f"{domain} competitors alternatives comparison", max_results=8
        )
    except Exception as exc:
        raise UpstreamAPIError(f"Tavily search failed: {exc}") from exc

    context = "\n\n".join(
        f"[{i + 1}] {r['title']} ({r['url']}): {(r.get('content') or '')[:400]}"
        for i, r in enumerate(search.get("results", []))
    )
    prompt = (
        f"Given the search results below about {domain}, identify {n} real companies "
        f"that compete with {domain}. For each, give: name, official URL, and a one-sentence "
        "rationale grounded in the search results.\n\n"
        f'Return ONLY JSON: {{"competitors": [{{"name": str, "url": str, "rationale": str}}, ...]}}\n\n'
        f"Search results:\n{context}"
    )
    raw, cost = _chat(WRITER_MODEL, prompt, max_tokens=900)
    competitors = _parse_competitors_json(raw)
    return CompetitorsResponse(domain=domain, competitors=competitors[:n], cost=cost)


def _parse_competitors_json(raw: str) -> list[Competitor]:
    """Strict JSON parse with brace-slice fallback for chatty models."""
    for candidate in (raw, raw[raw.find("{") : raw.rfind("}") + 1] if "{" in raw else ""):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("competitors"), list):
            return [Competitor(**c) for c in parsed["competitors"] if isinstance(c, dict)]
    return []


# ---- Stand-out features: Resources + Prompts ----
@mcp.resource("automations://catalog")
def tool_catalog() -> str:
    """A JSON catalog of every tool this server exposes, with example invocations."""
    catalog: list[ToolMeta] = [
        ToolMeta(
            name="summarize_url",
            description="Fetch a URL and return an N-bullet summary.",
            example_invocation='summarize_url(url="https://example.com/article", n_bullets=5)',
        ),
        ToolMeta(
            name="repurpose_content",
            description="Turn long-form text into a twitter thread, linkedin post, or newsletter.",
            example_invocation='repurpose_content(text="...", format="twitter_thread")',
        ),
        ToolMeta(
            name="daily_digest",
            description="Recent news on a topic, returned as a digest with citations.",
            example_invocation='daily_digest(topic="AI agent frameworks", n_results=5)',
        ),
        ToolMeta(
            name="find_competitors",
            description="Identify N plausible competitors for a company by domain.",
            example_invocation='find_competitors(domain="stripe.com", n=5)',
        ),
    ]
    return json.dumps([t.model_dump() for t in catalog], indent=2)


@mcp.prompt()
def daily_brief(topic: str = "AI engineering jobs") -> str:
    """A composable prompt that chains daily_digest + repurpose_content into a morning email draft."""
    return (
        f"Use the `daily_digest` tool to get today's news on '{topic}'. "
        "Then use the `repurpose_content` tool to turn that digest into a newsletter-style "
        "email of ~200 words. Return the final email body, ready to send."
    )


# ---- Public landing page at GET / (for browser visitors; MCP protocol uses POST /mcp) ----
_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MCP Automations</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
           max-width: 720px; margin: 3em auto; padding: 0 1em; line-height: 1.55; color: #222; }
    h1 { margin-bottom: 0.2em; }
    .sub { color: #555; margin-top: 0; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
    pre { background: #f4f4f6; padding: 1em; border-radius: 6px; overflow-x: auto; }
    a { color: #1758b8; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .links { display: flex; gap: 1.2em; flex-wrap: wrap; margin: 1.5em 0; }
    .badge { background: #eef; padding: 0.3em 0.7em; border-radius: 4px; font-size: 0.9em; }
    footer { color: #888; font-size: 0.85em; margin-top: 3em; }
  </style>
</head>
<body>
  <h1>MCP Automations</h1>
  <p class="sub">A Model Context Protocol server with 4 typed tools, deployed on Fly.io.</p>

  <div class="links">
    <a href="https://github.com/wzltmp/mcp-automations">GitHub</a>
    <a href="https://registry.modelcontextprotocol.io/v0/servers?search=mcp-automations">Official MCP Registry</a>
    <a href="https://mcp-automations-5vgea2ynuyrvbzkcxm6yoh.streamlit.app/">Browser playground</a>
  </div>

  <p>This URL is an <strong>MCP endpoint, not a webpage.</strong> MCP clients connect via JSON-RPC over POST to <code>/mcp</code>. Try it:</p>

  <pre>curl -X POST https://mcp-automations.fly.dev/mcp \\
  -H 'Content-Type: application/json' \\
  -H 'Accept: application/json, text/event-stream' \\
  -d '{"jsonrpc":"2.0","method":"initialize","id":1,
       "params":{"protocolVersion":"2024-11-05",
                 "capabilities":{},
                 "clientInfo":{"name":"curl","version":"1"}}}'</pre>

  <p>Or add it to your Claude Desktop config (<code>~/Library/Application Support/Claude/claude_desktop_config.json</code>):</p>

  <pre>{
  "mcpServers": {
    "mcp-automations": {
      "url": "https://mcp-automations.fly.dev/mcp",
      "transport": "http"
    }
  }
}</pre>

  <p>Tools exposed: <span class="badge">summarize_url</span> <span class="badge">repurpose_content</span> <span class="badge">daily_digest</span> <span class="badge">find_competitors</span></p>

  <p>For a non-technical walk-through, try the <a href="https://mcp-automations-5vgea2ynuyrvbzkcxm6yoh.streamlit.app/">browser playground</a> — same tools, no setup.</p>

  <footer>Listed on the Official MCP Registry as <code>io.github.wzltmp/mcp-automations</code>.</footer>
</body>
</html>
"""


@mcp.custom_route("/", methods=["GET"])
async def landing(_request: Request) -> HTMLResponse:
    """Browser-friendly landing page so non-MCP clients hitting GET / see something useful."""
    return HTMLResponse(_LANDING_HTML)


def main() -> None:
    """Run the server. Set ``MCP_TRANSPORT=http`` (and optionally ``MCP_HOST``/``PORT``) for remote mode."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        log.info("starting MCP server on http://%s:%d (streamable-http)", _HTTP_HOST, _HTTP_PORT)
        mcp.run(transport="streamable-http")
    else:
        log.info("starting MCP server on stdio")
        mcp.run()


if __name__ == "__main__":
    main()
