"""Pydantic models for tool inputs and outputs.

Structured outputs save the downstream LLM from re-parsing free-form strings —
the client sees a typed schema and can route fields directly.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Cost(BaseModel):
    """Per-call cost telemetry attached to every tool response."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0


class SummaryResponse(BaseModel):
    url: str
    n_bullets: int
    summary: str
    cost: Cost


class RepurposedContent(BaseModel):
    format: str = Field(description="twitter_thread, linkedin_post, or newsletter")
    content: str
    word_count: int
    cost: Cost


class DigestItem(BaseModel):
    title: str
    url: str
    snippet: str


class DigestResponse(BaseModel):
    topic: str
    items: list[DigestItem]
    summary: str
    cost: Cost


class Competitor(BaseModel):
    name: str
    url: str
    rationale: str = Field(description="Why this is plausibly a competitor")


class CompetitorsResponse(BaseModel):
    domain: str
    competitors: list[Competitor]
    cost: Cost


class ToolMeta(BaseModel):
    """Metadata exposed via the automations://catalog resource."""

    name: str
    description: str
    example_invocation: str
