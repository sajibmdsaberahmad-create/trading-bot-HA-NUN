#!/usr/bin/env python3
"""
core/smart_stack.py — Life Engine hub: Halim + PPO lead; API teaches; gates advise.

Vision doc: docs/VISION_SMART_STACK.md
Cursor rule: .cursor/rules/smart-stack-vision.mdc

One ship (scalper_runner hull) — smart sensors, brains, war, execution, learning.
Phases A–E foundation live; maturity ladder unlocks full power over time.
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
    """When true, vol/MTF/regime gates inform Halim+PPO but do not block."""
    if not smart_stack_enabled(cfg):
        return False
    return _env_bool("SMART_STACK_ADVISORY_GATES", "true")


def strict_profit_prob_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """
    Hard veto on red profit_probability / enter_ok=false.
    Default ON with Smart Stack — MTF/regime stay advisory; profit prob does not.
    Set SMART_STACK_STRICT_PROFIT_PROB=false to restore legacy fast-path bypasses.
    """
    if not smart_stack_enabled(cfg):
        return _env_bool("SMART_STACK_STRICT_PROFIT_PROB", "false")
    return _env_bool("SMART_STACK_STRICT_PROFIT_PROB", "true")


def ai_sure_entry_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """
    Entries require dynamic AI alignment — Halim + PPO + green calculative quality.
    No blind spike / micro-fast / timeout bypasses. Default ON with Smart Stack.
    """
    if not smart_stack_enabled(cfg):
        return _env_bool("SMART_STACK_AI_SURE_ENTRY", "false")
    return _env_bool("SMART_STACK_AI_SURE_ENTRY", "true")


def dynamic_entry_surety(
    cfg: Optional[BotConfig],
    *,
    scan_score: float = 0.0,
    spike_ratio: float = 1.0,
    ticker: str = "",
) -> Dict[str, float]:
    """Dynamic min confidence / profit-prob floors — war posture + session losses."""
    from core.capital_discipline import effective_min_confidence, effective_min_profit_probability
    from core.entry_quality import repeat_loser_prob_bump

    min_conf = effective_min_confidence(cfg)
    min_prob = effective_min_profit_probability(cfg, scan_score, spike_ratio)
    posture = war_posture_adjustments(cfg)
    min_conf += float(posture.get("conf_bump", 0))
    min_prob += float(posture.get("prob_bump", 0))
    loss_bump = repeat_loser_prob_bump(cfg, ticker) if ticker else 0.0
    min_prob += loss_bump
    return {
        "min_conf": min(min_conf, 0.92),
        "min_prob": min(min_prob, 0.95),
        "min_halim_conf": min(min_conf * 0.88, 0.88),
        "loss_bump": loss_bump,
    }


def fast_entry_pipeline_blocked(pipeline: str) -> bool:
    """Pipelines that bypass Halim/council deliberation."""
    p = (pipeline or "").lower()
    blocked = (
        "micro_fast", "spike_fast", "strong_spike", "quality_flash", "quality_lead",
        "ppo_lead", "spike_quality", "scanner_fast", "ppo_timeout", "ppo_strong_lead",
        "scanner_timeout", "momentum entry",
    )
    return any(b in p for b in blocked)

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
            trips = int(st.get("war_round_trips_today", st.get("round_trips_today", 0)) or 0)
            bullets_left = int(st.get("war_bullets_remaining", 0) or 0)
            if st.get("war_balance_driven") and not st.get("war_ai_sizing"):
                if bullets_left <= 1:
                    bump_conf += 0.04
                    bump_prob += 0.05
                    notes.append(f"war_bullets_left={bullets_left}")
                if bullets_left <= 0:
                    bump_conf += 0.06
                    bump_prob += 0.08
            elif trips >= 2:
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
        or out.get("quality_conf")
        or 0.0
    )
    eff_min_conf = min_conf + float(posture.get("conf_bump", 0))
    eff_min_prob = min_prob + float(posture.get("prob_bump", 0))

    if strict_profit_prob_enabled(cfg):
        if out.get("quality_enter") is False or (
            prob > 0 and prob < eff_min_prob
        ):
            out["enter"] = False
            out["reason"] = (
                f"war:profit_prob {prob:.0%} < {eff_min_prob:.0%} "
                f"({posture.get('note', '')})"
            )[:200]
            out["pipeline"] = "war:profit_prob_veto"
            return out

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


def halim_local_fast_sec(cfg: Optional[BotConfig] = None) -> float:
    """How long to wait for Halim before quality-led sniper resolve (no cloud)."""
    if not smart_stack_enabled(cfg):
        return 3.0
    return float(os.getenv("SNIPER_HALIM_FAST_SEC", "0.55"))


def halim_local_max_wait_sec(cfg: Optional[BotConfig] = None) -> float:
    """Council slot lifetime when Halim leads — must exceed halim LM latency."""
    if not smart_stack_enabled(cfg):
        return 1.5
    return float(os.getenv("SNIPER_HALIM_LOCAL_WAIT_SEC", "2.8"))


def teacher_api_hard_case_only(cfg: Optional[BotConfig] = None) -> bool:
    """API reserved for curriculum hard cases; Halim handles the rest."""
    return smart_stack_enabled(cfg) and _env_bool("SMART_STACK_TEACHER_HARD_ONLY", "true")


def build_halim_local_entry(
    cfg: Optional[BotConfig],
    *,
    halim_live: Dict[str, Any],
    quality: Dict[str, Any],
    ppo_action: int,
    ppo_conf: float,
    ppo_reason: str,
    min_conf: float,
    scan_score: float,
    spike_ratio: float,
    allow_pending_in_flight: bool = True,
) -> Dict[str, Any]:
    """
    Halim-led local entry — no cloud council wait.
    Keeps sniper speed: brief Halim wait, then quality-led resolve.
    """
    from core.capital_discipline import effective_min_profit_probability

    h_status = str(halim_live.get("status", "missing"))
    h_parsed = halim_live.get("parsed") or {}
    profit_prob = float(quality.get("profit_probability", 0.5) or 0.5)
    min_prob = effective_min_profit_probability(cfg, scan_score, spike_ratio) if cfg else 0.62
    base_conf = float(ppo_conf or 0.5)
    enter_ok = bool(quality.get("enter_ok", True))
    strict_prob = strict_profit_prob_enabled(cfg)
    ai_sure = ai_sure_entry_enabled(cfg)
    sure = dynamic_entry_surety(
        cfg, scan_score=scan_score, spike_ratio=spike_ratio,
    ) if ai_sure else {}
    min_conf_eff = max(min_conf, sure.get("min_conf", min_conf)) if ai_sure else min_conf
    min_prob_eff = max(min_prob, sure.get("min_prob", min_prob)) if ai_sure else min_prob
    prob_floor = min_prob_eff if strict_prob else min_prob * 0.85
    prob_strong = min_prob_eff if strict_prob else min_prob * 0.90

    def _out(
        enter: bool,
        pipeline: str,
        reason: str,
        conf: float,
        pending: bool = False,
        **extra: Any,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "enter": enter,
            "pending": pending,
            "confidence": round(conf, 4),
            "pipeline": pipeline,
            "reason": reason[:200],
            "journal": reason[:300],
            "ppo_action": int(ppo_action),
            "ppo_conf": base_conf,
        }
        if h_parsed:
            row["halim_enter"] = bool(h_parsed.get("enter", False))
            row["halim_conf"] = float(h_parsed.get("confidence", 0) or 0)
        row.update(extra)
        return row

    if h_status == "fresh" and h_parsed:
        h_enter = bool(h_parsed.get("enter", False))
        h_conf = float(h_parsed.get("confidence", 0) or 0)
        h_reason = str(h_parsed.get("reason", ""))[:80]
        veto_conf = float(os.getenv("HALIM_ENTRY_VETO_MIN_CONF", "0.85"))
        if not h_enter and h_conf >= veto_conf and ppo_action == 1:
            return _out(
                False, "halim:local_veto",
                f"Halim skip {h_conf:.0%}: {h_reason}",
                max(base_conf, h_conf * 0.5),
            )
        if ai_sure:
            min_halim = float(sure.get("min_halim_conf", min_conf_eff * 0.88))
            if (
                h_enter
                and h_conf >= min_halim
                and ppo_action == 1
                and base_conf >= min_conf_eff * 0.92
                and enter_ok
                and profit_prob >= min_prob_eff
            ):
                return _out(
                    True, "halim:ai_sure_lead",
                    (
                        f"AI-sure Halim {h_conf:.0%}: {h_reason or 'enter'} "
                        f"prob={profit_prob:.0%} PPO {base_conf:.0%}"
                    ),
                    max(base_conf, h_conf, profit_prob),
                )
            return _out(
                False, "halim:ai_sure_wait",
                (
                    f"AI-sure pass: Halim enter={h_enter} {h_conf:.0%} "
                    f"prob={profit_prob:.0%} (need {min_prob_eff:.0%}) PPO {base_conf:.0%}"
                ),
                max(base_conf, h_conf * 0.6),
            )
        enter = h_enter and h_conf >= min_conf * 0.80
        if not enter and h_enter and scan_score >= 45 and profit_prob >= (
            min_prob if strict_prob else min_prob * 0.88
        ):
            enter = True
        if enter and strict_prob and not enter_ok:
            enter = False
        if enter:
            return _out(
                True, "halim:local_lead",
                f"Halim lead {h_conf:.0%}: {h_reason or 'enter'}",
                max(base_conf, h_conf, profit_prob),
            )
        return _out(
            False, "halim:local_skip",
            f"Halim pass {h_conf:.0%}: {h_reason or 'wait'}",
            max(base_conf, h_conf * 0.6),
        )

    if ai_sure:
        if h_status == "in_flight" and allow_pending_in_flight:
            return _out(
                False, "halim:ai_sure_pending",
                f"AI-sure: awaiting Halim ({base_conf:.0%} PPO, prob={profit_prob:.0%})",
                base_conf,
                pending=True,
            )
        return _out(
            False, "halim:ai_sure_no_halim",
            (
                f"AI-sure: no Halim signal ({h_status}) — "
                f"prob={profit_prob:.0%} score={scan_score:.0f}"
            ),
            base_conf,
            pending=h_status in ("in_flight", "missing", "stale_context", "empty"),
        )

    if h_status == "in_flight" and allow_pending_in_flight:
        return _out(
            False, "halim:in_flight",
            f"PPO {base_conf:.0%} — Halim reasoning…",
            base_conf,
            pending=True,
        )

  # Quality-led sniper (cloud off / Halim slow) — preserve hunt speed
    quality_flash = (
        scan_score >= 35
        and spike_ratio >= 1.20
        and profit_prob >= prob_floor
        and (enter_ok or not strict_prob)
    )
    quality_strong = (
        scan_score >= 42
        and spike_ratio >= 1.15
        and profit_prob >= prob_strong
        and (enter_ok or not strict_prob)
    )
    if quality_flash or quality_strong:
        pipe = "halim:quality_flash" if quality_flash else "halim:quality_lead"
        return _out(
            True, pipe,
            (
                f"Quality lead (Halim {h_status}): prob={profit_prob:.0%} "
                f"score={scan_score:.0f} vol={spike_ratio:.1f}x"
            ),
            max(base_conf, profit_prob, min_conf * 0.78),
        )

    if ppo_action == 1 and base_conf >= min_conf:
        if strict_prob and (not enter_ok or profit_prob < min_prob):
            return _out(
                False, "halim:ppo_blocked_prob",
                (
                    f"PPO buy {base_conf:.0%} blocked: prob={profit_prob:.0%} "
                    f"(need {min_prob:.0%})"
                ),
                base_conf,
            )
        return _out(
            True, "halim:ppo_lead",
            f"PPO buy {base_conf:.0%} (Halim {h_status})",
            base_conf,
        )

    if (
        ppo_action == 0
        and scan_score >= 48
        and profit_prob >= min_prob
        and spike_ratio >= 1.18
        and (enter_ok or not strict_prob)
    ):
        return _out(
            True, "halim:spike_quality",
            (
                f"PPO HOLD but quality spike: prob={profit_prob:.0%} "
                f"score={scan_score:.0f}"
            ),
            max(profit_prob, min_conf * 0.82),
        )

    return _out(
        False, "halim:local_pass",
        (
            f"Local pass (Halim {h_status}): PPO {base_conf:.0%} "
            f"prob={profit_prob:.0%} score={scan_score:.0f}"
        ),
        base_conf,
    )


def live_ram_only(cfg: Optional[BotConfig] = None) -> bool:
    """
    Live session uses RAM only — no disk sweeps/jsonl trims while market is open.
    Cleanup runs off-hours or on shutdown. Default ON with smart stack.
    """
    if not smart_stack_enabled(cfg):
        return _env_bool("RAM_LIVE_ONLY", "false")
    return _env_bool("RAM_LIVE_ONLY", "true")


def maturity_ladder() -> Tuple[Tuple[str, str, str, str], ...]:
    """
    (capability, foundation_status, activates_when, mature_when)
    Foundation = wired now; maturity = data + training + brain stage.
    """
    return (
        ("Halim+PPO lead on every spike", "live", "now", "always"),
        ("Remove council bypass on PPO HOLD", "live", "now", "always"),
        ("Gates as features not vetoes", "live", "now", "always"),
        ("Log all spikes for gold", "live", "now", "always"),
        ("API as sampled teacher only", "live", "now", "teen→adult fade"),
        ("Smart war adaptive posture", "live", "now", "adult + war dataset"),
        ("Smart survival rails at execution", "live", "now", "always"),
        ("Halim adult-quality entries", "foundation", "toddler+", "adult + 1200 gold rows"),
        ("PPO varied signals beyond HOLD", "foundation", "infant+", "child+ micro-steps"),
        ("Calibrated dynamic thresholds", "foundation", "child+", "teen+ proxy calibrated"),
        ("Large balanced training set", "collecting", "ongoing", "600+ labeled verdicts"),
        ("Zero API session (adult stage)", "foundation", "adult stage", "350+ trades maturity"),
        ("War brain tuned per regime", "foundation", "child+", "regime gold + 150 trades"),
        ("Smart sensors (micro+MTF+tick)", "live", "now", "teen+ sensor fusion weights"),
        ("Super-fast execution paths", "live", "now", "adult latency budget <200ms"),
    )


def startup_banner_line(cfg: Optional[BotConfig] = None) -> str:
    if not smart_stack_enabled(cfg):
        return ""
    parts = ["🧠 LIFE ENGINE: Halim+PPO lead"]
    if mechanical_gates_advisory_only(cfg):
        parts.append("advisory gates")
    if ai_sure_entry_enabled(cfg):
        parts.append("AI-sure entries")
    if strict_profit_prob_enabled(cfg):
        parts.append("strict profit prob")
    if smart_war_posture_enabled(cfg):
        parts.append("war posture")
    parts.append("teacher curriculum")
    if live_ram_only(cfg):
        parts.append("RAM-live")
    return " | ".join(parts)
