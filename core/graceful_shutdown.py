#!/usr/bin/env python3
"""
core/graceful_shutdown.py — Flush all learning data before exit.

Used by live shutdown, replay teardown, and stop scripts (fallback when bot already dead).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

SHUTDOWN_JOURNAL = Path("models/halim_shutdown.jsonl")


def _journal(event: str, detail: Dict[str, Any]) -> None:
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **detail,
    }
    try:
        SHUTDOWN_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with open(SHUTDOWN_JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def flush_halim_data(cfg: Optional[BotConfig] = None, *, trigger: str = "shutdown") -> Dict[str, Any]:
    """Export action gold, manifest, registry — never raises."""
    cfg = cfg or BotConfig()
    result: Dict[str, Any] = {"trigger": trigger, "steps": {}}

    try:
        from core.halim_action_learn import export_action_gold
        result["steps"]["export_action_gold"] = export_action_gold()
    except Exception as exc:
        result["steps"]["export_action_gold"] = {"ok": False, "error": str(exc)[:120]}

    try:
        from core.halim_identity import write_halim_manifest, compute_halim_phase
        result["steps"]["halim_manifest"] = write_halim_manifest(cfg)
        result["phase"] = compute_halim_phase(cfg)
    except Exception as exc:
        result["steps"]["halim_manifest"] = {"ok": False, "error": str(exc)[:120]}

    try:
        from core.halim_registry import append_registry
        gold = (result["steps"].get("export_action_gold") or {})
        append_registry(
            "shutdown_flush",
            {
                "trigger": trigger,
                "action_gold_added": gold.get("added", 0),
                "action_gold_total": gold.get("total_gold", 0),
                "phase": result.get("phase"),
            },
        )
        result["steps"]["registry"] = {"ok": True}
    except Exception as exc:
        result["steps"]["registry"] = {"ok": False, "error": str(exc)[:120]}

    _journal("halim_flush", result)
    return result


def flush_coevolution(cfg: Optional[BotConfig] = None, *, trigger: str = "shutdown") -> Dict[str, Any]:
    try:
        from core.halim_ppo_coevolution import run_coevolution_cycle
        return run_coevolution_cycle(cfg, trigger=trigger)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def flush_owned_brain(
    cfg: Optional[BotConfig] = None,
    *,
    model: Any = None,
    trigger: str = "shutdown",
    push_git: bool = True,
) -> Dict[str, Any]:
    """Post-session evolution flywheel."""
    cfg = cfg or BotConfig()
    try:
        from core.owned_brain_evolution import run_post_session_evolution
        r = run_post_session_evolution(
            cfg, model=model, trigger=trigger, push_git=push_git,
        )
        _journal("owned_brain_evolution", {"trigger": trigger, "skipped": r.get("skipped")})
        return r
    except Exception as exc:
        out = {"ok": False, "error": str(exc)[:120]}
        _journal("owned_brain_evolution_failed", out)
        return out


def flush_git_sync(
    *,
    replay: bool = False,
    nav: float = 0.0,
    pnl_pct: float = 0.0,
    report_path: str = "",
) -> Dict[str, Any]:
    """Push batched learning artifacts."""
    try:
        if replay:
            from core.git_sync import flush_replay_session_git_sync, batched_git_stats
            st = batched_git_stats()
            ok = flush_replay_session_git_sync(nav, pnl_pct)
            return {"ok": ok, "replay": True, "batched": st}
        from core.git_sync import flush_batched_git_sync, push_full_shutdown_sync
        flush_batched_git_sync("pre_shutdown", full_sync=False, force=True)
        ok = push_full_shutdown_sync(nav, pnl_pct, report_path)
        return {"ok": ok, "replay": False}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def run_graceful_shutdown(
    cfg: Optional[BotConfig] = None,
    *,
    mode: str = "live",
    nav: float = 0.0,
    pnl_pct: float = 0.0,
    report_path: str = "",
    model: Any = None,
    push_git: bool = True,
    trigger: str = "shutdown",
) -> Dict[str, Any]:
    """
    Full data flush — Halim + owned brain + git.
    mode: live | replay
    """
    cfg = cfg or BotConfig()
    replay = mode == "replay" or os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")

    log.info(f"🛑 Graceful shutdown flush ({mode}) — saving all learning data…")
    summary: Dict[str, Any] = {
        "mode": mode,
        "trigger": trigger,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "steps": {},
    }

    summary["steps"]["halim"] = flush_halim_data(cfg, trigger=trigger)
    summary["steps"]["coevolution"] = flush_coevolution(cfg, trigger=trigger)

    ev_trigger = "replay_session_end" if replay else "live_session_end"
    if trigger not in ("evolution_done",):
        summary["steps"]["evolution"] = flush_owned_brain(
            cfg,
            model=model,
            trigger=ev_trigger if trigger == "shutdown" else trigger,
            push_git=push_git if not replay else False,
        )

    if push_git:
        summary["steps"]["git"] = flush_git_sync(
            replay=replay, nav=nav, pnl_pct=pnl_pct, report_path=report_path,
        )

    _journal("graceful_shutdown_complete", {
        "mode": mode,
        "nav": nav,
        "pnl_pct": pnl_pct,
        "steps_ok": {
            k: v.get("ok", not v.get("skipped")) if isinstance(v, dict) else bool(v)
            for k, v in summary["steps"].items()
        },
    })
    log.info("✅ Graceful shutdown flush complete")
    return summary


def run_standalone_shutdown_flush(*, replay: bool = False) -> Dict[str, Any]:
    """
    Fallback when bot process is already dead — still export Halim gold + git.
    Called by stop scripts if graceful SIGTERM did not run teardown.
    """
    mode = "replay" if replay else "live"
    log.info(f"🛑 Standalone shutdown flush ({mode}) — bot not running, saving disk data…")
    cfg = BotConfig()
    summary = run_graceful_shutdown(
        cfg,
        mode=mode,
        push_git=os.getenv("OWNED_BRAIN_GIT_PUSH", "true").lower() in ("1", "true", "yes"),
        trigger="standalone_flush",
    )
    return summary
