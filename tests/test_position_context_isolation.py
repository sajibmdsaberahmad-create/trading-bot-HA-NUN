"""Multi-position monitor must not bleed risk plans across tickers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.risk import RiskManager, TradePlan


class _StubRunner:
  """Minimal surface for context bind/load helpers copied from ScalperRunner."""

  def __init__(self):
    self._position_slots = {}
    self._risk_plans = {}
    self.risk = RiskManager(MagicMock())
    self.current_ticker = ""
    self.shares = 0.0
    self._entry_price = 0.0
    self._position_stop = 0.0
    self._position_target = 0.0
    self._position_peak = 0.0
    self._hard_stop_floor = 0.0
    self._position_opened_at = 0.0
    self._prev_shares = 0.0
    self._last_pulse_price = 0.0
    self._last_price_change_at = 0.0
    self._last_price_snapshot_at = 0.0
    self._last_pulse_fingerprint = ""
    self._last_position_pulse = 0.0
    self._last_ai_position_manage = 0.0
    self._last_stagnation_decision = {}
    self.bracket_handle = None
    self._bracket_by_ticker = {}

  # pull methods from scalper_runner module
  from core.scalper_runner import ScalperRunner as _SR

  _slot_entry_price = _SR._slot_entry_price
  _bind_risk_plan_for_ticker = _SR._bind_risk_plan_for_ticker
  _save_position_context = _SR._save_position_context
  _load_position_context = _SR._load_position_context

  def _repair_slot_entry_price(self, ticker: str) -> None:
    pass

  def _refresh_aggregate_position_state(self) -> None:
    pass


def test_slot_entry_price_prefers_fill():
  assert _StubRunner._slot_entry_price({"entry_price": 7.93, "entry_fill_px": 7.95}) == 7.95
  assert _StubRunner._slot_entry_price({"entry_price": 7.93}) == 7.93


def test_load_aal_does_not_keep_bito_risk_plan():
  runner = _StubRunner()
  bito_plan = TradePlan(
    side="LONG", entry_price=7.95, shares=58,
    initial_stop_price=7.93, take_profit_price=8.02,
    risk_usd=1.16, atr_at_entry=0.05,
  )
  runner._risk_plans["BITO"] = bito_plan
  runner.risk.open_position(bito_plan)
  runner._position_slots["BITO"] = {
    "shares": 58, "entry_price": 7.95, "entry_fill_px": 7.95,
    "stop": 7.93, "target": 8.02, "peak": 7.95, "hard_floor": 7.93,
    "opened_at": 1.0, "prev_shares": 58,
  }
  runner._position_slots["AAL"] = {
    "shares": 12, "entry_price": 18.0, "entry_fill_px": 18.0,
    "stop": 17.5, "target": 19.0, "peak": 18.0, "hard_floor": 17.5,
    "opened_at": 2.0, "prev_shares": 12,
  }
  runner._load_position_context("AAL")
  assert runner.risk.plan is not None
  assert runner.risk.plan.entry_price == pytest.approx(18.0)
  assert runner.risk.plan.entry_price != bito_plan.entry_price


def test_save_context_rejects_wrong_ticker():
  runner = _StubRunner()
  runner._position_slots["BITO"] = {"shares": 58, "entry_price": 7.95}
  runner.current_ticker = "AAL"
  runner._entry_price = 18.0
  runner._save_position_context("BITO")
  assert runner._position_slots["BITO"]["entry_price"] == 7.95
