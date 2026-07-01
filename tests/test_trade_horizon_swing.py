#!/usr/bin/env python3
"""Swing horizon gate tests."""
from __future__ import annotations

from unittest.mock import patch

from core.config import BotConfig
from core.trade_horizon import swing_ib_live_enabled, swing_shadow_enabled


def test_swing_ib_live_toddler_stage():
    with patch.dict(
        "os.environ",
        {"SWING_IB_LIVE": "true", "CAPITAL_PHASES_ENABLED": "true"},
        clear=False,
    ):
        with patch("core.capital_phase.capital_phase", return_value="premarket_full"):
            with patch("core.brain_maturity.compute_stage", return_value="toddler"):
                assert swing_ib_live_enabled(BotConfig(), "premarket_full") is True


def test_swing_shadow_toddler_stage():
    with patch.dict("os.environ", {"SWING_SHADOW_ENABLED": "true"}, clear=False):
        with patch("core.brain_maturity.compute_stage", return_value="toddler"):
            assert swing_shadow_enabled(BotConfig()) is True
