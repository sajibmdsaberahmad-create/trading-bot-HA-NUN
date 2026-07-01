#!/usr/bin/env python3
"""Mock-IB round-trip: entry limit → fill reconcile → flatten exit."""

from __future__ import annotations

import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.entry_pipeline import flatten_order_for_session
from core.fill_reconciler import PendingClose, finalize_flat_position_close


class _FakeBroker:
    def _round_price(self, price: float) -> float:
        return round(price, 4) if price < 1.0 else round(price, 2)


class TestRoundTripIb(unittest.TestCase):
    @patch("core.entry_pipeline.should_defer_bracket_children", return_value=False)
    @patch("core.entry_pipeline.should_use_extended_hours_orders", return_value=False)
    def test_entry_then_flatten_exit_round_trip(self, _ext, _defer):
        limit_inst = SimpleNamespace(action="SELL", lmtPrice=10.0)
        fake_mod = SimpleNamespace(
            LimitOrder=lambda *a, **k: limit_inst,
            MarketOrder=lambda *a, **k: SimpleNamespace(action="SELL"),
        )
        cfg = SimpleNamespace(
            PENNY_PRICE_THRESHOLD=1.0,
            MAX_MARKET_ENTRY_SHARES=400,
            IB_REGULATORY_LIMIT_PCT=0.01,
            EXIT_LIMIT_BUFFER_PCT=0.004,
            ENTRY_LIMIT_BUFFER_PCT=0.004,
            MAX_ACCEPTABLE_SLIPPAGE_PCT=0.004,
        )
        with patch.dict(sys.modules, {"core.ib_client": fake_mod}):
            order, mode = flatten_order_for_session(
                cfg, _FakeBroker(), 500, 20.0, 19.98, 20.02,
            )
        self.assertEqual(order.action, "SELL")
        self.assertIn("limit", mode)

    @patch("core.fill_tracker.ib_position_shares", return_value=0.0)
    @patch("core.fill_reconciler.resolve_exit_from_ib", return_value=(10.5, True))
    @patch("core.fill_reconciler.resolve_entry_from_ib", return_value=(10.0, True))
    def test_exit_finalize_after_ib_flat(self, _entry, _exit, _pos):
        pending = PendingClose(
            ticker="TEST",
            reason="take_profit",
            quote_exit_px=10.48,
            slot={"entry_price": 10.0, "entry_fill_px": 10.0, "shares": 100},
            shares=100.0,
            opened_at=time.time() - 60,
            ib_baseline_shares=100.0,
        )
        rec = finalize_flat_position_close(
            pending, MagicMock(), None, cfg=SimpleNamespace(),
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec["exit_fill"], 10.5)
        self.assertAlmostEqual(rec["pnl_usd"], 50.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
