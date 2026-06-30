#!/usr/bin/env python3
"""War account auto-reset at RTH open (ET)."""

from __future__ import annotations

from unittest.mock import patch

from core.config import BotConfig
from core.war_account import _roll_rth_session, _roll_session


def test_rth_reset_clears_exhausted_trips():
    cfg = BotConfig()
    state = {
        "session_date": "2026-06-30",
        "rth_rolled_date": None,
        "round_trips_today": 5,
        "lab_round_trips_today": 4,
        "bullets_used_session": 5,
        "nav": 5399.0,
        "settled_cash": 3480.0,
        "lab_settled": 4975.0,
        "open_war": None,
        "open_lab": None,
        "mode": "OBSERVE",
    }
    with patch.dict("os.environ", {"WAR_CAPITAL_USD": "3500", "WAR_BULLETS": "8", "WAR_AUTO_RESET_AT_RTH": "true"}, clear=False):
        with patch("core.war_account._append_ledger"):
            with patch("core.rth_session.is_rth", return_value=True):
                with patch("core.war_account._today_key", return_value="2026-06-30"):
                    ok = _roll_rth_session(state, cfg)
    assert ok is True
    assert state["round_trips_today"] == 0
    assert state["lab_round_trips_today"] == 0
    assert state["settled_cash"] == 3500.0
    assert state["mode"] == "WAR_ACTIVE"
    assert state["rth_rolled_date"] == "2026-06-30"


def test_rth_reset_runs_once_per_day():
    cfg = BotConfig()
    state = {
        "session_date": "2026-06-30",
        "rth_rolled_date": "2026-06-30",
        "round_trips_today": 5,
        "settled_cash": 100.0,
        "open_war": None,
        "open_lab": None,
    }
    with patch("core.rth_session.is_rth", return_value=True):
        with patch("core.war_account._today_key", return_value="2026-06-30"):
            assert _roll_rth_session(state, cfg) is False
    assert state["round_trips_today"] == 5


def test_roll_session_invokes_rth_reset():
    cfg = BotConfig()
    state = {
        "session_date": "2026-06-30",
        "rth_rolled_date": None,
        "round_trips_today": 5,
        "nav": 2000.0,
        "settled_cash": 500.0,
        "open_war": None,
        "open_lab": None,
    }
    with patch.dict("os.environ", {"WAR_CAPITAL_USD": "3500", "WAR_AUTO_RESET_AT_RTH": "true"}, clear=False):
        with patch("core.war_account._append_ledger"):
            with patch("core.rth_session.is_rth", return_value=True):
                with patch("core.war_account._today_key", return_value="2026-06-30"):
                    _roll_session(state, cfg)
    assert state["round_trips_today"] == 0
    assert state["settled_cash"] == 3500.0


def test_fresh_trips_on_hanoon_start_when_settled_remains():
    from core.war_account import _maybe_refresh_trips_if_settled

    cfg = BotConfig()
    state = {
        "round_trips_today": 5,
        "bullets_used_session": 5,
        "settled_cash": 3469.0,
        "nav": 5807.0,
        "bullets_total": 8,
        "open_war": None,
        "open_lab": None,
        "operating_capital": 3500.0,
    }
    with patch.dict(
        "os.environ",
        {
            "WAR_BULLETS": "8",
            "WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY": "5",
            "WAR_FRESH_TRIPS_ON_START": "true",
            "WAR_LIVE_OPERATING_CAPITAL": "0",
        },
        clear=False,
    ):
        assert _maybe_refresh_trips_if_settled(state, cfg) is True
    assert state["round_trips_today"] == 0
    assert state["bullets_used_session"] == 0


def test_fresh_trips_skipped_when_settled_dry():
    from core.war_account import _maybe_refresh_trips_if_settled

    cfg = BotConfig()
    state = {
        "round_trips_today": 8,
        "settled_cash": 10.0,
        "nav": 200.0,
        "bullets_total": 8,
        "operating_capital": 3500.0,
        "open_war": None,
        "open_lab": None,
    }
    with patch.dict(
        "os.environ",
        {"WAR_BULLETS": "8", "WAR_FRESH_TRIPS_ON_START": "true", "WAR_LIVE_OPERATING_CAPITAL": "0"},
        clear=False,
    ):
        assert _maybe_refresh_trips_if_settled(state, cfg) is False
    assert state["round_trips_today"] == 8
