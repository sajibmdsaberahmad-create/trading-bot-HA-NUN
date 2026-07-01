#!/usr/bin/env python3
"""Tests for IB-only Telegram notification context."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.ib_truth import IBAccountSnapshot, IBPosition, IBTruthSnapshot
from core.notify_ib_context import ib_telegram_account, merge_ib_telegram_context, telegram_notify_context


def _mock_runner():
    r = MagicMock()
    r.cfg = MagicMock()
    r.ib = MagicMock()
    r.top_pick = None
    r._locked_targets = []
    r.pilot.get_veteran_status.return_value = {"level": "Cadet"}
    return r


def test_ib_telegram_account_no_bot_nav():
    runner = _mock_runner()
    snap = IBTruthSnapshot(
        account=IBAccountSnapshot(net_liquidation=50_000, realized_pnl=10, unrealized_pnl=5),
        positions=[
            IBPosition(symbol="T", qty=100, avg_cost=20.0, market_price=20.5, unrealized_pnl=50),
        ],
        session_pnl_fifo=125.5,
        round_trips=[MagicMock(), MagicMock()],
        refreshed_at=1.0,
        session_scope="calendar",
    )
    with patch("core.notify_ib_context.refresh"), patch(
        "core.notify_ib_context.get_snapshot", return_value=snap
    ), patch(
        "core.notify_ib_context.account_summary",
        return_value={
            "ib_equity": 50_000,
            "equity": 50_000,
            "ib_fifo_session_pnl": 125.5,
            "day_pnl": 125.5,
            "ib_change": 125.5,
            "ib_realized_pnl": 10,
            "ib_unrealized_pnl": 5,
        },
    ), patch("core.notify_ib_context.ib_truth_enabled", return_value=True):
        ctx = ib_telegram_account(runner)
    assert "bot_nav" not in ctx
    assert ctx["nav"] == 50_000
    assert ctx["session_pnl"] == 125.5
    assert ctx["trades_today"] == 2


def test_telegram_notify_merges_ib_entry_fill():
    runner = _mock_runner()
    snap = IBTruthSnapshot(
        account=IBAccountSnapshot(net_liquidation=50_000),
        positions=[IBPosition(symbol="AAPL", qty=10, avg_cost=150.25, market_price=151.0)],
        session_pnl_fifo=0,
        refreshed_at=1.0,
    )
    with patch("core.notify_ib_context.refresh"), patch(
        "core.notify_ib_context.get_snapshot", return_value=snap
    ), patch(
        "core.notify_ib_context.account_summary",
        return_value={
            "ib_equity": 50_000,
            "equity": 50_000,
            "ib_fifo_session_pnl": 0,
            "day_pnl": 0,
            "ib_change": 0,
        },
    ), patch("core.notify_ib_context.ib_truth_enabled", return_value=True), patch(
        "core.rth_session.rth_reply_context", return_value={"market_state": "open"}
    ), patch("core.war_account.war_account_context", return_value={}):
        ctx = telegram_notify_context(
            runner,
            extra={"ticker": "AAPL", "entry": 999.0, "shares": 1},
            event_type="trade_opened",
        )
    assert ctx["entry"] == 150.25
    assert ctx["shares"] == 10
    assert ctx["pnl_source"] == "ib_fill"
    assert ctx["data_source"] == "ib_truth"


def test_merge_ib_strips_bot_nav():
    runner = _mock_runner()
    snap = IBTruthSnapshot(
        account=IBAccountSnapshot(net_liquidation=40_000),
        session_pnl_fifo=50.0,
        refreshed_at=1.0,
    )
    with patch("core.notify_ib_context.refresh"), patch(
        "core.notify_ib_context.get_snapshot", return_value=snap
    ), patch(
        "core.notify_ib_context.account_summary",
        return_value={
            "ib_equity": 40_000,
            "equity": 40_000,
            "ib_fifo_session_pnl": 50.0,
            "day_pnl": 50.0,
        },
    ), patch("core.notify_ib_context.ib_truth_enabled", return_value=True):
        ctx = merge_ib_telegram_context(
            runner, None, {"bot_nav": 999, "bot_cash": 888, "custom": "x"},
        )
    assert "bot_nav" not in ctx
    assert "bot_cash" not in ctx
    assert ctx["nav"] == 40_000
    assert ctx["custom"] == "x"
    assert ctx["session_pnl"] == 50.0
