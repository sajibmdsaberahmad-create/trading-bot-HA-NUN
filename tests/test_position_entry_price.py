"""IB avgCost normalization and per-ticker price sanity."""
from __future__ import annotations

from unittest.mock import MagicMock

from core.fill_tracker import normalize_ib_avg_cost, position_entry_price
from core.position_context import slot_price_sane


def test_normalize_ib_avg_cost_fixes_10x_drift():
    assert normalize_ib_avg_cost(140.83, market_px=14.08) == 14.083


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
    assert px == 14.083


def test_slot_price_sane_rejects_bito_price_on_soxs_entry():
    assert not slot_price_sane(3.22, 20.69)
    assert slot_price_sane(20.69, 20.74)
