#!/usr/bin/env python3
"""Tests for IB fill reconciliation on exit."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from core.fill_reconciler import (
    FillExecutionCache,
    FillRecord,
    PendingClose,
    build_close_record,
    resolve_entry_from_ib,
    resolve_exit_from_ib,
)


class TestResolveEntryFromIb:
    def test_prefers_execution_cache(self):
        cache = MagicMock()
        cache.latest.return_value = FillRecord(
            symbol="ABC", side="BOT", price=1.55, qty=100, ts=time.time(),
        )
        ib = MagicMock()
        px, ok = resolve_entry_from_ib(
            ib, cache,
            symbol="ABC",
            slot_entry_fill=1.50,
            slot_entry_quote=1.50,
            opened_at=time.time() - 30,
        )
        assert ok is True
        assert px == 1.55

    def test_falls_back_to_slot_fill(self):
        ib = MagicMock()
        px, ok = resolve_entry_from_ib(
            ib, None,
            symbol="ABC",
            slot_entry_fill=1.42,
            slot_entry_quote=1.40,
            opened_at=time.time() - 30,
        )
        assert ok is True
        assert px == 1.42


class TestBuildCloseRecordStrict:
    def test_strict_blocks_quote_only_exit(self):
        pending = PendingClose(
            ticker="XYZ",
            reason="test",
            quote_exit_px=2.00,
            slot={"entry_fill_px": 1.00, "entry_price": 1.00, "shares": 10},
            shares=10,
            opened_at=time.time() - 60,
        )
        ib = MagicMock()
        ib.fills.return_value = []
        rec = build_close_record(pending, ib, None, force=False, cfg=MagicMock())
        assert rec is None

    def test_ib_exit_fill_confirmed(self):
        trade = MagicMock()
        trade.orderStatus.filled = 10
        trade.orderStatus.avgFillPrice = 1.25
        trade.orderStatus.status = "Filled"
        pending = PendingClose(
            ticker="XYZ",
            reason="test",
            quote_exit_px=1.20,
            slot={"entry_fill_px": 1.00, "entry_price": 1.00, "shares": 10},
            shares=10,
            opened_at=time.time() - 60,
            flatten_trade=trade,
        )
        ib = MagicMock()
        ib.fills.return_value = []
        rec = build_close_record(pending, ib, None, force=False, cfg=MagicMock())
        assert rec is not None
        assert rec["exit_fill"] == 1.25
        assert rec["fill_confirmed"] is True
        assert rec["pnl_usd"] == 2.5


class TestResolveExitFromIb:
    def test_bracket_child_fill(self):
        stop = MagicMock()
        stop.orderStatus.status = "Filled"
        stop.orderStatus.avgFillPrice = 3.10
        stop.orderStatus.filled = 50
        bracket = MagicMock()
        bracket.stop_trade = stop
        bracket.target_trade = None
        bracket.parent_trade = None
        px, ok = resolve_exit_from_ib(
            MagicMock(), None,
            symbol="TST",
            bracket=bracket,
            quote_px=3.00,
            since_ts=time.time() - 120,
            entry_fill=3.50,
        )
        assert ok is True
        assert px == 3.10
