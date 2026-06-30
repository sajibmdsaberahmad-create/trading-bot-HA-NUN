#!/usr/bin/env python3
"""War account auto-reset at RTH open (ET) + balance-driven trip limits."""

from __future__ import annotations

from unittest.mock import patch

from core.config import BotConfig
from core.war_account import (
    _maybe_refresh_trips_if_settled,
    _recompute_mode,
    _roll_rth_session,
    _roll_session,
    balance_driven_trips_enabled,
    war_bullets_remaining,
)


def _paper_balance_env():
    return patch.dict(
        "os.environ",
        {
            "WAR_BULLETS": "8",
            "WAR_BALANCE_DRIVEN_TRIPS": "true",
            "WAR_LIVE_OPERATING_CAPITAL": "0",
            "WAR_CAPITAL_USD": "3500",
        },
        clear=False,
    )


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
            "WAR_BALANCE_DRIVEN_TRIPS": "false",
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
        {"WAR_BULLETS": "8", "WAR_FRESH_TRIPS_ON_START": "true", "WAR_BALANCE_DRIVEN_TRIPS": "false", "WAR_LIVE_OPERATING_CAPITAL": "0"},
        clear=False,
    ):
        assert _maybe_refresh_trips_if_settled(state, cfg) is False
    assert state["round_trips_today"] == 8


def test_balance_driven_ignores_fixed_trip_count():
    cfg = BotConfig()
    state = {
        "round_trips_today": 5,
        "bullets_used_session": 5,
        "settled_cash": 3469.0,
        "nav": 5807.0,
        "bullets_total": 8,
        "operating_capital": 3500.0,
        "open_war": None,
        "open_lab": None,
    }
    with _paper_balance_env():
        assert balance_driven_trips_enabled(cfg) is True
        assert war_bullets_remaining(state, cfg) >= 5
        assert _recompute_mode(state, cfg) == "WAR_ACTIVE"


def test_balance_driven_observe_when_settled_dry():
    cfg = BotConfig()
    state = {
        "round_trips_today": 12,
        "settled_cash": 40.0,
        "nav": 200.0,
        "bullets_total": 8,
        "operating_capital": 3500.0,
        "open_war": None,
        "open_lab": None,
        "lab_settled": 0.0,
    }
    with _paper_balance_env():
        assert war_bullets_remaining(state, cfg) == 0
        assert _recompute_mode(state, cfg) == "OBSERVE"


def test_fixed_cap_still_blocks_at_trip_max():
    cfg = BotConfig()
    state = {
        "round_trips_today": 5,
        "settled_cash": 3469.0,
        "nav": 5807.0,
        "bullets_total": 8,
        "operating_capital": 3500.0,
        "open_war": None,
        "open_lab": None,
    }
    with patch.dict(
        "os.environ",
        {
            "WAR_BULLETS": "8",
            "WAR_BALANCE_DRIVEN_TRIPS": "false",
            "WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY": "5",
            "WAR_LIVE_OPERATING_CAPITAL": "0",
        },
        clear=False,
    ):
        assert balance_driven_trips_enabled(cfg) is False
        assert _recompute_mode(state, cfg) == "OBSERVE"
