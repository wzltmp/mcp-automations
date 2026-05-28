"""Offline unit tests for the four MCP tools — no Anthropic, Tavily, or HTTP calls."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_server import server
from mcp_server.exceptions import (
    EmptyLLMResponseError,
    ExtractionError,
    UpstreamAPIError,
)
from mcp_server.models import Cost


def _fake_cost(model: str = server.CHEAP_MODEL) -> Cost:
    return Cost(model=model, input_tokens=100, output_tokens=50, usd=0.000350)


# ---------- summarize_url ----------
class TestSummarizeUrl:
    def test_happy_path(self) -> None:
        with (
            patch.object(server, "_fetch_clean_text", return_value="article body here"),
            patch.object(server, "_chat", return_value=("• bullet 1\n• bullet 2", _fake_cost())),
        ):
            result = server.summarize_url("https://example.com/post", n_bullets=2)
        assert result.url == "https://example.com/post"
        assert result.n_bullets == 2
        assert "bullet" in result.summary
        assert result.cost.model == server.CHEAP_MODEL

    def test_fetch_failure_raises_upstream(self) -> None:
        with patch.object(
            server,
            "_fetch_clean_text",
            side_effect=UpstreamAPIError("fetch failed for x: boom"),
        ), pytest.raises(UpstreamAPIError, match="fetch failed"):
            server.summarize_url("https://example.com/post")

    def test_extraction_failure_propagates(self) -> None:
        with patch.object(
            server,
            "_fetch_clean_text",
            side_effect=ExtractionError("no extractable text"),
        ), pytest.raises(ExtractionError):
            server.summarize_url("https://example.com/empty")


# ---------- repurpose_content ----------
class TestRepurposeContent:
    def test_happy_path_routes_to_writer_model(self) -> None:
        with patch.object(
            server,
            "_chat",
            return_value=("Tweet 1.\nTweet 2.\nTweet 3.", _fake_cost(server.WRITER_MODEL)),
        ) as mock_chat:
            result = server.repurpose_content(text="long blog post body", format="twitter_thread")
        assert result.format == "twitter_thread"
        assert result.word_count > 0
        assert result.cost.model == server.WRITER_MODEL
        # writer model is used, not the cheap one
        assert mock_chat.call_args.args[0] == server.WRITER_MODEL

    def test_empty_response_raises(self) -> None:
        with patch.object(
            server,
            "_chat",
            side_effect=EmptyLLMResponseError("no text block (model=x, stop=end_turn)"),
        ), pytest.raises(EmptyLLMResponseError):
            server.repurpose_content(text="src", format="linkedin_post")


# ---------- daily_digest ----------
class TestDailyDigest:
    def test_happy_path(self) -> None:
        fake_tavily = MagicMock()
        fake_tavily.search.return_value = {
            "results": [
                {"title": "Headline A", "url": "https://a.com", "content": "snippet A"},
                {"title": "Headline B", "url": "https://b.com", "content": "snippet B"},
            ]
        }
        with (
            patch.object(server, "_get_tavily", return_value=fake_tavily),
            patch.object(server, "_chat", return_value=("digest body", _fake_cost())),
        ):
            result = server.daily_digest(topic="AI agents", n_results=2)
        assert result.topic == "AI agents"
        assert len(result.items) == 2
        assert result.items[0].title == "Headline A"
        assert result.summary == "digest body"

    def test_tavily_failure_raises_upstream(self) -> None:
        fake_tavily = MagicMock()
        fake_tavily.search.side_effect = RuntimeError("tavily 500")
        with patch.object(server, "_get_tavily", return_value=fake_tavily), pytest.raises(
            UpstreamAPIError, match="Tavily search failed"
        ):
            server.daily_digest(topic="x")


# ---------- find_competitors ----------
class TestFindCompetitors:
    def test_happy_path_truncates_to_n(self) -> None:
        fake_tavily = MagicMock()
        fake_tavily.search.return_value = {
            "results": [{"title": f"R{i}", "url": f"https://{i}.com", "content": "ctx"} for i in range(5)]
        }
        raw_json = (
            '{"competitors": ['
            + ", ".join(
                f'{{"name": "C{i}", "url": "https://c{i}.com", "rationale": "r"}}'
                for i in range(5)
            )
            + "]}"
        )
        with (
            patch.object(server, "_get_tavily", return_value=fake_tavily),
            patch.object(server, "_chat", return_value=(raw_json, _fake_cost(server.WRITER_MODEL))),
        ):
            result = server.find_competitors(domain="example.com", n=3)
        assert result.domain == "example.com"
        # n=3 must truncate even when model returns 5
        assert len(result.competitors) == 3
        assert result.competitors[0].name == "C0"

    def test_unparseable_json_returns_empty_list(self) -> None:
        fake_tavily = MagicMock()
        fake_tavily.search.return_value = {"results": []}
        with (
            patch.object(server, "_get_tavily", return_value=fake_tavily),
            patch.object(
                server, "_chat", return_value=("totally not json", _fake_cost(server.WRITER_MODEL))
            ),
        ):
            result = server.find_competitors(domain="x.com", n=3)
        # tool should not crash; competitors list is just empty
        assert result.competitors == []
        assert result.cost.model == server.WRITER_MODEL

    def test_tavily_failure_raises_upstream(self) -> None:
        fake_tavily = MagicMock()
        fake_tavily.search.side_effect = RuntimeError("tavily down")
        with patch.object(server, "_get_tavily", return_value=fake_tavily), pytest.raises(
            UpstreamAPIError, match="Tavily search failed"
        ):
            server.find_competitors(domain="x.com")
