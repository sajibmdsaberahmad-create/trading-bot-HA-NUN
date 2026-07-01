"""War IB sync — preserve active war slots across oversize notional gate."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.config import BotConfig
from core.ib_truth import IBPosition, IBTruthSnapshot
from core.war_ib_sync import sync_war_positions_from_ib


def test_sync_preserves_oversize_war_slot():
    """BITO-style: ~95% nav deployment must not drop open_wars on periodic sync."""
    cfg = BotConfig()
    state = {
        "nav": 930.0,
        "operating_capital": 1000.0,
        "cash": 46.0,
        "settled_cash": 46.0,
        "deployed_usd": 884.0,
        "open_wars": {
            "BITO": {
                "ticker": "BITO",
                "shares": 109,
                "entry": 8.10,
                "ib_fill": 8.10,
                "comm": 0.44,
                "pipeline": "war_entry",
                "ts": 1.0,
            },
        },
        "open_war": {"ticker": "BITO", "shares": 109, "entry": 8.10},
        "open_labs": {},
    }
    snap = IBTruthSnapshot(refreshed_at=1.0)
    snap.positions = [IBPosition(symbol="BITO", qty=109, avg_cost=8.10)]

    ib = MagicMock()
    with patch("core.war_account._reconcile_war_cash_from_positions"):
        result = sync_war_positions_from_ib(ib, cfg, state=state, snap=snap)

    assert "BITO" in state["open_wars"]
    assert state["open_wars"]["BITO"]["entry"] == 8.10
    assert state["open_wars"]["BITO"]["shares"] == 109
    assert result["war_slots"] == 1
    assert "BITO" in result["oversize_kept"]
    assert "BITO" not in result["dropped"]


def test_sync_skips_oversize_ib_only_adoption():
    cfg = BotConfig()
    state = {
        "nav": 930.0,
        "operating_capital": 1000.0,
        "cash": 930.0,
        "settled_cash": 930.0,
        "deployed_usd": 0.0,
        "open_wars": {},
        "open_war": None,
        "open_labs": {},
    }
    snap = IBTruthSnapshot(refreshed_at=1.0)
    snap.positions = [IBPosition(symbol="BITO", qty=109, avg_cost=8.10)]

    ib = MagicMock()
    with patch("core.war_account._reconcile_war_cash_from_positions"):
        result = sync_war_positions_from_ib(ib, cfg, state=state, snap=snap)

    assert "BITO" not in state["open_wars"]
    assert result["war_slots"] == 0
    assert "BITO" in result["monitor_only"]
