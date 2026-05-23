"""Custom exception types for the MCP server tools.

Named exceptions make tool errors self-documenting in logs and let MCP clients
distinguish a "fetch failed" from an "LLM refused" from a "bad input."
"""
from __future__ import annotations


class MCPToolError(Exception):
    """Base for any tool-side error worth surfacing to the MCP client."""


class UpstreamAPIError(MCPToolError):
    """An external API call (Tavily, Anthropic, an HTTP fetch) failed."""


class EmptyLLMResponseError(MCPToolError):
    """The LLM returned a message with no usable text content (refusal or empty)."""


class ExtractionError(MCPToolError):
    """trafilatura could not extract meaningful text from a fetched page."""
