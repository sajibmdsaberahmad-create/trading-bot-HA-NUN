#!/usr/bin/env python3
"""
core/swing_paper.py — Virtual swing paper pool (no IB orders).

Uses IB Truth marks for PnL; logs to models/swing_paper_ledger.jsonl.
Enabled when SWING_PAPER_ENABLED=true + teen+ maturity + scalp gate.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.notify import log
from core.ib_truth import get_snapshot
from core.trade_horizon import HORIZON_SWING, swing_paper_enabled, tag_record

if TYPE_CHECKING:
    from core.config import BotConfig

STATE_PATH = Path(__file__).resolve().parent.parent / "models" / "swing_paper_state.json"
LEDGER_PATH = Path(__file__).resolve().parent.parent / "models" / "swing_paper_ledger.jsonl"


def swing_paper_capital_usd(cfg: Optional["BotConfig"] = None) -> float:
    return float(os.getenv("WAR_SWING_PAPER_USD", "2000"))


def _load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_swing_paper_state(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    cap = swing_paper_capital_usd(cfg)
    state = _load_state()
    if not state:
        state = {
            "capital": cap,
            "nav": cap,
            "cash": cap,
            "open_positions": {},
            "session_pnl": 0.0,
            "updated_at": time.time(),
        }
        _save_state(state)
    return state


def swing_paper_context(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    if not swing_paper_enabled(cfg):
        return {"swing_paper_enabled": False}
    st = ensure_swing_paper_state(cfg)
    snap = get_snapshot()
    unreal = 0.0
    for sym, slot in (st.get("open_positions") or {}).items():
        pos = next((p for p in snap.positions if p.symbol == sym), None)
        if pos and pos.market_price > 0:
            entry = float(slot.get("entry", 0) or 0)
            sh = float(slot.get("shares", 0) or 0)
            unreal += (pos.market_price - entry) * sh
    return {
        "swing_paper_enabled": True,
        "swing_paper_nav": round(float(st.get("nav", 0) or 0) + unreal, 2),
        "swing_paper_capital": swing_paper_capital_usd(cfg),
        "swing_paper_open": list((st.get("open_positions") or {}).keys()),
        "swing_paper_session_pnl": round(float(st.get("session_pnl", 0) or 0) + unreal, 2),
        "horizon": HORIZON_SWING,
    }


def record_swing_paper_shadow_entry(
    cfg: Optional["BotConfig"],
    symbol: str,
    shares: int,
    *,
    bias: str = "long",
    reason: str = "",
) -> Optional[Dict[str, Any]]:
    """Virtual swing entry at IB mark — no order."""
    if not swing_paper_enabled(cfg) or shares <= 0:
        return None
    sym = symbol.upper()
    snap = get_snapshot()
    pos = next((p for p in snap.positions if p.symbol == sym), None)
    mark = pos.market_price if pos and pos.market_price > 0 else 0.0
    if mark <= 0:
        return None
    st = ensure_swing_paper_state(cfg)
    cost = mark * shares
    if cost > float(st.get("cash", 0) or 0):
        return None
    st["cash"] = round(float(st["cash"]) - cost, 2)
    st["open_positions"][sym] = {
        "shares": shares,
        "entry": mark,
        "bias": bias,
        "ts": time.time(),
    }
    st["updated_at"] = time.time()
    _save_state(st)
    row = tag_record({
        "event": "swing_paper_entry",
        "symbol": sym,
        "shares": shares,
        "ib_mark": mark,
        "reason": reason[:120],
        "ts": time.time(),
        "shadow_only": True,
    }, HORIZON_SWING)
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    log.info(f"Swing paper entry (virtual): {shares}x {sym} @ IB ${mark:.2f}")
    return row


def sync_swing_paper_from_shadow_verdicts(
    runner: Any,
    cfg: Optional["BotConfig"] = None,
) -> int:
    """Promote swing shadow long/short verdicts to virtual paper entries."""
    if not swing_paper_enabled(cfg):
        return 0
    path = Path(__file__).resolve().parent.parent / "models" / "swing_shadow_verdicts.jsonl"
    if not path.exists():
        return 0
    st = ensure_swing_paper_state(cfg)
    open_syms = set((st.get("open_positions") or {}).keys())
    count = 0
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()[-20:]
        for line in lines:
            row = json.loads(line)
            sym = str(row.get("symbol", "")).upper()
            verdict = str(row.get("verdict", "")).lower()
            if sym in open_syms or verdict not in ("long", "short"):
                continue
            strength = float(row.get("strength", 0) or 0)
            if strength < float(os.getenv("SWING_PAPER_MIN_STRENGTH", "0.35")):
                continue
            cash = float(st.get("cash", 0) or 0)
            mark = float(row.get("ib_mark", 0) or 0)
            if mark <= 0:
                continue
            deploy = min(cash * 0.25, float(os.getenv("SWING_PAPER_MAX_DEPLOY", "500")))
            shares = int(deploy / mark)
            if shares < 1:
                continue
            if record_swing_paper_shadow_entry(cfg, sym, shares, bias=verdict, reason=row.get("reason", "")):
                open_syms.add(sym)
                count += 1
    except Exception as exc:
        log.debug(f"swing paper sync: {exc}")
    return count
