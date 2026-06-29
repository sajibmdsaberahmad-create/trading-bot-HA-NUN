#!/usr/bin/env python3
"""
core/smart_stack.py — Smart stack: Halim + PPO lead; API teaches; gates advise.

Vision doc: docs/VISION_SMART_STACK.md
Cursor rule: .cursor/rules/smart-stack-vision.mdc

Phases A–E:
  A — PPO HOLD escalates to Halim/council (never silent ppo_hold_skip)
  B — Mechanical gates advisory → context for brains, not hard blocks
  C — Teacher API sampled on hard cases (brain_maturity curriculum)
  D — Every spike verdict logged for gold / learning
  E — War/sniper posture adjusts confidence bars, not mute pipelines
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

VERDICT_LOG = Path("models/smart_stack_verdicts.jsonl")


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def smart_stack_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """Master switch — default ON. Set SMART_STACK=false to restore legacy dumb gates."""
    return _env_bool("SMART_STACK", "true")


def mechanical_gates_advisory_only(cfg: Optional[BotConfig] = None) -> bool:
    """When true, vol/MTF/regime/quality gates inform Halim+PPO but do not block."""
    if not smart_stack_enabled(cfg):
        return False
    return _env_bool("SMART_STACK_ADVISORY_GATES", "true")


def smart_war_posture_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """War/sniper adjusts bars instead of hard pipeline vetoes."""
    if not smart_stack_enabled(cfg):
        return False
    return _env_bool("SMART_STACK_WAR_POSTURE", "true")


def evaluate_pre_entry_advisories(
    cfg: Optional[BotConfig],
    *,
    scan_score: float,
    spike_ratio: float,
    forecast: Optional[Dict[str, Any]] = None,
    live_px: float = 0.0,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (allow_entry, watch_message, advisory_context).
    Smart stack: always allow (True) but pack gate signals into context.
    Legacy: delegates to passes_pre_entry_gate blocking behavior.
    """
    from core.capital_discipline import passes_pre_entry_gate

    advisories: Dict[str, Any] = {}
    ok, msg = passes_pre_entry_gate(
        cfg,
        scan_score=scan_score,
        spike_ratio=spike_ratio,
        forecast=forecast,
        live_px=live_px,
    )
    if msg:
        advisories["pre_entry"] = {"ok": ok, "reason": msg}
    if mechanical_gates_advisory_only(cfg):
        return True, msg if not ok else "", advisories
    return ok, msg, advisories


def collect_spike_gate_advisories(
    cfg: BotConfig,
    *,
    ticker: str,
    quality: Dict[str, Any],
    spike_regime: str,
    df_5m: Any,
    df_15m: Any,
    scan_score: float,
    spike_ratio: float,
) -> Dict[str, Any]:
    """Collect MTF/regime/quality gate signals for brain context."""
    from core.entry_quality import (
        quality_blocks_entry,
        regime_entry_caution,
        mtf_entry_caution,
        mtf_trend_aligned,
    )

    out: Dict[str, Any] = {"ticker": ticker.upper()}
    if not quality.get("enter_ok", True):
        out["quality"] = {
            "ok": False,
            "reason": str(quality.get("reason", ""))[:120],
            "profit_prob": quality.get("profit_probability"),
            "fakeout_risk": quality.get("fakeout_risk"),
        }
    mtf_caution, mtf_detail = mtf_entry_caution(
        cfg, df_5m, df_15m, scan_score=scan_score, spike_ratio=spike_ratio,
    )
    if mtf_caution:
        out["mtf"] = {"ok": False, "reason": "5m/15m not aligned", "detail": mtf_detail}
    else:
        mtf_ok, mtf_weak = mtf_trend_aligned(df_5m, df_15m)
        if not mtf_ok and mtf_weak:
            out["mtf"] = {"ok": False, "reason": "5m/15m weak", "detail": mtf_weak}
    regime_caution, regime_reason = regime_entry_caution(cfg, spike_regime)
    if regime_caution:
        out["regime"] = {"ok": False, "reason": regime_reason or f"regime={spike_regime}"}
    if quality_blocks_entry(cfg, quality):
        out["quality_hard"] = {"ok": False, "reason": str(quality.get("reason", ""))[:120]}
    return out


def spike_gates_block_entry(cfg: BotConfig, advisories: Dict[str, Any]) -> Tuple[bool, str]:
    """True when legacy hard-block mode and a gate says stop."""
    if mechanical_gates_advisory_only(cfg):
        return False, ""
    for key in ("quality_hard", "mtf", "regime", "quality"):
        block = advisories.get(key)
        if block and not block.get("ok", True):
            return True, str(block.get("reason", key))
    return False, ""


def format_gate_context_for_prompt(advisories: Optional[Dict[str, Any]]) -> str:
    if not advisories:
        return ""
    lines: List[str] = ["GATE ADVISORIES (context only — you decide enter/skip):"]
    for key, val in advisories.items():
        if key == "ticker" or not isinstance(val, dict):
            continue
        flag = "OK" if val.get("ok", True) else "CAUTION"
        reason = val.get("reason", "")
        lines.append(f"- {key}: {flag} {reason}")
    return "\n".join(lines)[:800]


def war_posture_adjustments(cfg: BotConfig) -> Dict[str, float]:
    """
    Dynamic confidence / profit-prob bars from war state + macro.
  Returns deltas to ADD to min_conf / min_prob (positive = stricter).
    """
    bump_conf = 0.0
    bump_prob = 0.0
    notes: List[str] = []
    try:
        from core.war_account import war_account_state, war_account_enabled
        if war_account_enabled(cfg):
            st = war_account_state(cfg) or {}
            trips = int(st.get("trips_today", 0) or 0)
            if trips >= 2:
                bump_conf += 0.04
                bump_prob += 0.05
                notes.append(f"war_trips={trips}")
            if trips >= 3:
                bump_conf += 0.06
                bump_prob += 0.08
    except Exception:
        pass
    try:
        from core.war_entry_gates import is_macro_risk_off
        if is_macro_risk_off(cfg):
            bump_conf += 0.03
            bump_prob += 0.04
            notes.append("macro_risk_off")
    except Exception:
        pass
    return {
        "conf_bump": bump_conf,
        "prob_bump": bump_prob,
        "note": "; ".join(notes),
    }


def apply_smart_war_entry(
    cfg: BotConfig,
    decision: Dict[str, Any],
    *,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    min_conf: float = 0.55,
    min_prob: float = 0.62,
) -> Dict[str, Any]:
    """
    Smart war: posture bumps + soft veto on weak edge.
    Legacy hard veto when smart_war_posture disabled.
    """
    if not bool(decision.get("enter")):
        return decision
    out = dict(decision)
    if not smart_war_posture_enabled(cfg):
        try:
            from core.war_entry_gates import apply_war_entry_veto
            return apply_war_entry_veto(
                cfg, out,
                ppo_action=ppo_action,
                ppo_conf=ppo_conf,
                spike_ratio=spike_ratio,
                scan_score=scan_score,
            )
        except Exception:
            return out

    posture = war_posture_adjustments(cfg)
    conf = float(out.get("confidence", ppo_conf) or ppo_conf)
    prob = float(
        out.get("profit_probability")
        or out.get("ollama_profit_probability")
        or 0.0
    )
    eff_min_conf = min_conf + float(posture.get("conf_bump", 0))
    eff_min_prob = min_prob + float(posture.get("prob_bump", 0))

    pipe = str(out.get("pipeline", ""))
    # Scanner timeout allowed when blend confidence clears war bar
    if "scanner_timeout" in pipe and conf < eff_min_conf * 0.92:
        out["enter"] = False
        out["reason"] = (
            f"war:posture conf {conf:.0%} < {eff_min_conf:.0%} "
            f"({posture.get('note', '')})"
        )[:200]
        out["pipeline"] = "war:posture_skip"
        return out

    if prob > 0 and prob < eff_min_prob:
        try:
            from core.war_entry_gates import is_sniper_strong_enough
            if not is_sniper_strong_enough(cfg, scan_score, spike_ratio):
                if conf < eff_min_conf:
                    out["enter"] = False
                    out["reason"] = (
                        f"war:posture prob {prob:.0%} < {eff_min_prob:.0%} "
                        f"({posture.get('note', '')})"
                    )[:200]
                    out["pipeline"] = "war:posture_skip"
                    return out
        except Exception:
            pass

    if posture.get("note"):
        prev = str(out.get("reason", ""))[:120]
        out["reason"] = f"war posture +{posture['conf_bump']:.0%}conf | {prev}"[:200]
        pipe_tag = str(out.get("pipeline", ""))
        out["pipeline"] = f"{pipe_tag}+war_posture" if pipe_tag else "war:posture_ok"
    return out


def should_ring_teacher_api(
    cfg: BotConfig,
    *,
    ticker: str,
    halim_status: str = "",
    halim_conf: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    scan_score: float = 0.0,
    spike_ratio: float = 1.0,
    disagreement: bool = False,
) -> Tuple[bool, str]:
    """
    Phase C curriculum: cloud teacher only on hard / sampled cases.
    Returns (ring, reason).
    """
    if not smart_stack_enabled(cfg):
        return True, "legacy_always_ring"

    if not getattr(cfg, "LIVE_AI_PIPELINE_ENABLED", True):
        return False, "pipeline_off"

    try:
        from core.brain_maturity import allow_teacher_api
        ok, why = allow_teacher_api("decision", cfg)
        if not ok:
            return False, why
    except Exception:
        pass

    # Hard case: Halim missing or low confidence on meaningful spike
    if halim_status in ("missing", "empty", "stale", "stale_context"):
        if scan_score >= 35 or spike_ratio >= 1.2:
            return True, "teacher:halim_unavailable"

    if halim_status == "in_flight" and ppo_action != 1:
        return False, "teacher:wait_halim"

    if disagreement and (scan_score >= 40 or spike_ratio >= 1.25):
        return True, "teacher:ppo_halim_disagree"

    if ppo_action == 0 and ppo_conf >= 0.52 and spike_ratio >= 1.15:
        return True, "teacher:ppo_hold_spike"

    if halim_conf > 0 and halim_conf < 0.55 and scan_score >= 45:
        return True, "teacher:halim_uncertain"

    # Maturity sample rate for curriculum labels
    try:
        from core.brain_maturity import maturity_snapshot
        rate = float(maturity_snapshot(cfg)["limits"].get("council_sample_rate", 0.1))
        if rate > 0 and (hash(f"{ticker}:{int(time.time()) // 30}") % 100) < int(rate * 100):
            return True, "teacher:curriculum_sample"
    except Exception:
        pass

    return False, "teacher:halim_ppo_sufficient"


def sniper_flash_halim_ok(
    cfg: Optional[BotConfig],
    halim_parsed: Optional[Dict[str, Any]],
) -> bool:
    """Phase E: sniper flash may proceed on Halim BUY without PPO BUY."""
    if not smart_stack_enabled(cfg) or not halim_parsed:
        return False
    if not bool(halim_parsed.get("enter")):
        return False
    min_c = float(os.getenv("SMART_STACK_FLASH_HALIM_MIN_CONF", "0.62"))
    return float(halim_parsed.get("confidence", 0) or 0) >= min_c


def log_spike_verdict(
    cfg: Optional[BotConfig],
    *,
    ticker: str,
    spike_ratio: float,
    scan_score: float,
    ppo_action: int,
    ppo_conf: float,
    decision: Dict[str, Any],
    gate_context: Optional[Dict[str, Any]] = None,
    halim_status: str = "",
) -> None:
    """Phase D: record every entry deliberation for gold / analytics."""
    if not smart_stack_enabled(cfg):
        return
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "spike_verdict",
        "ticker": ticker.upper(),
        "spike_ratio": round(float(spike_ratio), 4),
        "scan_score": round(float(scan_score), 2),
        "ppo_action": int(ppo_action),
        "ppo_conf": round(float(ppo_conf), 4),
        "enter": bool(decision.get("enter")),
        "pending": bool(decision.get("pending")),
        "pipeline": str(decision.get("pipeline", ""))[:80],
        "reason": str(decision.get("reason", ""))[:200],
        "confidence": round(float(decision.get("confidence", 0) or 0), 4),
        "halim_status": halim_status,
        "halim_enter": decision.get("halim_enter"),
        "halim_conf": decision.get("halim_conf"),
        "gate_context": gate_context or {},
    }
    try:
        VERDICT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(VERDICT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"smart_stack verdict log: {exc}")


def count_hourly_filled_entry(cfg: Optional[BotConfig] = None) -> bool:
    """Phase B: hourly cap counts fills only (not bracket submits)."""
    return smart_stack_enabled(cfg) and _env_bool("SMART_STACK_HOURLY_FILLS_ONLY", "true")


def startup_banner_line(cfg: Optional[BotConfig] = None) -> str:
    if not smart_stack_enabled(cfg):
        return ""
    parts = ["🧠 SMART STACK: Halim+PPO lead"]
    if mechanical_gates_advisory_only(cfg):
        parts.append("advisory gates")
    if smart_war_posture_enabled(cfg):
        parts.append("war posture")
    parts.append("teacher curriculum")
    return " | ".join(parts)
