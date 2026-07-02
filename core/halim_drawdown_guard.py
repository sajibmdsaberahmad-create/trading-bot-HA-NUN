#!/usr/bin/env python3
"""
core/halim_drawdown_guard.py — Rolling P&L tracker + automatic parameter rollback.

Covers Steps 2 and 5 from the roadmap:
  Step 2: Validate changes via real trade outcomes (not replay).
          When a self-tune change is made, subsequent trades validate it.
  Step 5: Auto-rollback on drawdown. If rolling P&L drops below threshold,
          all self-tune overrides are reverted to defaults.

Design:
  - Trades tracked in a bounded deque (last N = 50)
  - Drawdown computed as: peak_total - current_total / peak_total
  - If drawdown exceeds DD_THRESHOLD (default 15%), trigger rollback
  - Rollback: clear ALL self-tune overrides, reset P&L tracking
  - Every trade close calls record_trade(pnl, ticker)
  - Main loop calls check_drawdown(cfg) periodically
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

GUARD_JOURNAL = Path("models/drawdown_guard_journal.jsonl")

# ── Runtime state ─────────────────────────────────────────────────────────

_trades: deque = deque(maxlen=50)
_trades_lock = threading.Lock()
_peak_total: float = 0.0
_in_drawdown: bool = False
_rollback_count: int = 0
_last_check: float = 0.0


def guard_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("DRAWDOWN_GUARD_ENABLED", "true").lower() in ("1", "true", "yes")


def drawdown_threshold(cfg: Optional[BotConfig] = None) -> float:
    return float(os.getenv("DRAWDOWN_THRESHOLD", "0.15"))  # 15% default


def drawdown_check_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    return float(os.getenv("DRAWDOWN_CHECK_INTERVAL_SEC", "120"))  # every 2 min


def drawdown_cutoff_trades(cfg: Optional[BotConfig] = None) -> int:
    return int(os.getenv("DRAWDOWN_CUTOFF_TRADES", "5"))  # need at least 5 trades to assess


def max_rollbacks_per_session(cfg: Optional[BotConfig] = None) -> int:
    return int(os.getenv("DRAWDOWN_MAX_ROLLBACKS", "3"))


# ── P&L tracking ─────────────────────────────────────────────────────────

def record_trade(pnl: float, ticker: str = "") -> None:
    """Record a completed trade's P&L. Thread-safe."""
    global _peak_total
    with _trades_lock:
        _trades.append({
            "pnl": round(pnl, 2),
            "ticker": ticker.upper() if ticker else "?",
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        # Update peak total (sum of all trades in window)
        total = sum(t["pnl"] for t in _trades)
        if total > _peak_total:
            _peak_total = total


def _compute_drawdown() -> float:
    """Compute current drawdown relative to peak. Returns 0 if no peak."""
    global _peak_total
    with _trades_lock:
        if not _trades or _peak_total <= 0:
            return 0.0
        current_total = sum(t["pnl"] for t in _trades)
        if _peak_total <= 0:
            return 0.0
        return max(0.0, (_peak_total - current_total) / _peak_total)


def _current_total() -> float:
    with _trades_lock:
        return sum(t["pnl"] for t in _trades)


def _trade_count() -> int:
    with _trades_lock:
        return len(_trades)


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
    """Reset all tracking state."""
    global _peak_total, _in_drawdown, _rollback_count
    with _trades_lock:
        _trades.clear()
        _peak_total = 0.0
        _in_drawdown = False


# ── Drawdown check ───────────────────────────────────────────────────────

def check_drawdown(cfg: BotConfig) -> Dict[str, Any]:
    """
    Check current drawdown and trigger rollback if threshold exceeded.

    Returns status dict. Safe to call any time — throttles internally.
    """
    global _in_drawdown, _rollback_count, _last_check

    if not guard_enabled(cfg):
        return {"ok": False, "reason": "disabled"}

    now = time.time()
    if now - _last_check < drawdown_check_interval_sec(cfg):
        return {"ok": False, "reason": "too_soon"}
    _last_check = now

    if _trade_count() < drawdown_cutoff_trades(cfg):
        return {"ok": False, "reason": "insufficient_trades"}

    if _rollback_count >= max_rollbacks_per_session(cfg):
        return {"ok": True, "drawdown": _compute_drawdown(),
                "rollback": False, "reason": "max_rollbacks_reached"}

    dd = _compute_drawdown()
    threshold = drawdown_threshold(cfg)

    if dd <= threshold:
        if _in_drawdown:
            _in_drawdown = False
            log.info(f"📈 Drawdown recovered: {dd:.1%} (below {threshold:.0%})")
        return {"ok": True, "drawdown": dd, "rollback": False, "reason": "below_threshold"}

    # ── Drawdown exceeded threshold → rollback ────────────────────────
    _in_drawdown = True
    _rollback_count += 1

    try:
        from core.halim_self_tune import current_overrides
        overrides_before = current_overrides()
    except Exception:
        overrides_before = {}

    # Revert all self-tune overrides by clearing them
    try:
        from core.halim_self_tune import clear_overrides
        clear_overrides(cfg)
    except Exception:
        pass

    log.warning(
        f"🛑 Drawdown guard: {dd:.1%} exceeds {threshold:.0%} — "
        f"reverted overrides: {overrides_before or 'none'} "
        f"(rollback #{_rollback_count})"
    )
    _journal("rollback", {
        "drawdown": round(dd, 4),
        "threshold": round(threshold, 4),
        "overrides_before": overrides_before,
        "rollback_number": _rollback_count,
        "trades_in_window": _trade_count(),
        "total_pnl": round(_current_total(), 2),
    })

    # Reset P&L tracking after rollback (fresh start)
    _reset()

    return {"ok": True, "drawdown": dd, "rollback": True, "reason": "drawdown_exceeded"}


def status_line(cfg: BotConfig) -> str:
    """Brief status for logging."""
    dd = _compute_drawdown()
    thr = drawdown_threshold(cfg)
    n = _trade_count()
    rc = _rollback_count
    return f"drawdown={dd:.1%}/{thr:.0%} trades={n} rollbacks={rc}"


def drawdown_value() -> float:
    """Current drawdown as fraction (0-1)."""
    return _compute_drawdown()
