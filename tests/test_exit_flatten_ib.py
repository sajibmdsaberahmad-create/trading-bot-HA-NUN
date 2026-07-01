#!/usr/bin/env python3
"""Exit flatten must not clear local state before IB confirms."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.entry_pipeline import flatten_exit_limit_px, flatten_order_for_session
from core.fill_tracker import confirm_exit_fill, trade_order_status


class _FakeBroker:
  def _round_price(self, price: float) -> float:
      return round(price, 4) if price < 1.0 else round(price, 2)


class TestFlattenExitLimit(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(
            PENNY_PRICE_THRESHOLD=1.0,
            IB_REGULATORY_LIMIT_PCT=0.01,
            PENNY_LIMIT_BUFFER_PCT=0.006,
            EXIT_LIMIT_BUFFER_PCT=0.004,
            ENTRY_LIMIT_BUFFER_PCT=0.004,
            MAX_ACCEPTABLE_SLIPPAGE_PCT=0.004,
            EXTENDED_HOURS_DEFER_BRACKET=True,
            PAPER_TRADING=True,
            PAPER_MARKET_ENTRIES=True,
        )
        self.broker = _FakeBroker()

    def test_ext_hours_limit_below_bid(self):
        px, mode = flatten_exit_limit_px(
            self.cfg, self.broker, bid=137.67, ask=137.71, ref_px=137.69, shares=2,
        )
        self.assertLess(px, 137.67)
        self.assertIn("sell", mode)

    def test_penny_wide_spread_uses_bid(self):
        px, mode = flatten_exit_limit_px(
            self.cfg, self.broker, bid=0.56, ask=0.58, ref_px=0.5625, shares=524,
        )
        self.assertEqual(mode, "limit_wide_spread_sell")
        self.assertGreaterEqual(px, 0.55)

    @patch("core.entry_pipeline.should_defer_bracket_children", return_value=True)
    def test_flatten_order_uses_limit_outside_rth(self, _defer):
        import sys
        limit_inst = SimpleNamespace(action="SELL", lmtPrice=137.0)
        fake_mod = SimpleNamespace(
            LimitOrder=lambda *a, **k: limit_inst,
            MarketOrder=lambda *a, **k: SimpleNamespace(action="SELL"),
        )
        with patch.dict(sys.modules, {"core.ib_client": fake_mod}):
            order, mode = flatten_order_for_session(
                self.cfg, self.broker, 2, 137.67, 137.66, 137.68,
            )
        self.assertEqual(order.action, "SELL")
        self.assertGreater(order.lmtPrice, 0)
        self.assertIn("limit", mode)


class _FakeStatus:
    def __init__(self, status, filled=0.0, avg=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avg


class TestTradeOrderStatus(unittest.TestCase):
    def test_missing_trade(self):
        self.assertEqual(trade_order_status(None)["status"], "missing")

    def test_reads_status(self):
        tr = SimpleNamespace(
            order=SimpleNamespace(orderId=42),
            orderStatus=_FakeStatus("Submitted", 0, 0),
        )
        row = trade_order_status(tr)
        self.assertEqual(row["status"], "Submitted")
        self.assertEqual(row["order_id"], 42)


class TestConfirmExitFill(unittest.TestCase):
    @patch("core.fill_tracker.ib_position_shares", return_value=347.0)
    @patch("core.fill_tracker.poll_trade_fill", return_value=(0.0, 0.0))
    def test_rejected_order(self, _poll, _pos):
        tr = SimpleNamespace(
            order=SimpleNamespace(orderId=1),
            orderStatus=_FakeStatus("Inactive"),
        )
        qty, px, ok, src = confirm_exit_fill(
            MagicMock(),
            symbol="T",
            flatten_trade=tr,
            order_shares=347,
            ib_baseline=347,
            started_at=0,
            quote_px=20.7,
            poll_wait=0,
        )
        self.assertFalse(ok)
        self.assertTrue(src.startswith("rejected"))

    @patch("core.fill_tracker.ib_position_shares", return_value=0.0)
    @patch("core.fill_tracker.poll_trade_fill", return_value=(20.71, 347.0))
    def test_order_fill_confirmed(self, _poll, _pos):
        tr = SimpleNamespace(
            order=SimpleNamespace(orderId=2),
            orderStatus=_FakeStatus("Filled", 347, 20.71),
        )
        qty, px, ok, src = confirm_exit_fill(
            MagicMock(),
            symbol="T",
            flatten_trade=tr,
            order_shares=347,
            ib_baseline=347,
            started_at=0,
            quote_px=20.7,
            poll_wait=0,
        )
        self.assertTrue(ok)
        self.assertEqual(src, "order_fill")
        self.assertEqual(qty, 347.0)

    @patch("core.fill_tracker.ib_position_shares", return_value=347.0)
    @patch("core.fill_tracker.poll_trade_fill", return_value=(0.0, 0.0))
    def test_pending_submitted(self, _poll, _pos):
        tr = SimpleNamespace(
            order=SimpleNamespace(orderId=3),
            orderStatus=_FakeStatus("Submitted"),
        )
        _, _, ok, src = confirm_exit_fill(
            MagicMock(),
            symbol="T",
            flatten_trade=tr,
            order_shares=347,
            ib_baseline=347,
            started_at=0,
            quote_px=20.7,
            poll_wait=0,
        )
        self.assertFalse(ok)
        self.assertEqual(src, "pending:Submitted")


if __name__ == "__main__":
    unittest.main()
