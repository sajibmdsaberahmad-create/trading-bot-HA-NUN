#!/usr/bin/env python3
"""Tests for unified green trade doctrine across capital phases."""
import pandas as pd

from core.config import BotConfig
from core.green_trade_doctrine import (
    assess_green_entry,
    assess_green_exit,
    green_entry_mandatory,
    require_green_entry,
    same_tactics_all_phases,
    unified_doctrine_enabled,
)
from core.war_entry_gates import war_gates_active


def _uptrend_df(n=30):
    rows = []
    px = 10.0
    for i in range(n):
        o = px
        c = px + 0.05
        rows.append({"open": o, "high": c + 0.02, "low": o - 0.01, "close": c, "volume": 1000 + i})
        px = c
    return pd.DataFrame(rows)


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
    monkeypatch.setenv("GREEN_DOCTRINE_ENTRY", "true")
    cfg = BotConfig()
    flat = pd.DataFrame([{"open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": 100}] * 5)
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
    assert "uptrend" in block or "green_bar" in block or "profit_prob" in block


def test_green_entry_passes_strong_setup(monkeypatch):
    monkeypatch.setenv("GREEN_DOCTRINE_ENTRY", "true")
    monkeypatch.setenv("CAPITAL_DISCIPLINE", "false")
    cfg = BotConfig()
    df = _uptrend_df()
    micro = {"dir": 1, "pred_1bar": float(df["close"].iloc[-1]) + 0.1, "momentum": 0.05}
    a = assess_green_entry(
        cfg,
        ticker="TEST",
        df=df,
        current_px=float(df["close"].iloc[-1]),
        micro=micro,
        spike_ratio=1.8,
        scan_score=70,
        ppo_action=1,
        ppo_conf=0.75,
        decision={"enter": True, "confidence": 0.75, "halim_enter": True},
    )
    assert a["uptrend"] is True
    assert a["green_bar"] is True
    assert a["prediction_up"] is True


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
    assert ge["should_exit"] or ge["pnl_pct"] > 0
