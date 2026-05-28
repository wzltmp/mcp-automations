"""Pure-function tests for cost arithmetic and the lenient competitor JSON parser."""
from __future__ import annotations

import pytest

from mcp_server import server


class TestUsdCost:
    """Per-model token-pricing math, table-driven against PRICING."""

    @pytest.mark.parametrize(
        ("model", "in_tok", "out_tok", "expected"),
        [
            (server.CHEAP_MODEL, 1_000_000, 0, 1.0),
            (server.CHEAP_MODEL, 0, 1_000_000, 5.0),
            (server.CHEAP_MODEL, 1940, 147, round((1940 * 1.0 + 147 * 5.0) / 1_000_000, 6)),
            (server.WRITER_MODEL, 1_000_000, 0, 3.0),
            (server.WRITER_MODEL, 0, 1_000_000, 15.0),
            (server.WRITER_MODEL, 500, 200, round((500 * 3.0 + 200 * 15.0) / 1_000_000, 6)),
        ],
    )
    def test_known_models(self, model: str, in_tok: int, out_tok: int, expected: float) -> None:
        assert server._usd_cost(model, in_tok, out_tok) == expected

    def test_unknown_model_returns_zero(self) -> None:
        assert server._usd_cost("not-a-real-model", 1000, 1000) == 0.0


class TestParseCompetitorsJson:
    """The parser must handle Sonnet's varied JSON-emission styles without crashing."""

    def test_strict_json(self) -> None:
        raw = '{"competitors": [{"name": "A", "url": "https://a.com", "rationale": "alt"}]}'
        result = server._parse_competitors_json(raw)
        assert len(result) == 1
        assert result[0].name == "A"

    def test_json_wrapped_in_prose(self) -> None:
        raw = (
            'Here are the competitors:\n'
            '{"competitors": [{"name": "B", "url": "https://b.com", "rationale": "rival"}]}\n'
            'Hope this helps.'
        )
        result = server._parse_competitors_json(raw)
        assert len(result) == 1
        assert result[0].name == "B"

    def test_malformed_json_returns_empty(self) -> None:
        assert server._parse_competitors_json("not json at all { ] }") == []

    def test_missing_competitors_key_returns_empty(self) -> None:
        raw = '{"something_else": [1, 2]}'
        assert server._parse_competitors_json(raw) == []

    def test_non_dict_items_filtered_out(self) -> None:
        raw = (
            '{"competitors": ['
            '{"name": "Real", "url": "https://r.com", "rationale": "ok"}, '
            '"stray string", '
            '42'
            ']}'
        )
        result = server._parse_competitors_json(raw)
        assert len(result) == 1
        assert result[0].name == "Real"

    def test_empty_string_returns_empty(self) -> None:
        assert server._parse_competitors_json("") == []
