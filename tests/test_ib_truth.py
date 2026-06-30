"""IB Truth — FIFO round trips, ghost exit guard, war capital alignment."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.config import BotConfig
from core.ib_truth import (
    IBExecution,
    IBTruthSnapshot,
    fifo_round_trips,
    day_pnl_from_snapshot,
    IBAccountSnapshot,
)
from core.war_account import record_exit


def test_fifo_round_trip_pnl():
    execs = [
        IBExecution("TZA", "BOT", 3.79, 770, ts=1000.0, commission=1.0),
        IBExecution("TZA", "SLD", 3.76, 770, ts=2000.0, commission=1.0),
    ]
    trips = fifo_round_trips(execs)
    assert len(trips) == 1
    assert abs(trips[0].pnl_usd - (-23.1)) < 30.0  # ~-$23 not -$3212


def test_day_pnl_prefers_fifo():
    snap = IBTruthSnapshot(
        account=IBAccountSnapshot(net_liquidation=985000),
        session_pnl_fifo=-27.5,
        round_trips=[MagicMock()],
        refreshed_at=1.0,
    )
    pnl, _ = day_pnl_from_snapshot(snap, ib_start=985027.5)
    assert pnl == -27.5


def test_ghost_exit_skips_bogus_pnl():
    cfg = BotConfig()
    state = {
        "nav": 1000.0,
        "cash": 1000.0,
        "settled_cash": 1000.0,
        "deployed_usd": 0.0,
        "open_wars": {},
        "open_labs": {},
        "mode": "WAR_ACTIVE",
    }
    with patch("core.war_account.war_account_enabled", return_value=True):
        with patch("core.war_account.load_state", return_value=state):
            with patch("core.war_account.save_state"):
                with patch("core.war_account._append_ledger"):
                    with patch(
                        "core.war_account.apply_slippage_overlay",
                        side_effect=lambda *a, **k: (k.get("quote", 3.76), 0.0),
                    ):
                        row = record_exit(
                            cfg,
                            ticker="TZA",
                            shares=770,
                            ib_fill=3.76,
                            quote=3.76,
                            pnl_usd_ib=-3212.34,
                            entry_ib_fill=0.0,
                            exit_reason="stop",
                        )
    assert row.get("skipped") is True
    assert state.get("session_pnl_war", 0) == 0


def test_execution_in_rth_window():
    from datetime import datetime
    from core.market_hours import MARKET_TZ
    from core.rth_session import execution_in_rth_window, rth_session_start_ts

    # 10:00 ET today — inside RTH
    now = datetime.now(MARKET_TZ)
    inside = now.replace(hour=10, minute=0, second=0, microsecond=0).timestamp()
    assert execution_in_rth_window(inside) is True

    # 08:00 ET — premarket, outside RTH window
    pre = now.replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
    assert execution_in_rth_window(pre) is False

    start = rth_session_start_ts()
    assert start > 0


def test_filter_rth_executions():
    from core.ib_truth import IBExecution, filter_rth_executions
    from core.market_hours import MARKET_TZ
    from datetime import datetime

    now = datetime.now(MARKET_TZ)
    rth_ts = now.replace(hour=10, minute=0, second=0, microsecond=0).timestamp()
    pre_ts = now.replace(hour=7, minute=0, second=0, microsecond=0).timestamp()
    execs = [
        IBExecution("T", "BOT", 20.0, 10, ts=rth_ts),
        IBExecution("T", "SLD", 20.5, 10, ts=pre_ts),
    ]
    filtered = filter_rth_executions(execs)
    assert len(filtered) == 1
    assert filtered[0].side == "BOT"

    cfg = BotConfig()
    with patch.dict("os.environ", {"WAR_CAPITAL_USD": "1000"}, clear=False):
        from core.war_account import operating_capital_usd
        assert operating_capital_usd(cfg) == 1000.0
