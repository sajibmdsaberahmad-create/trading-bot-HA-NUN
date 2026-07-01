#!/usr/bin/env python3
"""Tests for LLM response sanity, strict green doctrine, PPO lead, IB session P&L."""

from __future__ import annotations

import os
from unittest.mock import patch

from core.capital_phase import PHASE_PREMARKET_FULL
from core.capital_discipline import allows_ppo_lead_while_pending
from core.commander_runtime import commander_entry_floors
from core.config import BotConfig
from core.green_trade_doctrine import assess_green_entry
from core.ib_truth import IBTruthSnapshot, IBAccountSnapshot, day_pnl_from_snapshot
from core.response_sanity import cjk_char_ratio, response_looks_english
from core.trading_copilot import _parse_brief_json


def test_cjk_detects_chinese_copilot_garbage():
    sample = (
        "PPO signal=False conf=0.05\n行动计划：1. 优先关注TA质量"
    )
    assert cjk_char_ratio(sample) > 0.05
    assert not response_looks_english(sample)


def test_copilot_parse_rejects_non_json_chinese():
    raw = "PPO signal=False\n行动计划：优先关注TA"
    brief = _parse_brief_json(raw, {"win_rate": 0.5})
    assert brief is None


def test_commander_floors_apply_in_premarket_full():
    cfg = BotConfig()
    with patch("core.capital_phase.capital_phase", return_value=PHASE_PREMARKET_FULL):
        floors = commander_entry_floors(cfg)
    assert floors.get("min_profit_probability", 0) >= 0.80


def test_green_entry_strict_in_premarket_requires_green_bar():
    import numpy as np
    import pandas as pd

    cfg = BotConfig()
    n = 25
    closes = np.linspace(1.0, 1.25, n)
    closes[-1] = closes[-2] * 0.99  # red last bar
    df = pd.DataFrame(
        {
            "open": closes * 1.002,
            "high": closes * 1.003,
            "low": closes * 0.997,
            "close": closes,
            "volume": np.full(n, 5000.0),
        }
    )
    micro = {
        "profit_probability": 0.85,
        "dir": 1,
        "pred_1bar": 1.26,
        "spike_likelihood": 0.6,
    }
    with patch("core.capital_phase.capital_phase", return_value=PHASE_PREMARKET_FULL):
        a = assess_green_entry(
            cfg,
            ticker="TEST",
            df=df,
            current_px=float(closes[-1]),
            micro=micro,
            spike_ratio=2.0,
            scan_score=85,
            ppo_action=1,
            ppo_conf=0.75,
            decision={"enter": True, "confidence": 0.75, "halim_enter": True},
        )
    assert a.get("green_bar") is False
    assert a.get("enter_ok") is False


def test_ppo_lead_honored_when_env_set():
    cfg = BotConfig()
    cfg.PPO_LEAD_WHILE_COUNCIL_PENDING = True
    with patch.dict(os.environ, {"CAPITAL_DISCIPLINE": "true"}):
        assert allows_ppo_lead_while_pending(cfg, scan_score=50, spike_ratio=1.1) is True


def test_calendar_session_prefers_fifo_pnl():
    snap = IBTruthSnapshot(
        account=IBAccountSnapshot(net_liquidation=100_000),
        session_pnl_ib=0.0,
        session_pnl_fifo=42.5,
        session_scope="calendar",
        refreshed_at=1.0,
    )
    pnl, _ = day_pnl_from_snapshot(snap, 99_000)
    assert pnl == 42.5
