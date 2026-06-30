#!/usr/bin/env python3
"""
core/war_ib_sync.py — War virtual ledger synced from IB Truth (core/ib_truth.py).

War pool = WAR_CAPITAL_USD ($1k) for sizing; positions/PnL grounded in IB Gateway.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.ib_truth import (
    build_snapshot,
    fifo_round_trips,
    get_snapshot,
    ib_truth_enabled,
    refresh,
    session_start_ts_et,
)
from core.rth_session import ib_truth_session_start_ts
from core.notify import log

_REPO = Path(__file__).resolve().parents[1]
LEDGER_PATH = _REPO / "models" / "war_account_ledger.jsonl"

_last_war_sync_ts: float = 0.0
_last_war_sync_sig: tuple = ()


def _war_sync_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    import os
    try:
        return max(15.0, float(os.getenv("WAR_IB_SYNC_INTERVAL_SEC", "90")))
    except (TypeError, ValueError):
        return 90.0


def _war_sync_signature(state: Dict[str, Any], pos_result: Dict[str, Any]) -> tuple:
    wars = state.get("open_wars") or {}
    keys = tuple(sorted(wars.keys()))
    shares = tuple(sorted((k, int(v.get("shares", 0) or 0)) for k, v in wars.items()))
    return (
        round(float(state.get("nav", 0) or 0), 2),
        round(float(state.get("session_pnl_war", 0) or 0), 2),
        int(pos_result.get("war_slots", 0) or 0),
        keys,
        shares,
    )

# Re-export for scripts/tests
__all__ = [
    "sync_war_from_ib",
    "build_reconcile_report",
    "format_reconcile_report",
    "read_war_ledger",
    "war_ib_sync_enabled",
]


def war_ib_sync_enabled(cfg: Optional[BotConfig] = None) -> bool:
    env = __import__("os").getenv("WAR_IB_SYNC", "true").strip().lower()
    if env in ("0", "false", "no"):
        return False
    return ib_truth_enabled(cfg)


def read_war_ledger(*, since_ts: float = 0.0) -> List[Dict[str, Any]]:
    if not LEDGER_PATH.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if since_ts and float(row.get("ts", 0) or 0) < since_ts:
                continue
            rows.append(row)
    except Exception:
        pass
    return rows


def _max_war_notional(nav: float, cfg: Optional[BotConfig] = None) -> float:
    from core.war_account import _env_float
    pct = _env_float("WAR_IB_RECOVER_MAX_NAV_PCT", 0.90)
    return max(50.0, nav * pct)


def _align_operating_capital(state: Dict[str, Any], cfg: BotConfig) -> bool:
    from core.war_account import _reconcile_war_cash_from_positions, operating_capital_usd

    cap = operating_capital_usd(cfg)
    old = float(state.get("operating_capital", cap) or cap)
    if abs(old - cap) < 0.5:
        return False
    session_pnl = float(state.get("session_pnl_war", 0) or 0)
    state["operating_capital"] = cap
    state["nav"] = round(cap + session_pnl, 2)
    _reconcile_war_cash_from_positions(state, cfg)
    log.info(
        f"⚔️ War capital aligned ${old:,.0f} → ${cap:,.0f} "
        f"(nav=${float(state.get('nav', 0)):,.0f} deployed=${float(state.get('deployed_usd', 0)):,.0f})"
    )
    return True


def sync_war_positions_from_ib(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from core.war_account import (
        _commission_usd,
        _normalize_open_positions,
        _reconcile_war_cash_from_positions,
        load_state,
        operating_capital_usd,
    )
    from core.ib_truth import position_entry_from_truth

    cfg = cfg or BotConfig()
    state = state if state is not None else load_state(cfg)
    _normalize_open_positions(state)

    snap = refresh(ib, cfg, force=True)
    ib_long = snap.long_positions()
    nav = float(state.get("nav", operating_capital_usd(cfg)) or operating_capital_usd(cfg))
    max_notional = _max_war_notional(nav, cfg)

    wars: Dict[str, Any] = {}
    monitor_only: List[str] = []
    adopted: List[str] = []
    dropped: List[str] = []

    for sym, pos in ib_long.items():
        sh = int(abs(pos.qty))
        if sh <= 0:
            continue
        entry = position_entry_from_truth(ib, sym, snap)
        if entry <= 0:
            entry = pos.avg_cost
        notional = entry * sh
        if notional > max_notional:
            monitor_only.append(sym)
            continue
        comm = _commission_usd(cfg, notional)
        prev = (state.get("open_wars") or {}).get(sym) or {}
        wars[sym] = {
            "ticker": sym,
            "shares": sh,
            "entry": entry,
            "ib_fill": entry,
            "comm": comm,
            "ts": float(prev.get("ts") or time.time()),
            "pipeline": "ib_sync",
            "promotion": prev.get("promotion", ""),
            "synced": True,
        }
        if sym not in (state.get("open_wars") or {}):
            adopted.append(sym)

    for sym in list((state.get("open_wars") or {}).keys()):
        if sym not in wars:
            dropped.append(sym)

    state["open_wars"] = wars
    state["open_war"] = next(iter(wars.values()), None) if wars else None
    _reconcile_war_cash_from_positions(state, cfg)

    return {
        "ib_positions": len(ib_long),
        "war_slots": len(wars),
        "adopted": adopted,
        "dropped": dropped,
        "monitor_only": monitor_only,
    }


def sync_session_pnl_from_ib(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    state: Optional[Dict[str, Any]] = None,
    since_ts: Optional[float] = None,
) -> Dict[str, Any]:
    from core.war_account import load_state, _reconcile_war_cash_from_positions

    cfg = cfg or BotConfig()
    state = state if state is not None else load_state(cfg)
    snap = refresh(ib, cfg, force=True, since_ts=since_ts)

    state["session_pnl_war"] = snap.session_pnl_fifo
    state["ticker_session_pnl"] = dict(snap.ticker_pnl_fifo)
    cap = float(state.get("operating_capital", 0) or 0)
    if cap > 0:
        state["nav"] = round(cap + snap.session_pnl_fifo, 2)
        _reconcile_war_cash_from_positions(state, cfg)

    return {
        "since_ts": snap.session_since_ts,
        "executions": len(snap.executions),
        "round_trips": len(snap.round_trips),
        "session_pnl_war": snap.session_pnl_fifo,
        "tickers": snap.ticker_pnl_fifo,
    }


def build_reconcile_report(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    since_ts: Optional[float] = None,
) -> Dict[str, Any]:
    from core.war_account import LEDGER_PATH as WAR_LEDGER, STATE_PATH, load_state, operating_capital_usd

    cfg = cfg or BotConfig()
    state = load_state(cfg)
    since = since_ts if since_ts is not None else ib_truth_session_start_ts(cfg)
    snap = refresh(ib, cfg, force=True, since_ts=since)
    ledger = read_war_ledger(since_ts=since)

    ib_trip_pnl = dict(snap.ticker_pnl_fifo)
    war_exit_pnl: Dict[str, float] = {}
    ghost_exits: List[Dict[str, Any]] = []
    open_at_exit: Dict[str, bool] = {}

    for row in ledger:
        ev = row.get("event", "")
        sym = str(row.get("ticker", "") or "").upper()
        if ev in ("war_entry", "war_ib_recover"):
            open_at_exit[sym] = True
        elif ev == "war_exit":
            pnl = float(row.get("net_pnl", 0) or 0)
            war_exit_pnl[sym] = round(war_exit_pnl.get(sym, 0) + pnl, 2)
            if not open_at_exit.get(sym):
                ghost_exits.append({
                    "ticker": sym,
                    "net_pnl": pnl,
                    "ts": row.get("ts"),
                    "ib_fill": row.get("ib_fill"),
                })
            open_at_exit[sym] = False

    pnl_drift: List[Dict[str, Any]] = []
    for sym in sorted(set(ib_trip_pnl) | set(war_exit_pnl)):
        ib_p = ib_trip_pnl.get(sym, 0)
        war_p = war_exit_pnl.get(sym, 0)
        delta = round(war_p - ib_p, 2)
        if abs(delta) > 5.0:
            pnl_drift.append({"ticker": sym, "war_pnl": war_p, "ib_pnl": ib_p, "delta": delta})

    ib_long = snap.long_positions()
    war_open = set((state.get("open_wars") or {}).keys())

    return {
        "war_state_path": str(STATE_PATH),
        "war_ledger_path": str(WAR_LEDGER),
        "operating_capital_cfg": operating_capital_usd(cfg),
        "war_nav": float(state.get("nav", 0)),
        "war_session_pnl": float(state.get("session_pnl_war", 0)),
        "ib_account": {
            "net_liquidation": snap.account.net_liquidation,
            "total_cash": snap.account.total_cash,
            "realized_pnl": snap.account.realized_pnl,
            "unrealized_pnl": snap.account.unrealized_pnl,
        },
        "ib_positions": [
            {"symbol": p.symbol, "qty": p.qty, "avg_cost": p.avg_cost, "unrealized_pnl": p.unrealized_pnl}
            for p in snap.positions
        ],
        "ib_round_trips_today": len(snap.round_trips),
        "ib_session_pnl_fifo": snap.session_pnl_fifo,
        "ib_session_pnl": snap.session_pnl_ib,
        "ib_realized_pnl": snap.account.realized_pnl,
        "ghost_exits": ghost_exits,
        "pnl_outliers": [g for g in ghost_exits if abs(float(g.get("net_pnl", 0) or 0)) > 100],
        "pnl_drift": pnl_drift,
        "ib_positions_not_in_war": [s for s in ib_long if s not in war_open],
        "war_slots_not_in_ib": [s for s in war_open if s not in ib_long],
        "ok": not ghost_exits and not pnl_drift and not [s for s in war_open if s not in ib_long],
    }


def sync_war_from_ib(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    apply: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    from core.war_account import _append_ledger, load_state, save_state, war_account_enabled

    global _last_war_sync_ts, _last_war_sync_sig

    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg) or not war_ib_sync_enabled(cfg):
        return {"ok": False, "reason": "disabled"}

    now = time.time()
    interval = _war_sync_interval_sec(cfg)
    if apply and not force and (now - _last_war_sync_ts) < interval:
        return {"ok": True, "skipped": "throttle", "next_in_sec": round(interval - (now - _last_war_sync_ts), 1)}

    state = load_state(cfg)
    cap_changed = _align_operating_capital(state, cfg)
    pos_result = sync_war_positions_from_ib(ib, cfg, state=state)
    pnl_result = sync_session_pnl_from_ib(ib, cfg, state=state)
    report = build_reconcile_report(ib, cfg)

    result = {
        "ok": True,
        "apply": apply,
        "capital_aligned": cap_changed,
        "positions": pos_result,
        "session_pnl": pnl_result,
        "reconcile": report,
    }

    if apply:
        sig = _war_sync_signature(state, pos_result)
        changed = sig != _last_war_sync_sig
        material = (
            changed
            or cap_changed
            or pos_result.get("adopted")
            or pos_result.get("dropped")
        )
        if material or force:
            save_state(state)
            _last_war_sync_ts = now
            _last_war_sync_sig = sig
            if material:
                _append_ledger({
                    "event": "war_ib_sync",
                    "capital_aligned": cap_changed,
                    "war_slots": pos_result.get("war_slots", 0),
                    "session_pnl_war": pnl_result.get("session_pnl_war", 0),
                    "monitor_only": pos_result.get("monitor_only", []),
                    "dropped": pos_result.get("dropped", []),
                    "ts": now,
                })
                log.info(
                    f"⚔️ War IB sync — nav=${float(state.get('nav', 0)):,.0f} "
                    f"session_pnl=${float(state.get('session_pnl_war', 0)):+.2f} "
                    f"slots={pos_result.get('war_slots', 0)}"
                )
            else:
                log.debug(
                    f"War IB sync unchanged — nav=${float(state.get('nav', 0)):,.0f} "
                    f"slots={pos_result.get('war_slots', 0)}"
                )
        else:
            result["skipped"] = "unchanged"

    return result


def format_reconcile_report(report: Dict[str, Any]) -> str:
    from core.ib_truth import format_snapshot_summary, get_snapshot

    lines = [
        format_snapshot_summary(get_snapshot()),
        "",
        "═══ War ledger vs IB ═══",
        f"  War NAV (virtual $1k pool): ${report.get('war_nav', 0):,.2f}",
        f"  War session PnL:           ${report.get('war_session_pnl', 0):+,.2f}",
        f"  Config operating cap:      ${report.get('operating_capital_cfg', 0):,.0f}",
        f"  IB FIFO session PnL:       ${report.get('ib_session_pnl_fifo', 0):+,.2f}",
    ]
    if report.get("ib_positions_not_in_war"):
        lines.append(f"  IB-only (monitor):         {report['ib_positions_not_in_war']}")
    if report.get("war_slots_not_in_ib"):
        lines.append(f"  War-only (stale):          {report['war_slots_not_in_ib']}")
    if report.get("ghost_exits"):
        lines.append(f"\n── Ghost exits ({len(report['ghost_exits'])}) ──")
        for g in report["ghost_exits"][:20]:
            lines.append(f"  {g['ticker']} net=${g.get('net_pnl', 0):+,.2f}")
    if report.get("pnl_drift"):
        lines.append("\n── PnL drift (war vs IB FIFO) ──")
        for d in report["pnl_drift"]:
            lines.append(
                f"  {d['ticker']} war=${d['war_pnl']:+,.2f} ib=${d['ib_pnl']:+,.2f} "
                f"Δ=${d['delta']:+,.2f}"
            )
    lines.append(f"\n  Status: {'OK' if report.get('ok') else 'DRIFT DETECTED'}")
    return "\n".join(lines)
