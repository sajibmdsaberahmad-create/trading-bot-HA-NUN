#!/usr/bin/env python3
"""Capital phase routing tests."""
from __future__ import annotations

from unittest.mock import patch

from core.capital_phase import (
    PHASE_PREMARKET_FULL,
    PHASE_RTH_FULL,
    PHASE_RTH_WAR,
    allows_horizon_live,
    capital_phase,
    uses_war_sizing,
)
from core.config import BotConfig


def test_swing_allowed_during_rth_war():
    with patch.dict(
        "os.environ",
        {"CAPITAL_PHASES_ENABLED": "true", "SWING_IB_LIVE": "true", "SWING_LIVE_DURING_RTH_WAR": "true"},
        clear=False,
    ):
        with patch("core.capital_phase.capital_phase", return_value=PHASE_RTH_WAR):
            with patch("core.trade_horizon.swing_ib_live_enabled", return_value=True):
                assert allows_horizon_live("swing", BotConfig()) is True
                assert uses_war_sizing(BotConfig(), horizon="swing") is False
                assert uses_war_sizing(BotConfig(), horizon="scalp") is True


def test_premarket_full_phase():
    with patch.dict("os.environ", {"CAPITAL_PHASES_ENABLED": "true"}, clear=False):
        with patch("core.capital_phase.get_market_state", return_value="pre_market"):
            assert capital_phase(BotConfig()) == PHASE_PREMARKET_FULL
            assert uses_war_sizing(BotConfig()) is False


def test_rth_war_when_pool_ok():
    with patch.dict("os.environ", {"CAPITAL_PHASES_ENABLED": "true"}, clear=False):
        with patch("core.capital_phase.get_market_state", return_value="open"):
            with patch("core.capital_phase.war_pool_exhausted", return_value=False):
                assert capital_phase(BotConfig()) == PHASE_RTH_WAR
                assert uses_war_sizing(BotConfig()) is True


def test_rth_full_when_war_exhausted():
    with patch.dict("os.environ", {"CAPITAL_PHASES_ENABLED": "true"}, clear=False):
        with patch("core.capital_phase.get_market_state", return_value="open"):
            with patch("core.capital_phase.war_pool_exhausted", return_value=True):
                assert capital_phase(BotConfig()) == PHASE_RTH_FULL
                assert uses_war_sizing(BotConfig()) is False
