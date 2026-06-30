#!/usr/bin/env python3
"""
core/position_sync.py — IB-grounded multi-position slot sync (extracted from scalper_runner).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Set

from core.fill_tracker import position_avg_cost, position_entry_price
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
        return
    resolved = position_entry_price(ib, ticker, market_px=live_price)
    if resolved > 0 and 0.85 <= (resolved / live_price) <= 1.15:
        log.warning(
            f"  🔧 Entry price repair {ticker}: ${entry:.4f} → ${resolved:.4f} "
            f"(live ${live_price:.4f})"
        )
        slot["entry_price"] = resolved
        slot["entry_fill_px"] = resolved


def ib_long_position_map(ib) -> Dict[str, float]:
    try:
        from core.ib_truth import get_snapshot, ib_truth_enabled
        snap = get_snapshot()
        if ib_truth_enabled() and snap.refreshed_at > 0:
            return {sym: pos.qty for sym, pos in snap.long_positions().items()}
    except Exception:
        pass
    out: Dict[str, float] = {}
    for p in ib.positions():
        sym = getattr(p.contract, "symbol", "")
        pos = float(p.position)
        if pos > 0:
            out[sym] = pos
    return out


def adopt_ib_positions_into_slots(
    ib,
    position_slots: Dict[str, Dict[str, Any]],
    *,
    exclude_tickers: Optional[Set[str]] = None,
) -> list[str]:
    """Recover IB long holdings into position_slots so monitor/AI can manage after restart."""
    adopted: list[str] = []
    skip = {str(t).upper() for t in (exclude_tickers or set())}
    ib_map = ib_long_position_map(ib)
    now = time.time()
    for ticker, sh in ib_map.items():
        if sh <= 0:
            continue
        t = ticker.upper()
        if t in skip:
            continue
        resolved = position_entry_price(ib, t)
        entry = resolved if resolved > 0 else 0.0
        slot = position_slots.get(t)
        if slot and float(slot.get("shares", 0) or 0) > 0:
            if entry > 0:
                slot["entry_fill_px"] = entry
                slot["entry_price"] = entry
                slot["ib_fill_confirmed"] = True
            slot["shares"] = float(sh)
            continue
        stop = entry * 0.995 if entry > 0 else 0.0
        target = entry * 1.015 if entry > 0 else 0.0
        position_slots[t] = {
            "shares": float(sh),
            "session_shares": float(sh),
            "entry_price": entry,
            "entry_fill_px": entry,
            "ib_fill_confirmed": entry > 0,
            "stop": stop,
            "target": target,
            "peak": entry or 0.0,
            "hard_floor": stop,
            "opened_at": now,
            "prev_shares": float(sh),
            "last_pulse_price": entry,
            "last_price_change_at": now,
            "last_price_snapshot_at": 0.0,
            "last_pulse_fingerprint": "",
            "last_position_pulse": 0.0,
            "last_ai_position_manage": 0.0,
            "last_stagnation_decision": {},
            "recovered_from_ib": True,
        }
        adopted.append(t)
        log.info(
            f"  📎 Recovered IB position {t}: {sh:.0f}sh @ ${entry:.4f} — live monitor armed"
        )
    return adopted


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
                resolved = position_entry_price(ib, ticker)
                if resolved > 0:
                    slot["entry_fill_px"] = resolved
                    slot["entry_price"] = resolved
        else:
            opened = float(slot.get("opened_at", 0))
            if opened and (now - opened) < grace_sec:
                continue
            slot["shares"] = 0.0
