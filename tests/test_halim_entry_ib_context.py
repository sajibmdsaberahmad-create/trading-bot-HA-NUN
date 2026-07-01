#!/usr/bin/env python3
"""Halim entry prompt — IB + sizing context for spike participation."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from core.config import BotConfig
from core.halim_entry_line import (
    _build_entry_prompt,
    build_halim_entry_ib_context,
    halim_entry_ib_context_enabled,
)


def test_ib_context_disabled_by_env():
    with patch.dict(os.environ, {"HALIM_ENTRY_IB_CONTEXT": "false"}):
        assert halim_entry_ib_context_enabled() is False
        assert build_halim_entry_ib_context(BotConfig(), ticker="PLUG", price=3.17) == ""


def test_build_entry_prompt_includes_ib_and_math_lines():
    prompt = _build_entry_prompt(
        ticker="PLUG",
        price=3.17,
        spike=2.3,
        scan=76.0,
        ppo_buy=True,
        ppo_conf=0.62,
        ib_context="ib nav=1000 buying_power=800 sizing risk_usd=50 shares_hint=15 ask=3.1700",
        profit_prob=0.83,
    )
    assert "ib nav=1000" in prompt
    assert "shares_hint=15" in prompt
    assert "size_intent" in prompt
    assert "calculated lottery" in prompt


@patch("core.ib_truth.ib_truth_enabled", return_value=True)
@patch("core.ib_truth.get_snapshot")
@patch("core.war_account.war_account_enabled", return_value=False)
def test_build_halim_entry_ib_context_sizing(_war, mock_snap, _truth):
    acct = MagicMock()
    acct.net_liquidation = 12_450.0
    acct.buying_power = 47_832.0
    acct.available_funds = 45_100.0
    acct.realized_pnl = 42.0
    acct.excess_liquidity = 9_200.0
    snap = MagicMock()
    snap.refreshed_at = 1.0
    snap.account = acct
    snap.long_positions.return_value = {"SPY": object()}
    mock_snap.return_value = snap

    line = build_halim_entry_ib_context(BotConfig(), ticker="PLUG", price=3.17)
    assert "nav=12450" in line
    assert "buying_power=47832" in line
    assert "shares_hint=" in line
    assert "ask=3.1700" in line
