"""War ledger must track each open ticker — not overwrite single open_war slot."""
from __future__ import annotations

from unittest.mock import patch

from core.config import BotConfig
from core.war_account import (
    _normalize_open_positions,
    _reconcile_war_cash_from_positions,
    _resolve_open_slot,
    adopt_war_ib_recovery,
    record_entry,
    record_exit,
)


def test_normalize_migrates_legacy_open_war():
    state = {
        "open_war": {"ticker": "BITO", "shares": 58, "entry": 7.95, "comm": 0.5},
        "open_lab": None,
    }
    _normalize_open_positions(state)
    assert "BITO" in state["open_wars"]
    assert state["open_wars"]["BITO"]["entry"] == 7.95


def test_record_exit_uses_matching_ticker_not_stale_open_war():
    cfg = BotConfig()
    state = {
        "nav": 3500.0,
        "cash": 2000.0,
        "settled_cash": 2000.0,
        "deployed_usd": 1500.0,
        "open_wars": {
            "BITO": {"ticker": "BITO", "shares": 58, "entry": 7.95, "comm": 0.5},
            "TZA": {"ticker": "TZA", "shares": 100, "entry": 10.0, "comm": 0.5},
        },
        "open_war": {"ticker": "TZA", "shares": 100, "entry": 10.0, "comm": 0.5},
        "open_labs": {},
        "open_lab": None,
        "unsettled": [],
        "round_trips_today": 0,
        "session_pnl_war": 0.0,
        "mode": "WAR_ACTIVE",
    }
    with patch("core.war_account.war_account_enabled", return_value=True):
        with patch("core.war_account.load_state", return_value=state):
            with patch("core.war_account.save_state"):
                with patch("core.war_account._append_ledger"):
                    with patch("core.war_account.apply_slippage_overlay", side_effect=lambda *a, **k: (k.get("quote", a[3] if len(a) > 3 else 0), 0.0)):
                        row = record_exit(
                            cfg,
                            ticker="BITO",
                            shares=58,
                            ib_fill=7.94,
                            quote=7.94,
                            pnl_usd_ib=-0.58,
                            entry_ib_fill=7.95,
                            exit_reason="trailing_stop",
                        )
    assert row["net_pnl"] > -5.0
    assert row["net_pnl"] > -50.0
    assert "BITO" not in state["open_wars"]
    assert "TZA" in state["open_wars"]


def test_record_entry_keeps_multiple_open_wars():
    cfg = BotConfig()
    state = {
        "nav": 3500.0,
        "cash": 2500.0,
        "settled_cash": 2500.0,
        "deployed_usd": 500.0,
        "open_wars": {
            "BITO": {"ticker": "BITO", "shares": 58, "entry": 7.95, "comm": 0.5},
        },
        "open_war": {"ticker": "BITO", "shares": 58, "entry": 7.95, "comm": 0.5},
        "open_labs": {},
        "open_lab": None,
        "bullets_used_session": 1,
        "entries_today": 1,
        "fee_drag_today": 0.5,
        "mode": "WAR_ACTIVE",
    }
    with patch("core.war_account.war_account_enabled", return_value=True):
        with patch("core.war_account.load_state", return_value=state):
            with patch("core.war_account.save_state"):
                with patch("core.war_account._roll_session"):
                    with patch("core.war_account._append_ledger"):
                        with patch("core.war_account.apply_slippage_overlay", side_effect=lambda *a, **k: (k.get("quote", a[3] if len(a) > 3 else 0), 0.0)):
                            record_entry(
                                cfg, ticker="TZA", shares=50,
                                ib_fill=10.0, quote=10.0,
                            )
    assert "BITO" in state["open_wars"]
    assert "TZA" in state["open_wars"]
    use_lab, slot = _resolve_open_slot(state, "TZA")
    assert not use_lab
    assert slot["entry"] == 10.0


def test_adopt_ib_recovery_does_not_overdraw_settled():
    cfg = BotConfig()
    state = {
        "nav": 3500.0,
        "operating_capital": 3500.0,
        "cash": 3500.0,
        "settled_cash": 3500.0,
        "deployed_usd": 0.0,
        "open_wars": {},
        "open_war": None,
        "open_labs": {},
        "open_lab": None,
        "mode": "WAR_ACTIVE",
        "session_date": "2026-06-30",
    }
    with patch("core.war_account.war_account_enabled", return_value=True):
        with patch("core.war_account.load_state", return_value=state):
            with patch("core.war_account.save_state"):
                with patch("core.war_account._roll_session"):
                    with patch("core.war_account._append_ledger"):
                        with patch(
                            "core.war_account.apply_slippage_overlay",
                            side_effect=lambda *a, **k: (k.get("quote", 10.0), 0.0),
                        ):
                            row = adopt_war_ib_recovery(
                                cfg, ticker="INTC", shares=2, ib_fill=14.0, quote=14.0,
                            )
    assert row.get("event") == "war_ib_recover"
    assert float(state["settled_cash"]) >= 0
    assert "INTC" in state["open_wars"]
    assert float(state["deployed_usd"]) == 20.0


def test_adopt_ib_recovery_skips_oversized_position():
    cfg = BotConfig()
    state = {
        "nav": 3500.0,
        "operating_capital": 3500.0,
        "cash": 3500.0,
        "settled_cash": 3500.0,
        "deployed_usd": 0.0,
        "open_wars": {},
        "open_war": None,
        "open_labs": {},
        "open_lab": None,
        "mode": "WAR_ACTIVE",
        "session_date": "2026-06-30",
    }
    with patch("core.war_account.war_account_enabled", return_value=True):
        with patch("core.war_account.load_state", return_value=state):
            with patch("core.war_account.save_state"):
                with patch("core.war_account._roll_session"):
                    with patch("core.war_account._append_ledger"):
                        with patch(
                            "core.war_account.apply_slippage_overlay",
                            side_effect=lambda *a, **k: (k.get("quote", 21.0), 0.0),
                        ):
                            row = adopt_war_ib_recovery(
                                cfg, ticker="T", shares=347, ib_fill=21.0, quote=21.0,
                            )
    assert row.get("skipped") is True
    assert "T" not in state["open_wars"]
    assert float(state["settled_cash"]) == 3500.0


def test_reconcile_heals_negative_settled():
    cfg = BotConfig()
    state = {
        "nav": 3500.0,
        "operating_capital": 3500.0,
        "cash": -3993.0,
        "settled_cash": -3993.0,
        "deployed_usd": 7489.0,
        "open_wars": {
            "T": {"ticker": "T", "shares": 347, "entry": 20.77},
            "INTC": {"ticker": "INTC", "shares": 2, "entry": 14.0},
        },
        "open_labs": {},
    }
    _reconcile_war_cash_from_positions(state, cfg)
    assert float(state["settled_cash"]) == 0.0
    assert float(state["cash"]) == 0.0
