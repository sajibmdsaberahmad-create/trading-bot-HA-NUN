"""Multi-position monitor must not bleed risk plans across tickers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.position_context import (
    bind_risk_plan_for_ticker,
    risk_plan_sane_for_tick,
    slot_entry_price,
    slot_price_sane,
)
from core.risk import RiskManager, TradePlan


def test_slot_entry_price_prefers_fill():
    assert slot_entry_price({"entry_price": 7.93, "entry_fill_px": 7.95}) == 7.95
    assert slot_entry_price({"entry_price": 7.93}) == 7.93


def test_load_aal_does_not_keep_bito_risk_plan():
    risk = RiskManager(MagicMock(), initial_equity=10000.0)
    risk_plans = {}
    slots = {}
    bito_plan = TradePlan(
        side="LONG", entry_price=7.95, shares=58,
        initial_stop_price=7.93, take_profit_price=8.02,
        risk_usd=1.16, atr_at_entry=0.05,
    )
    risk_plans["BITO"] = bito_plan
    risk.open_position(bito_plan)
    slots["BITO"] = {
        "shares": 58, "entry_price": 7.95, "entry_fill_px": 7.95,
        "stop": 7.93, "target": 8.02, "peak": 7.95, "hard_floor": 7.93,
    }
    slots["AAL"] = {
        "shares": 12, "entry_price": 18.0, "entry_fill_px": 18.0,
        "stop": 17.5, "target": 19.0, "peak": 18.0, "hard_floor": 17.5,
    }
    bind_risk_plan_for_ticker(
        "AAL", position_slots=slots, risk_plans=risk_plans, risk=risk,
    )
    assert risk.plan is not None
    assert risk.plan.entry_price == pytest.approx(18.0)
    assert risk.plan.entry_price != bito_plan.entry_price


def test_slot_price_sane_rejects_cross_ticker_quote():
    assert slot_price_sane(3.22, 3.24)
    assert not slot_price_sane(3.22, 7.96)
    assert slot_price_sane(7.95, 7.96)


def test_risk_plan_sane_rejects_cross_ticker_price():
    plan = TradePlan(
        side="LONG", entry_price=7.95, shares=58,
        initial_stop_price=7.93, take_profit_price=8.02,
        risk_usd=1.16, atr_at_entry=0.05,
    )
    assert risk_plan_sane_for_tick(plan, entry_price=7.95, shares=58, current_px=7.94)
    assert not risk_plan_sane_for_tick(plan, entry_price=7.95, shares=58, current_px=18.09)
