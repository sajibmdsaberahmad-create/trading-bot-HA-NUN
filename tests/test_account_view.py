#!/usr/bin/env python3
"""Unit tests for account_view."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.account_view import day_pnl_ib, display_equity


def test_day_pnl_ib():
    runner = MagicMock()
    runner._ib_starting_balance = 100_000
    runner.account_equity = 99_500
    chg, pct = day_pnl_ib(runner)
    assert chg == -500
    assert pct == pytest.approx(-0.5)


def test_display_equity_prefers_ib():
    runner = MagicMock()
    runner.account_equity = 50_000
    runner.bot_nav = 999_999
    cfg = MagicMock()
    with patch("core.account_view.require_ib_fill_sync", return_value=True):
        assert display_equity(runner, cfg) == 50_000
