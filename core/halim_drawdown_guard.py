#!/usr/bin/env python3
"""
core/halim_drawdown_guard.py — Rolling P&L tracker from IB truth + auto rollback.

IB is the single source of truth for P&L. The guard reads session P&L
from the cached IB truth snapshot — no local trade tracking.

Design:
  - Reads realized_pnl + unrealized_pnl from ib_truth snapshot periodically
  - Tracks peak day PnL; computes drawdown as peak-to-current decline
  - If drawdown exceeds DRAWDOWN_THRESHOLD, triggers auto-rollback of
    all self-tune overrides
  - Journals to models/drawdown_guard_journal.jsonl
  - No local trade accounting — IB Gateway does the math
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

GUARD_JOURNAL = Path("models/drawdown_guard_journal.jsonl")

# ── Runtime state ─────────────────────────────────────────────────────────

_peak_pnl: float = 0.0
_in_drawdown: bool = False
_rollback_count: int = 0
_last_check: float = 0.0


def guard_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("DRAWDOWN_GUARD_ENABLED", "true").lower() in ("1", "true", "yes")


def drawdown_threshold(cfg: Optional[BotConfig] = None) -> float:
    return float(os.getenv("DRAWDOWN_THRESHOLD", "0.15"))  # 15% default


def drawdown_check_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    return float(os.getenv("DRAWDOWN_CHECK_INTERVAL_SEC", "120"))  # every 2 min


def max_rollbacks_per_session(cfg: Optional[BotConfig] = None) -> int:
    return int(os.getenv("DRAWDOWN_MAX_ROLLBACKS", "3"))


def min_trades_ib(cfg: Optional[BotConfig] = None) -> int:
    """Minimum number of IB executions (round trips) before assessing drawdown."""
    return int(os.getenv("DRAWDOWN_MIN_TRIPS", "2"))


# ── IB P&L reading ───────────────────────────────────────────────────────

def _ib_day_pnl() -> Dict[str, float]:
    """
    Read current P&L from IB truth snapshot.

    Returns dict with realized_pnl, unrealized_pnl, total, and trip_count.
    Returns zeros if IB truth is unavailable.
    """
    try:
        from core.ib_truth import get_snapshot
        snap = get_snapshot()
        if not snap or not snap.refreshed_at or snap.refreshed_at <= 0:
            return {"realized": 0.0, "unrealized": 0.0, "total": 0.0, "trips": 0}

        realized = float(snap.account.realized_pnl)
        unrealized = float(snap.account.unrealized_pnl)
        trips = len(snap.round_trips)

        return {
            "realized": round(realized, 2),
            "unrealized": round(unrealized, 2),
            "total": round(realized + unrealized, 2),
            "trips": trips,
        }
    except Exception as exc:
        log.debug(f"Drawdown guard: IB snapshot read error: {exc}")
        return {"realized": 0.0, "unrealized": 0.0, "total": 0.0, "trips": 0}


def _compute_drawdown() -> Dict[str, float]:
    """
    Compute current drawdown from IB P&L.

    Returns dict with drawdown (fraction 0-1), current_pnl, peak_pnl, trips.
    """
    global _peak_pnl
    pnl = _ib_day_pnl()
    total = pnl["total"]

    # Update peak (only on positive P&L — don't track losses as peak)
    if total > _peak_pnl:
        _peak_pnl = total

    if _peak_pnl <= 0:
        return {**pnl, "drawdown": 0.0, "peak_pnl": _peak_pnl}

    dd = max(0.0, (_peak_pnl - total) / max(_peak_pnl, 0.01))
    return {**pnl, "drawdown": round(dd, 4), "peak_pnl": round(_peak_pnl, 2)}


def _journal(event: str, detail: Dict[str, Any]) -> None:
    try:
        GUARD_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **detail,
        }
        with open(GUARD_JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"Drawdown journal: {exc}")


def _reset() -> None:
    """Reset peak tracking."""
    global _peak_pnl, _in_drawdown
    _peak_pnl = 0.0
    _in_drawdown = False


# ── Drawdown check ───────────────────────────────────────────────────────

def check_drawdown(cfg: BotConfig) -> Dict[str, Any]:
    """
    Check current drawdown from IB P&L and trigger rollback if threshold exceeded.

    Returns status dict. Throttled internally.
    """
    global _in_drawdown, _rollback_count, _last_check

    if not guard_enabled(cfg):
        return {"ok": False, "reason": "disabled"}

    now = time.time()
    if now - _last_check < drawdown_check_interval_sec(cfg):
        return {"ok": False, "reason": "too_soon"}
    _last_check = now

    state = _compute_drawdown()
    trips = state.get("trips", 0)

    if trips < min_trades_ib(cfg):
        return {"ok": True, "reason": "insufficient_trips", **state}

    if _rollback_count >= max_rollbacks_per_session(cfg):
        return {"ok": True, "rollback": False, "reason": "max_rollbacks_reached", **state}

    dd = state.get("drawdown", 0.0)
    threshold = drawdown_threshold(cfg)

    if dd <= threshold:
        if _in_drawdown:
            _in_drawdown = False
            log.info(
                f"📈 Drawdown recovered: {dd:.1%} (below {threshold:.0%}) "
                f"pnl=${state.get('total', 0):+.2f} trips={trips}"
            )
        return {"ok": True, "rollback": False, "reason": "below_threshold", **state}

    # ── Drawdown exceeded threshold → rollback ────────────────────────
    _in_drawdown = True
    _rollback_count += 1

    try:
        from core.halim_self_tune import current_overrides as _co
        overrides_before = _co()
    except Exception:
        overrides_before = {}

    # Revert all self-tune overrides
    try:
        from core.halim_self_tune import clear_overrides as _clear
        _clear(cfg)
    except Exception:
        pass

    log.warning(
        f"🛑 Drawdown guard: {dd:.1%} exceeds {threshold:.0%} "
        f"(pnl=${state.get('total', 0):+.2f}, peak=${state.get('peak_pnl', 0):+.2f}) — "
        f"reverted overrides: {overrides_before or 'none'} "
        f"(rollback #{_rollback_count})"
    )
    _journal("rollback", {
        "drawdown": dd,
        "threshold": threshold,
        "current_pnl": state.get("total", 0),
        "peak_pnl": state.get("peak_pnl", 0),
        "trips": trips,
        "overrides_before": overrides_before,
        "rollback_number": _rollback_count,
    })

    # Reset peak tracking after rollback (fresh start)
    _reset()

    # Also fire a code review on rollback
    try:
        from core.halim_code_review import request_review as _rr
        _rr(
            f"Drawdown rollback triggered. "
            f"IB PnL=${state.get('total', 0):+.2f} from peak=${state.get('peak_pnl', 0):+.2f}"
        )
    except Exception:
        pass

    # Record the IB error pattern for Halim overseer
    try:
        from core.halim_overseer import record_event as _re
        _re("drawdown_rollback", f"dd={dd:.1%} pnl=${state.get('total', 0):+.2f}")
    except Exception:
        pass

    return {"ok": True, "rollback": True, "reason": "drawdown_exceeded", **state}


def drawdown_value() -> float:
    """Current drawdown as fraction (0-1). Queries IB truth."""
    state = _compute_drawdown()
    return state.get("drawdown", 0.0)


def pnl_status() -> Dict[str, float]:
    """Current IB P&L snapshot for logging."""
    return _compute_drawdown()


def status_line(cfg: BotConfig) -> str:
    """Brief status for logging."""
    state = _compute_drawdown()
    thr = drawdown_threshold(cfg)
    rc = _rollback_count
    return (
        f"IB PnL=${state.get('total', 0):+.2f} "
        f"peak=${state.get('peak_pnl', 0):+.2f} "
        f"dd={state.get('drawdown', 0):.1%}/{thr:.0%} "
        f"trips={state.get('trips', 0)} "
        f"rollbacks={rc}"
    )
