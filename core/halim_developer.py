#!/usr/bin/env python3
"""
core/halim_developer.py — M. A. Halim as self-developer: mutate, improve, document, git push.

Halim-native cycle (no external LLM):
  1. Self-improvement plan (performance → param adjustments)
  2. Heuristic mutations (trade stats → bounded param changes)
  3. Apply commander plan (guardrailed)
  4. Sync halim/ model repo + update manifests/docs
  5. Full git push (HANOON + learning artifacts + docs)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

DEV_LOG = Path("models/halim_developer.jsonl")
_embedded_git_started = False


def halim_auto_push_enabled() -> bool:
    return os.getenv("HALIM_AUTO_PUSH", "true").lower() in ("1", "true", "yes")


def enable_halim_developer_mode(cfg: BotConfig) -> BotConfig:
    """Halim self-development — git push only when GIT_PUSH_DURING_SESSION allows."""
    os.environ.setdefault("OWNED_BRAIN_GIT_PUSH", "true")
    os.environ.setdefault("HALIM_AUTO_PUSH", "true")
    session_push = os.getenv("GIT_PUSH_DURING_SESSION", "false").lower() in ("1", "true", "yes")
    cfg.GIT_PUSH_DURING_SESSION = session_push
    if session_push:
        ensure_embedded_git_watcher(cfg)
    else:
        log.info(
            "🧠 Halim developer mode — git deferred until stop_hanoon "
            "(GIT_PUSH_DURING_SESSION=false)"
        )
    return cfg


def ensure_embedded_git_watcher(cfg: Optional[BotConfig] = None) -> None:
    """Background git auto-push inside HANOON (no separate daemon required)."""
    global _embedded_git_started
    if _embedded_git_started or not halim_auto_push_enabled():
        return
    if os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes"):
        log.info("🧠 Halim git auto-push skipped during REPLAY (1 sync at session end)")
        return
    if cfg and not getattr(cfg, "GIT_PUSH_DURING_SESSION", False):
        return
    if os.getenv("GIT_PUSH_DURING_SESSION", "false").lower() not in ("1", "true", "yes"):
        return
    try:
        from core.git_sync import (
            _enabled,
            init as git_init,
            is_standalone_mode,
            run_standalone_daemon,
            set_standalone_mode,
        )
        cfg = cfg or BotConfig()
        git_init(cfg)
        if not _enabled:
            log.warning("Halim git push: disabled — set GITHUB_TOKEN + GITHUB_HANOON_REPO in .env")
            return
        if is_standalone_mode():
            _embedded_git_started = True
            return
        set_standalone_mode(True)
        _embedded_git_started = True

        def _daemon():
            try:
                run_standalone_daemon(cfg)
            except Exception as exc:
                log.debug(f"Halim embedded git watcher: {exc}")

        threading.Thread(target=_daemon, name="halim-git-auto-push", daemon=True).start()
        log.info("🧠 M. A. Halim — embedded git auto-push active (always sync)")
    except Exception as exc:
        log.debug(f"Halim git watcher start: {exc}")


def _append_dev_log(row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    DEV_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(DEV_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _halim_heuristic_plan(cfg: BotConfig) -> Dict[str, Any]:
    """Local mutation plan — no external LLM."""
    from core.ppo_teacher_training import trade_stats

    stats = trade_stats(n=400)
    wr = float(stats.get("win_rate", 0) or 0)
    conf = float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.65))
    min_prob = float(getattr(cfg, "MIN_PROFIT_PROBABILITY", 0.62))
    mutations = []

    if wr < 0.20:
        mutations.append({
            "param": "CONFIDENCE_THRESHOLD",
            "value": min(0.78, conf + 0.03),
            "reason": f"Halim dev: WR {wr:.0%} — tighten entry confidence",
        })
        mutations.append({
            "param": "MIN_PROFIT_PROBABILITY",
            "value": min(0.65, min_prob + 0.04),
            "reason": "Halim dev: raise profit probability gate",
        })
    elif wr > 0.45:
        mutations.append({
            "param": "CONFIDENCE_THRESHOLD",
            "value": max(0.55, conf - 0.02),
            "reason": f"Halim dev: WR {wr:.0%} healthy — slight relax",
        })

    scan_iv = int(getattr(cfg, "SCAN_INTERVAL_SECONDS", 30))
    if wr < 0.15 and scan_iv < 90:
        mutations.append({
            "param": "SCAN_INTERVAL_SECONDS",
            "value": min(120, scan_iv + 10),
            "reason": "Halim dev: slow scan cadence after heavy losses",
        })

    lessons = [
        "Halim documents every mutation in git — review improvement_history.json",
        f"Session trade WR {wr:.0%} on {stats.get('count', 0)} round-trips",
    ]
    if wr < 0.25:
        lessons.append("Skip repeat losers until proxy confidence rises")

    return {
        "summary": f"Halim heuristic dev plan — WR {wr:.0%}",
        "mutations": mutations[:4],
        "lessons": lessons,
        "strategy_shift": "Owned students lead; params adapt from real fills",
        "_source": "halim_developer",
    }


def _sync_halim_repo() -> Dict[str, Any]:
    script = Path("halim/scripts/sync_from_tradingbot.py")
    if not script.is_file():
        return {"skipped": True, "reason": "no_halim_repo"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--source", "."],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(".").resolve()),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                return json.loads(proc.stdout.strip())
            except Exception:
                return {"ok": True, "stdout": proc.stdout[:200]}
        return {"ok": False, "stderr": proc.stderr[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _halim_git_push_all(reason: str) -> Dict[str, Any]:
    """Immediate full learning + doc push."""
    out: Dict[str, Any] = {"reason": reason}
    try:
        from core.halim_guardrails import gate_git_push, gate_file_write
        ok, msg = gate_git_push(reason)
        if not ok:
            return {"skipped": True, "reason": msg}
        from core.git_sync import (
            push_learning_checkpoint,
            sync_all_learning_artifacts,
            push_change,
        )
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out["learning"] = push_learning_checkpoint(f"halim_{reason}", full_sync=True)
        out["full_sync"] = sync_all_learning_artifacts(f"halim_{reason}_{tag}")
        doc_files = [
            f for f in (
                "docs/HALIM.md",
                "docs/OWNED_BRAIN.md",
                "docs/BRAIN_DEVELOPMENT_LOG.md",
                "models/halim_identity.json",
                "models/halim_manifest.json",
                "models/halim_developer.jsonl",
                "models/owned_brain_manifest.json",
                "models/ai_guidelines.txt",
                "models/parameter_adjustments.json",
                "models/improvement_history.json",
                "halim/HALIM_MANIFEST.json",
                "halim/README.md",
                "halim/data/registry.jsonl",
            )
            if Path(f).is_file()
        ]
        if doc_files:
            safe = [f for f in doc_files if gate_file_write(f)[0]]
            if safe:
                out["docs_push"] = push_change(
                    f"halim: {reason} — docs+state",
                    files=safe,
                    category="training",
                )
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


def run_halim_developer_cycle(
    cfg: Optional[BotConfig] = None,
    *,
    trigger: str = "session",
    runner: Optional["ScalperRunner"] = None,
    push_git: bool = True,
) -> Dict[str, Any]:
    """
    Halim self-development: mutate params, improve, sync repo, push git.
    """
    cfg = cfg or BotConfig()
    enable_halim_developer_mode(cfg)

    from core.halim_identity import HALIM_FULL_NAME, ensure_identity, write_halim_manifest

    ensure_identity(cfg)
    try:
        from core.halim_guardrails import ensure_constitution
        ensure_constitution()
    except Exception:
        pass
    log.info(f"🔧 {HALIM_FULL_NAME} developer cycle ({trigger})")

    result: Dict[str, Any] = {
        "trigger": trigger,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "steps": {},
    }

    # 1. Self-improvement engine
    try:
        from core.self_improver import generate_self_improvement_plan
        result["steps"]["self_improvement"] = generate_self_improvement_plan(cfg)
    except Exception as exc:
        result["steps"]["self_improvement"] = {"ok": False, "error": str(exc)[:120]}

    # 2. Heuristic mutations + apply (guardrailed)
    try:
        from core.commander_learning import apply_commander_plan
        from core.halim_guardrails import gate_mutation, kill_switch_active
        if kill_switch_active():
            result["steps"]["mutations"] = {"skipped": True, "reason": "kill_switch"}
        else:
            plan = _halim_heuristic_plan(cfg)
            filtered = []
            for mut in plan.get("mutations") or []:
                ok, msg = gate_mutation(str(mut.get("param", "")), cfg)
                if ok:
                    filtered.append(mut)
            plan["mutations"] = filtered
            autopilot = getattr(runner, "autopilot", None) if runner else None
            consciousness = getattr(runner, "consciousness", None) if runner else None
            applied = apply_commander_plan(
                cfg, plan,
                autopilot=autopilot,
                consciousness=consciousness,
                source=f"halim_developer_{trigger}",
            )
            result["steps"]["mutations"] = {
                "plan_source": plan.get("_source"),
                "applied": applied.get("applied", []),
                "rejected": applied.get("rejected", []),
            }
    except Exception as exc:
        result["steps"]["mutations"] = {"ok": False, "error": str(exc)[:120]}

    # 3. Halim manifests + repo sync
    try:
        result["steps"]["halim_manifest"] = write_halim_manifest(cfg)
        result["steps"]["halim_repo_sync"] = _sync_halim_repo()
    except Exception as exc:
        result["steps"]["halim_repo_sync"] = {"ok": False, "error": str(exc)[:120]}

    # 4. Git push everything
    if push_git:
        result["steps"]["git_push"] = _halim_git_push_all(trigger)

    n_applied = len((result["steps"].get("mutations") or {}).get("applied") or [])
    summary = (
        f"Halim developer {trigger}: {n_applied} mutation(s), "
        f"git={'ok' if result['steps'].get('git_push') else 'skip'}"
    )
    result["summary"] = summary
    _append_dev_log(result)

    try:
        from core.brain_notify import notify_brain_development
        notify_brain_development(
            cfg,
            "brain_evolution",
            {
                "stage": result["steps"].get("halim_manifest", {}).get("phase", "?"),
                "trigger": f"halim_dev_{trigger}",
                "summary": summary,
                "mutations_applied": n_applied,
                "git_push": "pushed" if push_git else "skipped",
            },
        )
    except Exception:
        pass

    log.info(f"🔧 {HALIM_FULL_NAME} developer complete — {summary}")
    return result
