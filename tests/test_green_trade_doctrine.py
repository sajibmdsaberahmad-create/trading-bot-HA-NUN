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


def test_multibar_ride_defaults(monkeypatch):
    monkeypatch.setenv("GREEN_MULTIBAR_RIDE", "true")
    from core.green_trade_doctrine import multibar_ride_enabled, slippage_exit_enabled
    cfg = BotConfig()
    assert multibar_ride_enabled(cfg)
    assert slippage_exit_enabled(cfg)


def test_dynamic_exit_ride_action(monkeypatch):
    monkeypatch.setenv("GREEN_DOCTRINE_EXIT", "true")
    monkeypatch.setenv("GREEN_MULTIBAR_RIDE", "true")
    from core.green_trade_doctrine import assess_dynamic_exit
    cfg = BotConfig()
    micro = {
        "pred_1bar": 10.2,
        "pred_3bar": 10.5,
        "profit_run": 0.55,
        "fade_risk": 0.2,
        "dir": 1,
        "momentum": 0.1,
        "vol_accel": 1.2,
    }
    dx = assess_dynamic_exit(
        cfg,
        ticker="T",
        current_px=10.0,
        entry_px=9.9,
        pnl_pct=0.01,
        peak_pct=0.012,
        micro=micro,
        bars_held=1,
    )
    assert dx.get("action") in ("ride_multibar", "hold", "exit_profit")


def test_green_exit_on_giveback(monkeypatch):
    monkeypatch.setenv("GREEN_DOCTRINE_EXIT", "true")
    from core.green_trade_doctrine import assess_dynamic_exit
    cfg = BotConfig()
    ge = assess_dynamic_exit(
        cfg,
        ticker="TEST",
        pnl_pct=0.003,
        peak_pct=0.008,
        micro={"dir": -1, "fade_risk": 0.6},
    )
    assert ge["pnl_pct"] > 0
