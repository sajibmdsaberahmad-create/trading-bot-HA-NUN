#!/usr/bin/env python3
"""
core/ib_truth_checklist.py — Startup IB Truth readiness gate.

Confirms IB snapshot is fresh and coherent before the trading loop runs.
Logs a compact checklist banner; optionally waits and blocks until ready.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.ib_truth import (
    IBTruthSnapshot,
    apply_to_runner,
    get_snapshot,
    ib_truth_enabled,
    refresh,
)
from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig


def _is_replay_live() -> bool:
    return os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")


def checklist_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    if _is_replay_live():
        return False
    if not ib_truth_enabled(cfg):
        return False
    return os.getenv("IB_TRUTH_STARTUP_CHECK", "true").lower() in ("1", "true", "yes")


def startup_block_on_fail(cfg: Optional["BotConfig"] = None) -> bool:
    if _is_replay_live():
        return False
    return os.getenv("IB_TRUTH_STARTUP_BLOCK", "true").lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _max_age_sec() -> float:
    return _env_float("IB_TRUTH_STARTUP_MAX_AGE_SEC", 30.0)


def _wait_timeout_sec() -> float:
    return _env_float("IB_TRUTH_STARTUP_WAIT_SEC", 20.0)


def _has_usable_account(snap: IBTruthSnapshot) -> bool:
    acct = snap.account
    if acct.net_liquidation > 0:
        return True
    if acct.total_cash > 0 or acct.buying_power > 0:
        return True
    if snap.positions:
        return True
    return False


def evaluate_ib_truth_snapshot(
    snap: IBTruthSnapshot,
    cfg: Optional["BotConfig"] = None,
    runner: Any = None,
) -> Dict[str, Any]:
    """Evaluate one snapshot — returns ok, block, items, summary lines."""
    items: List[Dict[str, Any]] = []
    blockers: List[str] = []
    warnings: List[str] = []

    if not ib_truth_enabled(cfg):
        return {
            "ok": True,
            "block": False,
            "skipped": True,
            "reason": "ib_truth_disabled",
            "items": [],
            "lines": ["IB Truth checklist skipped (REQUIRE_IB_FILL_SYNC=false)"],
        }

    age = time.time() - snap.refreshed_at if snap.refreshed_at > 0 else 1e9
    fresh = age <= _max_age_sec()

    def _item(name: str, ok: bool, detail: str, *, critical: bool = True) -> None:
        items.append({"name": name, "ok": ok, "detail": detail, "critical": critical})
        if not ok and critical:
            blockers.append(f"{name}: {detail}")
        elif not ok:
            warnings.append(f"{name}: {detail}")

    _item("connected", snap.connected, "yes" if snap.connected else "IB not connected", critical=True)
    _item("snapshot_fresh", fresh, f"age={age:.1f}s (max {_max_age_sec():.0f}s)", critical=True)
    _item(
        "account_values",
        _has_usable_account(snap),
        f"NetLiq=${snap.account.net_liquidation:,.2f} cash=${snap.account.total_cash:,.2f}",
        critical=True,
    )

    hub_on = os.getenv("IB_HUB_ENABLED", "true").lower() in ("1", "true", "yes")
    _item("ib_hub", hub_on, "enabled" if hub_on else "off (direct refresh)", critical=False)

    try:
        from core.capital_phase import capital_phase, capital_phases_enabled
        if capital_phases_enabled(cfg):
            phase = capital_phase(cfg, runner)
            uses_war = False
            try:
                from core.capital_phase import uses_war_sizing
                uses_war = uses_war_sizing(cfg, runner)
            except Exception:
                pass
            _item(
                "capital_phase",
                True,
                f"{phase} sizing={'war' if uses_war else 'full_ib'}",
                critical=False,
            )
    except Exception:
        pass

    pos_n = len(snap.positions)
    ord_n = len(snap.open_orders)
    _item(
        "positions",
        True,
        f"{pos_n} open | orders={ord_n} | execs={len(snap.executions)}",
        critical=False,
    )

    if runner is not None and snap.account.net_liquidation > 0:
        eq = float(getattr(runner, "account_equity", 0) or 0)
        aligned = eq <= 0 or abs(eq - snap.account.net_liquidation) / snap.account.net_liquidation < 0.02
        _item(
            "runner_sync",
            aligned,
            f"runner=${eq:,.2f} ib=${snap.account.net_liquidation:,.2f}",
            critical=False,
        )

    ok = len(blockers) == 0
    lines = _format_lines(snap, items, blockers, warnings)
    return {
        "ok": ok,
        "block": not ok and startup_block_on_fail(cfg),
        "blockers": blockers,
        "warnings": warnings,
        "items": items,
        "lines": lines,
        "snapshot_age_sec": round(age, 2),
        "refreshed_at": snap.refreshed_at,
    }


def _format_lines(
    snap: IBTruthSnapshot,
    items: List[Dict[str, Any]],
    blockers: List[str],
    warnings: List[str],
) -> List[str]:
    lines: List[str] = []
    status = "READY" if not blockers else "NOT READY"
    lines.append(f"Status: {status} | server={snap.server_time or '?'} scope={snap.session_scope}")
    lines.append(
        f"NetLiq ${snap.account.net_liquidation:,.2f} | "
        f"PnL session ${snap.session_pnl_ib:,.2f} | "
        f"UPL ${snap.account.unrealized_pnl:,.2f}"
    )
    for it in items:
        mark = "✓" if it["ok"] else ("✗" if it.get("critical") else "~")
        lines.append(f"{mark} {it['name']}: {it['detail']}")
    for w in warnings[:3]:
        lines.append(f"~ warn: {w}")
    for b in blockers[:3]:
        lines.append(f"✗ block: {b}")
    return lines


def wait_for_ib_truth_ready(
    ib,
    cfg: Optional["BotConfig"],
    runner: Any = None,
    *,
    timeout_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Refresh IB Truth until checklist passes or timeout."""
    timeout = timeout_sec if timeout_sec is not None else _wait_timeout_sec()
    deadline = time.time() + timeout
    last: Dict[str, Any] = {"ok": False, "block": startup_block_on_fail(cfg), "lines": []}

    while time.time() < deadline:
        try:
            snap = refresh(ib, cfg, force=True, ttl_sec=0.0)
            if runner is not None:
                apply_to_runner(runner, snap)
        except Exception as exc:
            last = {
                "ok": False,
                "block": startup_block_on_fail(cfg),
                "blockers": [f"refresh_failed: {exc}"],
                "lines": [f"IB refresh error: {exc}"],
            }
            time.sleep(1.0)
            continue

        last = evaluate_ib_truth_snapshot(snap, cfg, runner)
        if last.get("ok"):
            return last
        time.sleep(1.5)

    if not last.get("blockers"):
        last["blockers"] = ["timeout waiting for fresh IB snapshot"]
    last["block"] = startup_block_on_fail(cfg)
    last["ok"] = False
    return last


def run_startup_checklist(
    runner: Any,
    ib,
    cfg: Optional["BotConfig"] = None,
    *,
    wait: bool = True,
) -> Dict[str, Any]:
    """
    Run at HANOON startup after first balance refresh.
    When wait=true, polls IB until ready or timeout.
    """
    cfg = cfg or getattr(runner, "cfg", None)
    if not checklist_enabled(cfg):
        reason = "replay_live" if _is_replay_live() else "checklist_disabled"
        lines: List[str] = []
        if _is_replay_live():
            lines = ["IB Truth checklist skipped (replay CSV mode — no Gateway required)"]
        return {
            "ok": True,
            "skipped": True,
            "block": False,
            "reason": reason,
            "lines": lines,
        }

    if wait:
        result = wait_for_ib_truth_ready(ib, cfg, runner)
    else:
        snap = get_snapshot()
        if snap.refreshed_at <= 0 and ib is not None:
            try:
                snap = refresh(ib, cfg, force=True, ttl_sec=0.0)
                apply_to_runner(runner, snap)
            except Exception as exc:
                return {
                    "ok": False,
                    "block": startup_block_on_fail(cfg),
                    "blockers": [str(exc)],
                    "lines": [f"IB refresh failed: {exc}"],
                }
        result = evaluate_ib_truth_snapshot(snap, cfg, runner)

    runner._ib_truth_startup = result
    runner._ib_truth_ready = bool(result.get("ok"))
    return result


def log_startup_checklist(result: Dict[str, Any]) -> None:
    """Print compact IB Truth banner to console."""
    if result.get("skipped"):
        return
    from core.startup_log import log_block

    title = "IB TRUTH CHECKLIST — LIVE FROM GATEWAY"
    if result.get("ok"):
        title += " ✓"
    else:
        title += " ✗"
    log_block(title, result.get("lines") or ["no data"])
    if result.get("block") and not result.get("ok"):
        log.error(
            "IB Truth not ready — set IB_TRUTH_STARTUP_BLOCK=false to run anyway "
            f"(blockers: {', '.join(result.get('blockers') or [])})"
        )
    elif result.get("ok"):
        log.info("  ⚡ IB Truth ready — trading from Gateway snapshot (light/fast path)")


def runtime_ib_truth_ok(cfg: Optional["BotConfig"] = None, runner: Any = None) -> bool:
    """Quick gate for entry paths — snapshot fresh enough."""
    if _is_replay_live():
        return True
    if not ib_truth_enabled(cfg):
        return True
    if runner is not None and hasattr(runner, "_ib_truth_ready"):
        if not getattr(runner, "_ib_truth_ready", True):
            return False
    snap = get_snapshot()
    if snap.refreshed_at <= 0:
        return False
    age = time.time() - snap.refreshed_at
    runtime_max = _env_float("IB_TRUTH_RUNTIME_MAX_AGE_SEC", 90.0)
    return age <= runtime_max and snap.connected
