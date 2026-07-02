"""Session boundary transitions — pre-market open and loop cadence."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.config import BotConfig
from core.market_hours import MARKET_TZ, get_market_state
from core.rth_session import is_pre_market_session, rth_main_loop_sec


def test_pre_market_state_at_four_am():
    cfg = BotConfig()
    fake = datetime(2026, 7, 2, 4, 5, tzinfo=MARKET_TZ)
    with patch("core.market_hours.now_et", return_value=fake):
        assert get_market_state(cfg) == "pre_market"


def test_pre_market_fast_main_loop():
    cfg = BotConfig()
    with patch("core.rth_session.is_pre_market_session", return_value=True):
        assert rth_main_loop_sec(cfg) == pytest.approx(cfg.PRE_MARKET_LOOP_SEC)


def test_on_pre_market_open_forces_rescan():
    from core.scalper_runner import ScalperRunner

    runner = MagicMock(spec=ScalperRunner)
    runner.cfg = BotConfig()
    runner._pre_market_open_day = None
    runner._day_session_ended = True
    runner._last_scan_time = 999.0
    runner._needs_initial_scan = False
    runner._deferred_ib_scan = True
    runner._md_suspended = True
    runner._contract_blacklist = set()
    runner._locked_targets = []
    runner.autopilot = None
    runner.consciousness = None

    with patch("core.scalper_session.now_et") as mock_now:
        mock_now.return_value = datetime(2026, 7, 2, 4, 0, tzinfo=MARKET_TZ)
        with patch("core.scalper_session.clear_transient_md_blocks", return_value=[]):
            with patch("core.scalper_session.rth_tier", return_value="pre_market"):
                with patch("core.scalper_session.teach_profit_hunt_lesson"):
                    from core.scalper_session import ScalperSessionMixin

                    ScalperSessionMixin._on_pre_market_open(runner, "overnight")

    assert runner._pre_market_open_day == "2026-07-02"
    assert runner._day_session_ended is False
    assert runner._needs_initial_scan is True
    assert runner._last_scan_time == 0.0
    assert runner._deferred_ib_scan is False
    runner._resume_tradable_market_data.assert_called_once()
