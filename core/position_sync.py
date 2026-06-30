#!/usr/bin/env python3
"""
core/position_sync.py — IB-grounded multi-position slot sync (extracted from scalper_runner).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Set

from core.fill_tracker import position_avg_cost
from core.notify import log


def repair_slot_entry_price(
    ib,
    ticker: str,
    slot: Dict[str, Any],
    live_price: float,
) -> None:
    """Fix cross-ticker contamination — refresh entry from IB if price is implausible."""
    if not slot:
        return
    entry = float(slot.get("entry_price", 0) or 0)
    if entry <= 0 or live_price <= 0:
        return
    ratio = entry / live_price
    if 0.85 <= ratio <= 1.15:
        return
    avg = position_avg_cost(ib, ticker)
    if avg > 0 and 0.85 <= (avg / live_price) <= 1.15:
        log.warning(
            f"  🔧 Entry price repair {ticker}: ${entry:.4f} → ${avg:.4f} "
            f"(live ${live_price:.4f})"
        )
        slot["entry_price"] = avg
        slot["entry_fill_px"] = avg


def ib_long_position_map(ib) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p in ib.positions():
        sym = getattr(p.contract, "symbol", "")
        pos = float(p.position)
        if pos > 0:
            out[sym] = pos
    return out


def sync_position_slots_from_ib(
    ib,
    position_slots: Dict[str, Dict[str, Any]],
    *,
    short_warned: Set[str],
    grace_sec: float = 60.0,
) -> None:
    """Cap slot shares to session size; refresh entry from IB avgCost when confirmed."""
    if not position_slots:
        return
    ib_map = ib_long_position_map(ib)
    for p in ib.positions():
        sym = getattr(p.contract, "symbol", "")
        pos = float(p.position)
        if pos < 0 and sym not in short_warned:
            short_warned.add(sym)
            log.warning(
                f"IB short position {pos:.0f} {sym} "
                f"— long-only scalper ignoring (orphan paper debris)"
            )
    now = time.time()
    for ticker, slot in list(position_slots.items()):
        if ticker in ib_map:
            ib_sh = float(ib_map[ticker])
            session_sh = float(slot.get("session_shares", 0) or slot.get("shares", 0))
            slot["shares"] = min(ib_sh, session_sh) if session_sh > 0 else ib_sh
            if slot.get("ib_fill_confirmed"):
                avg = position_avg_cost(ib, ticker)
                if avg > 0:
                    slot["entry_fill_px"] = avg
                    slot["entry_price"] = avg
        else:
            opened = float(slot.get("opened_at", 0))
            if opened and (now - opened) < grace_sec:
                continue
            slot["shares"] = 0.0
