#!/usr/bin/env python3
"""
core/ib_hub.py — Single entry for ALL IB Gateway services HANOON uses.

Every balance refresh, AI context build, and off-hours train pulls through here.
No duplicate accountValues/positions() outside ib_truth + order placement paths.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig
    from core.connector import IBConnector
    from core.scalper_runner import ScalperRunner


def ib_hub_enabled() -> bool:
    return os.getenv("IB_HUB_ENABLED", "true").lower() in ("1", "true", "yes")


def _symbol_universe(runner: Optional["ScalperRunner"] = None) -> List[str]:
    syms: List[str] = []
    try:
        from core.ib_truth import get_snapshot
        snap = get_snapshot()
        for p in snap.positions:
            if p.symbol and p.symbol not in syms:
                syms.append(p.symbol)
    except Exception:
        pass
    if runner is not None:
        for t in getattr(runner, "locked_targets", None) or []:
            s = str(t).upper()
            if s and s not in syms:
                syms.append(s)
        if getattr(runner, "current_ticker", None):
            s = str(runner.current_ticker).upper()
            if s and s not in syms:
                syms.append(s)
    if not syms:
        syms = ["SPY", "QQQ"]
    return syms[: int(os.getenv("IB_HUB_MAX_SYMBOLS", "12"))]


def refresh_all_ib_services(
    ib,
    cfg: Optional["BotConfig"] = None,
    connector: Optional["IBConnector"] = None,
    *,
    symbols: Optional[List[str]] = None,
    full: bool = False,
    force: bool = False,
    runner: Optional["ScalperRunner"] = None,
) -> Dict[str, Any]:
    """
    One orchestrated IB pull: truth → extended → macro.
    Returns summary dict (not full snapshots — use get_hub_context for AI).
    """
    if not ib_hub_enabled():
        return {"ib_hub": False}

    from core.ib_truth import apply_to_runner, get_snapshot, ib_truth_enabled, refresh

    syms = symbols or _symbol_universe(runner)
    snap = refresh(ib, cfg, force=force)
    if runner is not None and ib_truth_enabled(cfg):
        apply_to_runner(runner, snap)

    ext: Dict[str, Any] = {}
    if connector is not None:
        try:
            from core.ib_extended import ib_extended_enabled, refresh_ib_extended
            if ib_extended_enabled():
                ext = refresh_ib_extended(
                    ib, cfg, connector, symbols=syms, full=full, force=force,
                )
        except Exception as exc:
            log.debug(f"ib_hub extended: {exc}")

    macro: Dict[str, Any] = {}
    if connector is not None:
        try:
            from core.ib_macro import get_ib_macro_context, ib_macro_enabled
            if ib_macro_enabled():
                macro = get_ib_macro_context(connector, force=force or full)
        except Exception as exc:
            log.debug(f"ib_hub macro: {exc}")

    return {
        "ib_hub": True,
        "refreshed_at": time.time(),
        "full": full,
        "positions": len(snap.positions),
        "open_orders": len(snap.open_orders),
        "executions": len(snap.executions),
        "extended_keys": list(ext.keys()) if ext else [],
        "macro_source": macro.get("source", ""),
        "symbols": syms,
    }


def refresh_services_for_runner(
    runner: "ScalperRunner",
    *,
    full: bool = False,
    force: bool = False,
) -> None:
    """Called from _refresh_account_balance — truth + light extended + macro."""
    try:
        refresh_all_ib_services(
            runner.ib,
            getattr(runner, "cfg", None),
            getattr(runner, "conn", None),
            full=full,
            force=force,
            runner=runner,
        )
    except Exception as exc:
        log.debug(f"ib_hub runner refresh: {exc}")


def get_hub_context(
    cfg: Optional["BotConfig"] = None,
    connector: Optional["IBConnector"] = None,
    runner: Optional["ScalperRunner"] = None,
) -> Dict[str, Any]:
    """Full IB context for Halim, council, Telegram — all services merged."""
    from core.ib_truth import ib_ai_context

    ctx = ib_ai_context(cfg, connector=connector)
    ctx["ib_hub"] = ib_hub_enabled()
    if runner is not None:
        ctx["symbols_watched"] = _symbol_universe(runner)
    try:
        from core.ib_data_catalog import catalog_summary
        ctx["ib_catalog"] = catalog_summary()
    except Exception:
        pass
    return ctx


def audit_ib_coverage() -> Dict[str, Any]:
    """CLI /status: which IB API calls are active vs equity-hull skipped."""
    from core.ib_data_catalog import IB_API_CATEGORIES, catalog_summary

    summary = catalog_summary()
    skipped = [
        {"category": c, "call": call, "reason": note}
        for c, call, cons, note in IB_API_CATEGORIES
        if cons == "—"
    ]
    return {**summary, "skipped_equity_hull": skipped}
