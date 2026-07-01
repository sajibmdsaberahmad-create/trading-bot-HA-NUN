"""IB avgCost normalization and per-ticker price sanity."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.fill_tracker import normalize_ib_avg_cost, position_entry_price
from core.position_context import slot_price_sane


def test_normalize_ib_avg_cost_fixes_10x_drift():
    assert normalize_ib_avg_cost(140.83, market_px=14.08) == pytest.approx(14.083)


def test_normalize_ib_avg_cost_keeps_matching_avg():
    assert normalize_ib_avg_cost(14.05, market_px=14.08) == 14.05


def test_normalize_ib_avg_cost_uses_market_when_irreconcilable():
    assert normalize_ib_avg_cost(500.0, market_px=14.0) == 14.0


def test_position_entry_price_normalizes_with_market():
    ib = MagicMock()
    pos = MagicMock()
    pos.contract.symbol = "INTC"
    pos.contract.multiplier = 1
    pos.avgCost = 140.83
    ib.positions.return_value = [pos]
    px = position_entry_price(ib, "INTC", market_px=14.08)
    assert px == pytest.approx(14.083)


def test_slot_price_sane_rejects_bito_price_on_soxs_entry():
    assert not slot_price_sane(3.22, 20.69)
    assert slot_price_sane(20.69, 20.74)


def test_sanitize_quote_price_fixes_10x_live_quote():
    from core.fill_tracker import sanitize_quote_price
    fixed = sanitize_quote_price(140.21, ref_px=14.02, pred_px=14.05, symbol="INTC")
    assert fixed == pytest.approx(14.021, rel=0.02)


def test_snapshot_market_price_uses_ib_truth_without_qualify():
    from unittest.mock import MagicMock, patch

    from core.fill_tracker import snapshot_market_price
    from core.ib_truth import IBAccountSnapshot, IBPosition, IBTruthSnapshot

    ib = MagicMock()
    ib.isConnected.return_value = True
    snap = IBTruthSnapshot(
        account=IBAccountSnapshot(),
        positions=[IBPosition(symbol="INTC", qty=10, avg_cost=14.0, market_price=14.52)],
        refreshed_at=1.0,
    )
    with patch("core.ib_truth.get_snapshot", return_value=snap), patch(
        "core.ib_truth.ib_truth_enabled", return_value=True
    ):
        px = snapshot_market_price(ib, "INTC")
    assert px == pytest.approx(14.52)
    ib.qualifyContracts.assert_not_called()


def test_snapshot_market_price_skips_ib_when_async_loop_running():
    from unittest.mock import MagicMock, patch
    import asyncio

    from core.fill_tracker import snapshot_market_price

    ib = MagicMock()
    ib.isConnected.return_value = True

    async def _run():
        with patch("core.fill_tracker._cached_market_price", return_value=0.0):
            px = snapshot_market_price(ib, "INTC")
        assert px == 0.0
        ib.qualifyContracts.assert_not_called()

    asyncio.run(_run())
