#!/usr/bin/env python3
"""Unit tests for IB fill confirmation helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.fill_tracker import (
    confirm_entry_fill,
    ib_fill_strict,
    ib_position_shares,
    require_ib_fill_sync,
)


class TestConfirmEntryFill:
    def test_order_status_fill(self):
        trade = MagicMock()
        trade.orderStatus.filled = 100
        trade.orderStatus.avgFillPrice = 1.25
        trade.orderStatus.status = "Filled"

        ib = MagicMock()
        ib.positions.return_value = []

        qty, px, ok, src = confirm_entry_fill(
            ib,
            symbol="TEST",
            parent_trade=trade,
            cache=None,
            order_shares=100,
            min_fill_ratio=0.85,
            ib_pos_baseline=0,
            started_at=1000.0,
            quote_px=1.20,
        )
        assert ok is True
        assert qty == 100
        assert px == 1.25
        assert src == "order_status"

    def test_position_delta_not_orphan(self):
        trade = MagicMock()
        trade.orderStatus.filled = 0
        trade.orderStatus.avgFillPrice = 0
        trade.orderStatus.status = "Submitted"

        pos = MagicMock()
        pos.contract.symbol = "TEST"
        pos.position = 5000
        pos.avgCost = 0.01

        ib = MagicMock()
        ib.positions.return_value = [pos]
        ib.fills.return_value = []

        qty, px, ok, src = confirm_entry_fill(
            ib,
            symbol="TEST",
            parent_trade=trade,
            cache=None,
            order_shares=100,
            min_fill_ratio=0.85,
            ib_pos_baseline=4900,
            started_at=1000.0,
            quote_px=1.20,
        )
        assert ok is True
        assert qty == 100
        assert src == "position_delta"

    def test_orphan_position_rejected(self):
        trade = MagicMock()
        trade.orderStatus.filled = 0
        trade.orderStatus.status = "PreSubmitted"

        pos = MagicMock()
        pos.contract.symbol = "TEST"
        pos.position = 5000
        pos.avgCost = 1.20

        ib = MagicMock()
        ib.positions.return_value = [pos]
        ib.fills.return_value = []

        qty, px, ok, src = confirm_entry_fill(
            ib,
            symbol="TEST",
            parent_trade=trade,
            cache=None,
            order_shares=100,
            min_fill_ratio=0.85,
            ib_pos_baseline=0,
            started_at=1000.0,
            quote_px=1.20,
        )
        assert ok is False
        assert qty == 0


class TestIbSyncFlags:
    def test_defaults_on(self):
        assert require_ib_fill_sync() is True
        assert ib_fill_strict() is True
