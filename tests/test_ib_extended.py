"""IB extended services — fundamentals, news, WSH, PnL, what-if."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.ib_extended import (
    _parse_fundamental_xml,
    extended_ai_context,
    ib_extended_enabled,
    what_if_margin_allows,
)


def test_parse_fundamental_xml():
    xml = "<ReportSnapshot><PE>12.5</PE><MarketCap>1000000</MarketCap></ReportSnapshot>"
    out = _parse_fundamental_xml(xml)
    assert out.get("PE") == 12.5
    assert out.get("MarketCap") == 1000000.0


def test_extended_ai_context_empty():
    with patch("core.ib_extended.get_extended_cache", return_value={}):
        ctx = extended_ai_context()
    assert ctx.get("ib_extended") is False


def test_ib_extended_enabled_default():
    with patch.dict("os.environ", {"IB_EXTENDED_ENABLED": "true"}):
        assert ib_extended_enabled() is True


def test_what_if_skipped_when_gate_off():
    ib = MagicMock()
    conn = MagicMock()
    with patch.dict("os.environ", {"IB_WHATIF_MARGIN_GATE": "false"}):
        ok, prev = what_if_margin_allows(ib, conn, "SPY", 10)
    assert ok is True
    assert prev.get("skipped") is True
