"""Trade horizon gates — scalp live, swing shadow, maturity."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from core.config import BotConfig
from core.ib_truth import IBAccountSnapshot, IBTruthSnapshot
from core.trade_horizon import (
    HORIZON_SCALP,
    HORIZON_SWING,
    active_order_horizon,
    scalp_profit_gate_passed,
    swing_shadow_enabled,
    tag_record,
)


def test_active_order_horizon_is_scalp():
    with patch.dict("os.environ", {"CAPITAL_PHASES_ENABLED": "false"}, clear=False):
        assert active_order_horizon(BotConfig()) == HORIZON_SCALP


def test_tag_record_defaults_scalp():
    with patch.dict("os.environ", {"CAPITAL_PHASES_ENABLED": "false"}, clear=False):
        row = tag_record({"symbol": "SPY"})
        assert row["horizon"] == HORIZON_SCALP


def test_tag_record_swing():
    row = tag_record({"symbol": "QQQ"}, HORIZON_SWING)
    assert row["horizon"] == HORIZON_SWING


def test_scalp_gate_force_pass():
    with patch.dict("os.environ", {"SCALP_PROFIT_GATE_FORCE": "pass"}):
        assert scalp_profit_gate_passed(BotConfig()) is True


def test_swing_shadow_child_stage():
    with patch("core.brain_maturity.compute_stage", return_value="child"):
        with patch.dict("os.environ", {"SWING_SHADOW_ENABLED": "true"}):
            assert swing_shadow_enabled(BotConfig()) is True


def test_ib_truth_open_orders_field():
    from core.ib_truth import day_pnl_from_snapshot

    snap = IBTruthSnapshot(
        account=IBAccountSnapshot(net_liquidation=985000, realized_pnl=-12.5),
        session_pnl_ib=-12.5,
        session_pnl_fifo=-99.0,
        refreshed_at=1.0,
        open_orders=[],
    )
    pnl, _ = day_pnl_from_snapshot(snap, ib_start=985012.5)
    assert pnl == -12.5
