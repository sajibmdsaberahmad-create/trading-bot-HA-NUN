#!/usr/bin/env python3
"""
core/swing_executor.py — Live IB swing entries (multi-day GTC brackets).

Runs in capital phases premarket_full + rth_full. Economics from IB Truth only;
local state is tags + learning metadata (no virtual NAV).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.capital_phase import allows_horizon_live, capital_phase
from core.horizon_tags import tag_learning_row
from core.notify import log
from core.trade_horizon import HORIZON_SWING, swing_ib_live_enabled

if TYPE_CHECKING:
    from core.config import BotConfig

LEDGER_PATH = Path(__file__).resolve().parent.parent / "models" / "swing_ib_ledger.jsonl"
SCAN_INTERVAL = float(os.getenv("SWING_IB_SCAN_INTERVAL_SEC", "600"))


def _swing_stop_target_pct(cfg: Optional["BotConfig"] = None) -> tuple[float, float]:
    stop = float(os.getenv("SWING_STOP_PCT", "0.04"))
    target = float(os.getenv("SWING_TARGET_PCT", "0.08"))
    return stop, target


def _max_swing_positions(cfg: Optional["BotConfig"] = None) -> int:
    return int(os.getenv("SWING_IB_MAX_POSITIONS", "3"))


def _min_signal_strength() -> float:
    return float(os.getenv("SWING_IB_MIN_STRENGTH", "0.35"))


def swing_slots(runner: Any) -> Dict[str, Dict[str, Any]]:
    slots = getattr(runner, "_position_slots", {}) or {}
    return {
        t: s for t, s in slots.items()
        if str(s.get("horizon", "scalp")) == HORIZON_SWING
    }


def ticker_held_as_swing(runner: Any, ticker: str) -> bool:
    sym = (ticker or "").upper()
    slot = (getattr(runner, "_position_slots", {}) or {}).get(sym)
    return bool(slot and str(slot.get("horizon", "scalp")) == HORIZON_SWING and float(slot.get("shares", 0) or 0) > 0)


def scalp_blocked_by_swing(runner: Any, ticker: str) -> bool:
    """Scalp must not stack on an open swing line for the same symbol."""
    return ticker_held_as_swing(runner, ticker)


def _append_ledger(row: Dict[str, Any]) -> None:
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"swing ib ledger: {exc}")


def _symbols_for_swing(runner: Any, cfg: Optional["BotConfig"]) -> List[str]:
    from core.swing_shadow import _symbols_for_scan
    return _symbols_for_scan(runner, cfg)


def _swing_signal(runner: Any, sym: str) -> Dict[str, Any]:
    from core.swing_shadow import _simple_swing_signal
    dm = getattr(runner, "data_manager", None)
    if dm is None:
        return {"bias": "hold", "strength": 0.0}
    try:
        bars = dm.get_bars(sym, "1 hour", duration="5 D")
    except Exception:
        bars = []
    return _simple_swing_signal(bars or [])


def _size_swing_shares(runner: Any, cfg: "BotConfig", px: float) -> int:
    from core.pilot_mode import get_ai_deploy_budget
    budget = get_ai_deploy_budget(
        cfg,
        pilot=getattr(runner, "pilot", None),
        account_equity=float(getattr(runner, "account_equity", 0) or 0),
        available_cash=float(getattr(runner, "available_cash", 0) or 0),
        open_positions=len(swing_slots(runner)),
    )
    max_pct = float(os.getenv("SWING_IB_MAX_DEPLOY_PCT", "0.15"))
    eq = float(getattr(runner, "account_equity", 0) or 0)
    if eq > 0 and max_pct > 0:
        budget = min(budget, eq * max_pct)
    if px <= 0 or budget <= 0:
        return 0
    return max(0, int(budget / px))


def try_swing_ib_entry(
    runner: Any,
    cfg: Optional["BotConfig"],
    sym: str,
    signal: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Place one IB swing bracket if signal + phase allow."""
    if not swing_ib_live_enabled(cfg, capital_phase(cfg, runner)):
        return None
    if not allows_horizon_live(HORIZON_SWING, cfg, runner):
        return None
    sym = sym.upper()
    if signal.get("bias") != "long":
        return None
    if float(signal.get("strength", 0) or 0) < _min_signal_strength():
        return None
    if ticker_held_as_swing(runner, sym):
        return None
    if len(swing_slots(runner)) >= _max_swing_positions(cfg):
        return None

    px = float(runner._live_price_for(sym, 0) or 0)
    if px <= 0:
        return None
    shares = _size_swing_shares(runner, cfg, px)
    if shares < 1:
        return None

    stop_pct, tgt_pct = _swing_stop_target_pct(cfg)
    stop_px = px * (1.0 - stop_pct)
    tgt_px = px * (1.0 + tgt_pct)
    phase = capital_phase(cfg, runner)

    try:
        bracket = runner.broker.place_bracket_buy(
            shares,
            px,
            stop_px,
            tgt_px,
            symbol=sym,
            horizon=HORIZON_SWING,
            capital_phase=phase,
            pipeline=str(signal.get("reason", "swing_ib"))[:24],
        )
    except Exception as exc:
        log.debug(f"swing IB entry {sym}: {exc}")
        return None

    now = time.time()
    slot = {
        "shares": float(shares),
        "session_shares": float(shares),
        "entry_price": px,
        "entry_fill_px": px,
        "horizon": HORIZON_SWING,
        "capital_phase": phase,
        "stop": stop_px,
        "target": tgt_px,
        "opened_at": now,
        "ib_fill_confirmed": False,
        "swing_signal": signal.get("reason", ""),
    }
    runner._position_slots[sym] = slot

    row = tag_learning_row(
        {
            "event": "swing_ib_entry",
            "symbol": sym,
            "shares": shares,
            "entry_px": round(px, 4),
            "stop": round(stop_px, 4),
            "target": round(tgt_px, 4),
            "phase": phase,
            "order_id": getattr(bracket, "parent_order_id", 0),
            "ts": now,
        },
        horizon=HORIZON_SWING,
        capital_phase=phase,
        pipeline=str(signal.get("reason", "")),
    )
    _append_ledger(row)
    log.info(
        f"  📈 SWING IB ENTRY {sym}: {shares}sh @ ${px:.4f} "
        f"stop ${stop_px:.4f} tgt ${tgt_px:.4f} [{phase}]"
    )
    return row


def run_swing_ib_cycle(
    runner: Any,
    cfg: Optional["BotConfig"] = None,
    *,
    force: bool = False,
) -> int:
    """Scan + attempt swing IB entries; returns entries placed."""
    cfg = cfg or getattr(runner, "cfg", None)
    if not swing_ib_live_enabled(cfg, capital_phase(cfg, runner)):
        return 0
    now = time.time()
    last = float(getattr(runner, "_last_swing_ib_scan", 0) or 0)
    if not force and now - last < SCAN_INTERVAL:
        return 0
    runner._last_swing_ib_scan = now

    placed = 0
    for sym in _symbols_for_swing(runner, cfg):
        signal = _swing_signal(runner, sym)
        if try_swing_ib_entry(runner, cfg, sym, signal):
            placed += 1
    return placed


def monitor_swing_ib_slots(runner: Any, cfg: Optional["BotConfig"] = None) -> None:
    """Refresh swing slot marks from IB Truth (no local PnL math)."""
    from core.ib_truth import get_snapshot
    snap = get_snapshot()
    if snap.refreshed_at <= 0:
        return
    for sym, slot in swing_slots(runner).items():
        pos = snap.long_positions().get(sym)
        if not pos:
            continue
        if pos.market_price > 0:
            slot["mark_px"] = pos.market_price
        if pos.avg_cost > 0:
            slot["entry_fill_px"] = pos.avg_cost
            slot["entry_price"] = pos.avg_cost
        slot["ib_unrealized"] = pos.unrealized_pnl
        slot["ib_fill_confirmed"] = pos.qty > 0
