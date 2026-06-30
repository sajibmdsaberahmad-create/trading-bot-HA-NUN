#!/usr/bin/env python3
"""
core/swing_learning.py — Multi-day swing labels from real IB closes.

Writes models/swing_ib_trips.jsonl for PPO swing training (hold_days from IB).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.horizon_tags import parse_order_ref, tag_learning_row
from core.notify import log
from core.trade_horizon import HORIZON_SWING

if TYPE_CHECKING:
    from core.config import BotConfig

TRIPS_PATH = Path(__file__).resolve().parent.parent / "models" / "swing_ib_trips.jsonl"


def _append_trip(row: Dict[str, Any]) -> None:
    try:
        TRIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRIPS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"swing trip log: {exc}")


def record_swing_ib_close(
    cfg: Optional["BotConfig"],
    *,
    symbol: str,
    shares: float,
    entry_px: float,
    exit_px: float,
    pnl_usd: float,
    opened_at: float,
    closed_at: Optional[float] = None,
    capital_phase: str = "",
    exit_reason: str = "",
    order_ref: str = "",
) -> Dict[str, Any]:
    """Log a completed multi-day (or intraday) swing round trip from IB fills."""
    closed = float(closed_at or time.time())
    opened = float(opened_at or closed)
    hold_sec = max(0.0, closed - opened)
    hold_days = round(hold_sec / 86400.0, 2)
    tags = parse_order_ref(order_ref)
    row = tag_learning_row(
        {
            "event": "swing_ib_close",
            "symbol": symbol.upper(),
            "shares": round(float(shares), 4),
            "entry_px": round(float(entry_px), 4),
            "exit_px": round(float(exit_px), 4),
            "pnl_usd": round(float(pnl_usd), 2),
            "hold_sec": round(hold_sec, 1),
            "hold_days": hold_days,
            "multi_day": hold_days >= 1.0,
            "exit_reason": exit_reason[:80],
            "order_ref": order_ref[:64],
            "opened_at": opened,
            "closed_at": closed,
            "ts": closed,
        },
        horizon=HORIZON_SWING,
        capital_phase=capital_phase or tags.get("capital_phase", ""),
        pipeline=tags.get("pipeline", "swing_close"),
    )
    _append_trip(row)
    log.info(
        f"  📈 SWING IB CLOSE {symbol.upper()}: ${pnl_usd:+.2f} "
        f"hold={hold_days:.1f}d [{exit_reason}]"
    )
    return row


def ingest_ib_swing_round_trips(cfg: Optional["BotConfig"] = None) -> int:
    """Scan IB Truth FIFO trips tagged swing via execution orderRef."""
    from core.ib_truth import get_snapshot
    snap = get_snapshot()
    if snap.refreshed_at <= 0:
        return 0
    logged = 0
    for trip in snap.round_trips:
        ref = str(getattr(trip, "order_ref", "") or "")
        tags = parse_order_ref(ref)
        if tags.get("horizon") != HORIZON_SWING and HORIZON_SWING not in ref:
            continue
        row = record_swing_ib_close(
            cfg,
            symbol=trip.symbol,
            shares=trip.shares,
            entry_px=trip.entry_px,
            exit_px=trip.exit_px,
            pnl_usd=trip.pnl_usd,
            opened_at=trip.entry_ts,
            closed_at=trip.exit_ts,
            capital_phase=tags.get("capital_phase", ""),
            exit_reason="ib_fifo",
            order_ref=ref,
        )
        if row:
            logged += 1
    return logged


def read_swing_trips(*, min_hold_days: float = 0.0) -> List[Dict[str, Any]]:
    if not TRIPS_PATH.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in TRIPS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if float(row.get("hold_days", 0) or 0) >= min_hold_days:
                out.append(row)
        except Exception:
            continue
    return out
