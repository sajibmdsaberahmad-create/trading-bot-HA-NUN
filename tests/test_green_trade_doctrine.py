#!/usr/bin/env python3
"""Tests for unified green trade doctrine across capital phases."""
import pytest

from core.config import BotConfig
from core.green_trade_doctrine import (
    assess_green_exit,
    green_entry_mandatory,
    same_tactics_all_phases,
    unified_doctrine_enabled,
)
from core.war_entry_gates import war_gates_active


def test_unified_doctrine_defaults_on():
    cfg = BotConfig()
    assert unified_doctrine_enabled(cfg)
    assert same_tactics_all_phases(cfg)
    assert green_entry_mandatory(cfg)


def test_war_gates_active_with_unified_doctrine(monkeypatch):
    monkeypatch.setenv("GREEN_DOCTRINE_UNIFIED", "true")
    monkeypatch.setenv("WAR_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("WAR_SNIPER_MODE", "false")
    cfg = BotConfig()
    assert war_gates_active(cfg)


def test_green_entry_blocks_without_uptrend(monkeypatch):
    pd = pytest.importorskip("pandas")
    monkeypatch.setenv("GREEN_DOCTRINE_ENTRY", "true")
    flat = pd.DataFrame([{"open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": 100}] * 5)
    from core.green_trade_doctrine import require_green_entry

    cfg = BotConfig()
    block = require_green_entry(
        cfg,
        ticker="TEST",
        df=flat,
        current_px=10.0,
        micro={"dir": 1, "pred_1bar": 10.5},
        spike_ratio=1.5,
        scan_score=60,
        ppo_action=1,
        ppo_conf=0.7,
        decision={"enter": True, "confidence": 0.7},
    )
    assert block is not None


def test_green_exit_on_giveback(monkeypatch):
    monkeypatch.setenv("GREEN_DOCTRINE_EXIT", "true")
    cfg = BotConfig()
    ge = assess_green_exit(
        cfg,
        ticker="TEST",
        pnl_pct=0.003,
        peak_pct=0.008,
        micro={"dir": -1, "fade_risk": 0.6},
    )
    assert ge["pnl_pct"] > 0
