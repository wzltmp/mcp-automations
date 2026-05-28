"""Streamlit playground for the MCP server's tools.

Exercises each tool the server exposes (``summarize_url``, ``repurpose_content``,
``daily_digest``, ``find_competitors``) by calling the underlying functions
directly — no MCP transport in between. The MCP layer is just a wrapper around
the same Python callables, so this is a faithful preview of what a Claude client
would see when invoking the tools.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Streamlit Cloud runs the entrypoint as a script, not a package, so `from mcp_server.X`
# fails without this shim. Pattern carried over from project 02.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from dotenv import load_dotenv

from mcp_server.exceptions import MCPToolError
from mcp_server.models import Cost
from mcp_server.server import (
    daily_digest,
    find_competitors,
    repurpose_content,
    summarize_url,
)

load_dotenv()

st.set_page_config(page_title="MCP Automations Playground", page_icon=":electric_plug:", layout="wide")

GITHUB_URL = "https://github.com/wzltmp/mcp-automations"

# Per-session abuse caps. The Anthropic console monthly cap is the real backstop;
# these just deter casual abuse and make the limits visible to honest users.
MAX_CALLS_PER_SESSION = 20
MAX_SPEND_PER_SESSION_USD = 0.50


def _money(usd: float) -> str:
    """Escape ``$`` so Streamlit's KaTeX renderer doesn't turn it into inline math."""
    return f"\\${usd:.6f}"


def _track_cost(cost: Cost) -> None:
    """Append this call's cost to the session running total."""
    session = st.session_state.setdefault("costs", [])
    session.append(cost)


def _guard() -> bool:
    """Return True if the call may proceed; otherwise render an error and return False."""
    costs: list[Cost] = st.session_state.get("costs", [])
    if len(costs) >= MAX_CALLS_PER_SESSION:
        st.error(
            f"Session limit reached ({MAX_CALLS_PER_SESSION} calls). "
            "Refresh the page to start a new session."
        )
        return False
    if sum(c.usd for c in costs) >= MAX_SPEND_PER_SESSION_USD:
        st.error(
            f"Session spend cap reached ({_money(MAX_SPEND_PER_SESSION_USD)}). "
            "Refresh the page to start a new session."
        )
        return False
    return True


def _render_cost(cost: Cost) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Model", cost.model.split("-claude-")[-1] if "claude-" in cost.model else cost.model)
    col2.metric("Input tokens", cost.input_tokens)
    col3.metric("Output tokens", cost.output_tokens)
    col4.metric("Cost", _money(cost.usd))


# ---- Sidebar ----
with st.sidebar:
    st.markdown("### About")
    st.markdown(
        "A live playground for the **MCP Automations server** — 4 tools an LLM can call "
        "to summarize a URL, repurpose long-form content, fetch a news digest, "
        "or identify a company's competitors."
    )
    st.markdown(f"[Source on GitHub]({GITHUB_URL})")
    st.markdown("---")
    st.markdown("### What is MCP?")
    st.markdown(
        "**Model Context Protocol** is the emerging standard for exposing tools to LLMs. "
        "This same server can be plugged into Claude Desktop (stdio transport) or "
        "deployed publicly (streamable-HTTP) — and any MCP-aware client gets the "
        "same typed tool catalog."
    )
    st.markdown("---")
    st.markdown("### Stack")
    st.markdown(
        "- FastMCP server (Python `mcp` SDK)\n"
        "- Claude Sonnet 4.6 (writer) + Haiku 4.5 (cheap calls)\n"
        "- Tavily Search + trafilatura for clean text\n"
        "- Pydantic models on every tool output\n"
        "- Streamlit Cloud (this playground)"
    )
    st.markdown("---")
    st.markdown("### Session cost")
    costs: list[Cost] = st.session_state.get("costs", [])
    total = sum(c.usd for c in costs)
    st.metric("Total spend", _money(total), help=f"{len(costs)} call(s) this session")
    col_calls, col_budget = st.columns(2)
    col_calls.metric("Calls left", max(0, MAX_CALLS_PER_SESSION - len(costs)))
    col_budget.metric("Budget left", _money(max(0.0, MAX_SPEND_PER_SESSION_USD - total)))
    st.caption(
        f"Per-session caps: {MAX_CALLS_PER_SESSION} calls / "
        f"{_money(MAX_SPEND_PER_SESSION_USD)}. Refresh for a new session."
    )
    if costs and st.button("Reset session cost"):
        st.session_state["costs"] = []
        st.rerun()

# ---- Main ----
st.title(":electric_plug: MCP Automations Playground")
st.caption(
    "Each tab calls one tool from the MCP server. Outputs are the same typed "
    "Pydantic responses an LLM client would receive."
)

tab_summary, tab_repurpose, tab_digest, tab_competitors = st.tabs(
    [":page_facing_up: summarize_url", ":recycle: repurpose_content", ":newspaper: daily_digest", ":dart: find_competitors"]
)

# ---- summarize_url ----
with tab_summary:
    st.markdown("Fetch a URL, extract the article text with trafilatura, and ask Haiku for an N-bullet summary.")
    with st.form("summary_form"):
        url = st.text_input("URL", placeholder="https://www.paulgraham.com/greatwork.html")
        n_bullets = st.slider("Bullets", 3, 10, 5)
        submitted = st.form_submit_button("Summarize", type="primary")
    if submitted and url and _guard():
        try:
            with st.spinner("Fetching + summarizing..."):
                result = summarize_url(url, n_bullets=n_bullets)
        except MCPToolError as exc:
            st.error(f"{type(exc).__name__}: {exc}")
        else:
            st.markdown("#### Summary")
            st.markdown(result.summary)
            _render_cost(result.cost)
            _track_cost(result.cost)

# ---- repurpose_content ----
with tab_repurpose:
    st.markdown("Repurpose long-form text into a social/email format. Uses Sonnet for higher writing quality.")
    with st.form("repurpose_form"):
        text = st.text_area(
            "Source text",
            height=200,
            placeholder="Paste a blog post, transcript, or memo...",
        )
        fmt = st.selectbox("Format", ["twitter_thread", "linkedin_post", "newsletter"])
        submitted = st.form_submit_button("Repurpose", type="primary")
    if submitted and text.strip() and _guard():
        try:
            with st.spinner("Rewriting..."):
                result = repurpose_content(text=text, format=fmt)
        except MCPToolError as exc:
            st.error(f"{type(exc).__name__}: {exc}")
        else:
            st.markdown(f"#### {fmt.replace('_', ' ').title()}  ·  {result.word_count} words")
            st.markdown(result.content)
            _render_cost(result.cost)
            _track_cost(result.cost)

# ---- daily_digest ----
with tab_digest:
    st.markdown("Tavily news search for the topic, then a ~200-word digest with citations.")
    with st.form("digest_form"):
        topic = st.text_input("Topic", placeholder="e.g., AI agent frameworks")
        n_results = st.slider("Sources", 3, 10, 5)
        submitted = st.form_submit_button("Build digest", type="primary")
    if submitted and topic.strip() and _guard():
        try:
            with st.spinner("Searching + summarizing..."):
                result = daily_digest(topic=topic, n_results=n_results)
        except MCPToolError as exc:
            st.error(f"{type(exc).__name__}: {exc}")
        else:
            st.markdown("#### Digest")
            st.markdown(result.summary)
            with st.expander(f"Sources ({len(result.items)})"):
                for i, item in enumerate(result.items, 1):
                    st.markdown(f"**[{i}]** [{item.title}]({item.url})")
                    st.caption(item.snippet)
            _render_cost(result.cost)
            _track_cost(result.cost)

# ---- find_competitors ----
with tab_competitors:
    st.markdown("Search the web, then ask Sonnet to nominate N plausible competitors with one-line rationales.")
    with st.form("competitors_form"):
        domain = st.text_input("Company domain", placeholder="stripe.com")
        n = st.slider("How many competitors", 3, 10, 5)
        submitted = st.form_submit_button("Find competitors", type="primary")
    if submitted and domain.strip() and _guard():
        try:
            with st.spinner("Researching..."):
                result = find_competitors(domain=domain, n=n)
        except MCPToolError as exc:
            st.error(f"{type(exc).__name__}: {exc}")
        else:
            if not result.competitors:
                st.warning("Model returned no parseable competitors — try a more recognizable domain.")
            else:
                st.markdown(f"#### Competitors of {result.domain}")
                for c in result.competitors:
                    st.markdown(f"**[{c.name}]({c.url})** — {c.rationale}")
            _render_cost(result.cost)
            _track_cost(result.cost)
