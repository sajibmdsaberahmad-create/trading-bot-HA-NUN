"""Institutional algo-wave entry — green doctrine integration."""
from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.config import BotConfig
from core.green_wave_entry import (
    assess_wave_remaining_edge,
    detect_institutional_impulse,
    green_wave_entry_enabled,
    institutional_entry_veto,
)
from core.green_trade_doctrine import assess_green_entry, assess_dynamic_exit


@pytest.fixture(autouse=True)
def wave_env(monkeypatch):
    monkeypatch.setenv("GREEN_DOCTRINE_ENTRY", "true")
    monkeypatch.setenv("GREEN_DOCTRINE_UNIFIED", "true")
    monkeypatch.setenv("GREEN_WAVE_ENTRY", "true")
    monkeypatch.setenv("GREEN_WAVE_RELAX_GREEN_BAR", "true")


def _bars(n: int = 30, *, green_last: bool = True) -> pd.DataFrame:
    closes = np.linspace(10.0, 10.4, n)
    if not green_last:
        closes[-1] = closes[-2] - 0.05
    opens = closes - 0.01
    vols = np.full(n, 100_000.0)
    vols[-3:] = 350_000.0
    return pd.DataFrame({
        "open": opens,
        "high": closes + 0.02,
        "low": closes - 0.02,
        "close": closes,
        "volume": vols,
    })


def test_wave_entry_enabled():
    assert green_wave_entry_enabled(BotConfig())


def test_institutional_impulse_detects_footprint():
    inst = {
        "direction": "accumulating",
        "strength": 0.62,
        "confidence": 0.55,
        "block_trade_detected": True,
        "volume_cluster_detected": True,
        "cumulative_delta_z": 1.2,
        "relative_volume": 2.1,
        "tick_velocity": 0.002,
    }
    micro = {
        "vol_accel": 1.45,
        "spike_likelihood": 0.55,
        "fade_risk": 0.15,
        "dir": 1,
        "momentum": 0.12,
    }
    row = detect_institutional_impulse(inst, micro, spike_ratio=1.8)
    assert row["impulse_ok"] is True
    assert row["impulse_score"] >= 0.48


def test_institutional_veto_on_distribution():
    inst = {"direction": "distributing", "strength": 0.7}
    assert institutional_entry_veto(inst, {}) is not None


def test_green_entry_allows_wave_without_green_bar():
    cfg = BotConfig()
    df = _bars(green_last=False)
    inst = {
        "direction": "accumulating",
        "strength": 0.65,
        "confidence": 0.6,
        "block_trade_detected": True,
        "volume_cluster_detected": True,
        "cumulative_delta_z": 1.5,
        "relative_volume": 2.0,
    }
    micro = {
        "vol_accel": 1.5,
        "spike_likelihood": 0.5,
        "fade_risk": 0.1,
        "dir": 1,
        "momentum": 0.15,
        "pred_1bar": 10.5,
    }
    with patch("core.entry_quality.assess_entry_quality", return_value={
        "profit_probability": 0.72,
        "enter_ok": True,
        "reason": "ok",
    }):
        with patch("core.scalper_filters.only_uptrend", return_value=True):
            with patch("core.green_trade_doctrine._dynamic_min_confidence", return_value=0.55):
                with patch("core.green_trade_doctrine._dynamic_min_profit_prob", return_value=0.55):
                    a = assess_green_entry(
                cfg,
                ticker="TEST",
                df=df,
                current_px=10.4,
                micro=micro,
                spike_ratio=1.6,
                scan_score=80,
                ppo_action=1,
                ppo_conf=0.65,
                decision={"enter": True, "confidence": 0.65, "ppo_action": 1},
                institutional=inst,
            )
    assert a.get("wave_impulse") is True
    assert a.get("effective_green_bar") is True
    assert a.get("enter_ok") is True


def test_wave_remaining_edge_exits_when_faded():
    micro = {
        "profit_run": 0.1,
        "fade_risk": 0.75,
        "momentum": -0.05,
        "vol_accel": 0.7,
        "pred_1bar": 10.0,
        "pred_3bar": 9.98,
    }
    inst = {"direction": "distributing", "strength": 0.5}
    row = assess_wave_remaining_edge(
        micro, inst, current_px=10.0, pnl_pct=0.008, peak_pct=0.012,
    )
    assert row["wave_edge"] < 0.25
    assert row["should_exit_now"] is True


def test_dynamic_exit_wave_edge_profit_book():
    cfg = BotConfig()
    micro = {
        "profit_run": 0.05,
        "fade_risk": 0.8,
        "momentum": -0.1,
        "vol_accel": 0.75,
        "pred_1bar": 10.0,
        "pred_3bar": 9.95,
        "dir": -1,
    }
    inst = {"direction": "distributing", "strength": 0.55}
    dx = assess_dynamic_exit(
        cfg,
        ticker="T",
        current_px=10.0,
        entry_px=9.9,
        pnl_pct=0.01,
        peak_pct=0.015,
        micro=micro,
        institutional=inst,
    )
    assert dx.get("should_exit") is True
    assert dx.get("action") == "exit_profit"
