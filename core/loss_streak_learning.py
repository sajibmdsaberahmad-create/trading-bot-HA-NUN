#!/usr/bin/env python3
"""
core/loss_streak_learning.py — Learn from loss streaks instead of long blind cool-offs.

Reads recent post-mortems / trade journal, runs 5W commander plan, applies
guardrailed mutations, and returns a resume confidence score.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

REVIEW_PATH = Path("models/loss_streak_review.jsonl")
POST_MORTEM = Path("models/post_mortem_audit.jsonl")


def _read_recent_losses(limit: int = 8) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not POST_MORTEM.exists():
        return rows
    try:
        lines = POST_MORTEM.read_text(errors="replace").splitlines()
        for line in reversed(lines[-400:]):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = str(row.get("event", ""))
            if ev == "exit_postmortem" and float(row.get("pnl_usd", 0) or 0) < 0:
                rows.append(row)
            elif ev == "entry_fill" and row.get("ticker"):
                rows.append(row)
            if len(rows) >= limit * 2:
                break
    except OSError:
        pass
    return rows[:limit]


def _heuristic_confidence(applied: int, streak: int, losses: List[Dict[str, Any]]) -> float:
    conf = 0.48
    conf += min(0.15, applied * 0.05)
    conf += min(0.12, len(losses) * 0.02)
    if streak <= 3:
        conf += 0.08
    slippage_hits = sum(
        1 for r in losses
        if abs(float(r.get("entry_slippage_pct", 0) or 0)) > 0.03
        or abs(float(r.get("slippage_pct", 0) or 0)) > 0.03
    )
    if slippage_hits:
        conf += 0.06
    return min(0.88, conf)


def _heuristic_mutations(cfg: BotConfig, losses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    muts: List[Dict[str, Any]] = []
    slippage = any(
        abs(float(r.get("entry_slippage_pct", r.get("slippage_pct", 0)) or 0)) > 0.04
        for r in losses
    )
    if slippage:
        cur = float(getattr(cfg, "MAX_ACCEPTABLE_SLIPPAGE_PCT", 0.004))
        muts.append({
            "param": "MAX_ACCEPTABLE_SLIPPAGE_PCT",
            "value": max(0.002, cur * 0.85),
            "reason": "Loss streak — tighten max entry slippage after bad fills",
        })
        muts.append({
            "param": "MIN_REWARD_RISK_RATIO",
            "value": min(3.0, float(getattr(cfg, "MIN_REWARD_RISK_RATIO", 2.0)) + 0.15),
            "reason": "Loss streak — demand slightly better R:R before re-entry",
        })
    muts.append({
        "param": "CONFIDENCE_THRESHOLD",
        "value": min(
            0.75,
            float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.55)) + 0.03,
        ),
        "reason": "Loss streak — raise entry bar until pilot regains edge",
    })
    muts.append({
        "param": "VOLUME_SPIKE_MIN_RATIO",
        "value": min(
            2.5,
            float(getattr(cfg, "VOLUME_SPIKE_MIN_RATIO", 1.25)) + 0.1,
        ),
        "reason": "Loss streak — require stronger volume confirmation",
    })
    return muts[:3]


def run_loss_streak_learning(
    cfg: BotConfig,
    runner: "ScalperRunner",
) -> Dict[str, Any]:
    """Blocking learning session — call from background thread only."""
    from core.commander_learning import (
        apply_commander_plan,
        build_learning_context,
        generate_commander_plan,
    )

    streak = int(getattr(getattr(runner, "risk", None), "_consecutive_losses", 0) or 0)
    losses = _read_recent_losses(10)
    loss_lines = []
    for r in losses[:6]:
        loss_lines.append(
            f"- {r.get('ticker', '?')}: "
            f"pnl=${float(r.get('pnl_usd', 0) or 0):+.2f} "
            f"slip={float(r.get('entry_slippage_pct', r.get('slippage_pct', 0)) or 0):.2%} "
            f"regime={r.get('regime_tag', r.get('regime', ''))}"
        )

    trigger = (
        f"LOSS STREAK HALT — {streak} consecutive losses.\n"
        "WHY did we lose? Read recent trades and post-mortems.\n"
        "WHAT must change before resuming? Propose max 3 param mutations.\n"
        "Recent losses:\n" + ("\n".join(loss_lines) or "(no post-mortem rows yet)")
    )

    ctx = build_learning_context(cfg, runner, trigger=trigger)
    ctx["loss_streak"] = streak
    ctx["recent_loss_rows"] = losses[:8]

    def _think(prompt: str) -> str:
        ac = getattr(runner, "ai_commander", None)
        if ac and hasattr(ac, "think"):
            return ac.think(prompt[:3500], task="decide") or ""
        return ""

    plan = generate_commander_plan(cfg, ctx, _think)
    heuristic = _heuristic_mutations(cfg, losses)
    streak_muts = []
    try:
        from core.live_trade_guard import loss_streak_heuristic_mutations
        streak_muts = loss_streak_heuristic_mutations(cfg, streak)
    except Exception:
        pass
    merged: List[Dict[str, Any]] = []
    seen_params: set = set()
    for mut in streak_muts + heuristic + (plan.get("mutations") or []):
        p = str(mut.get("param", "")).strip()
        if not p or p in seen_params:
            continue
        seen_params.add(p)
        merged.append(mut)
        if len(merged) >= 4:
            break
    plan["mutations"] = merged
    if "Could not parse" in str(plan.get("summary", "")) and merged:
        plan["summary"] = (
            f"Loss streak {streak} — applied {len(merged)} guardrail mutation(s) "
            f"(deterministic + heuristics)"
        )

    applied = apply_commander_plan(
        cfg, plan,
        autopilot=getattr(runner, "autopilot", None),
        consciousness=getattr(runner, "consciousness", None),
        source="loss_streak",
    )
    n_applied = len(applied.get("applied") or [])
    confidence = _heuristic_confidence(n_applied, streak, losses)
    if plan.get("understanding"):
        confidence = min(0.9, confidence + 0.05)

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "streak": streak,
        "summary": (plan.get("summary") or "")[:500],
        "understanding": (plan.get("understanding") or "")[:400],
        "lessons": (plan.get("lessons") or [])[:5],
        "mutations_applied": n_applied,
        "resume_confidence": round(confidence, 3),
        "loss_sample": loss_lines[:5],
    }
    try:
        REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError as exc:
        log.debug(f"Loss streak review write: {exc}")

    log.info(
        f"🧠 LOSS STREAK review ({streak} losses): "
        f"{(plan.get('summary') or '')[:100]} | "
        f"applied={n_applied} resume_conf={confidence:.0%}"
    )
    if plan.get("lessons"):
        for lesson in plan["lessons"][:2]:
            log.info(f"  📚 Lesson: {str(lesson)[:120]}")

    min_sec = float(getattr(cfg, "LOSS_STREAK_LEARNING_MIN_SEC", 45))
    started = float(getattr(runner.risk, "_learning_started_at", 0) or time.time())
    wait = min_sec - (time.time() - started)
    if wait > 0:
        time.sleep(wait)

    return {
        "summary": plan.get("summary", ""),
        "confidence": confidence,
        "applied": n_applied,
        "lessons": plan.get("lessons") or [],
    }
