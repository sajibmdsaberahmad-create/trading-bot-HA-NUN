#!/usr/bin/env python3
"""
core/war_entry_gates.py — War/sniper entry doctrine (commander lottery band).

Blocks timeout/scanner junk on war; risk_off = flash only; enforces min confidence.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.config import BotConfig


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def war_entry_advisory_only(cfg: Optional[BotConfig] = None) -> bool:
    """
    When true, war doctrine never blocks entries — only annotates posture for sizing/learn.
    Set WAR_ENTRY_ADVISORY_ONLY=true in PPO wheel profile.
    """
    return _env_bool("WAR_ENTRY_ADVISORY_ONLY", "false")


def war_gates_active(cfg: Optional[BotConfig] = None) -> bool:
    """War sniper gates OR unified green doctrine on all capital phases."""
    try:
        from core.green_trade_doctrine import same_tactics_all_phases
        from core.war_account import war_account_enabled, sniper_mode
        if same_tactics_all_phases(cfg) and war_account_enabled(cfg):
            return True
        return war_account_enabled(cfg) and sniper_mode(cfg)
    except Exception:
        return False


def war_min_entry_confidence(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    try:
        from core.war_account import is_live_war
        if not is_live_war(cfg):
            return _env_float("WAR_PAPER_MIN_ENTRY_CONFIDENCE", 0.58)
    except Exception:
        pass
    return _env_float("WAR_MIN_ENTRY_CONFIDENCE", 0.65)


def war_min_profit_probability(cfg: Optional[BotConfig] = None) -> float:
    """Commander calculated lottery floor on war."""
    cfg = cfg or BotConfig()
    try:
        from core.war_account import is_live_war
        if not is_live_war(cfg):
            return _env_float("WAR_PAPER_MIN_PROFIT_PROBABILITY", 0.62)
    except Exception:
        pass
    return _env_float("WAR_MIN_PROFIT_PROBABILITY", 0.80)


def war_blocks_scanner_timeout(cfg: Optional[BotConfig] = None) -> bool:
    if not war_gates_active(cfg):
        return False
    return _env_bool("WAR_BLOCK_SCANNER_TIMEOUT", "true")


def war_blocks_scanner_fast(cfg: Optional[BotConfig] = None) -> bool:
    if not war_gates_active(cfg):
        return False
    return _env_bool("WAR_BLOCK_SCANNER_FAST", "true")


def macro_risk_off_sniper_only(cfg: Optional[BotConfig] = None) -> bool:
    if not war_gates_active(cfg):
        return False
    cfg = cfg or BotConfig()
    try:
        from core.war_account import is_live_war
        if not is_live_war(cfg):
            return _env_bool("WAR_PAPER_MACRO_STAND_ASIDE", "false")
    except Exception:
        pass
    return _env_bool("MACRO_RISK_OFF_SNIPER_ONLY", "true")


def is_macro_risk_off(cfg: Optional[BotConfig] = None) -> bool:
    try:
        from core.market_context import get_macro_context
        tone = str(get_macro_context().get("risk_tone", "") or "")
        return tone in ("risk_off", "mild_risk_off", "high_fear")
    except Exception:
        return False


def is_sniper_flash_pipeline(pipeline: str) -> bool:
    p = str(pipeline or "")
    return p in ("sniper:flash", "sniper:strong") or p.startswith("sniper:")


def _war_entry_veto_reason(
    cfg: BotConfig,
    *,
    pipeline: str = "",
    confidence: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    profit_probability: float = 0.0,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
) -> Optional[str]:
    """Internal: would-block reason without advisory-only bypass."""
    if not war_gates_active(cfg):
        return None

    pipe = str(pipeline or "")
    conf = float(confidence or ppo_conf or 0.0)
    min_conf = war_min_entry_confidence(cfg)
    min_prob = war_min_profit_probability(cfg)

    if war_blocks_scanner_timeout(cfg) and "scanner_timeout" in pipe:
        return f"war:block scanner_timeout (conf={conf:.0%} < lottery bar)"

    if war_blocks_scanner_fast(cfg) and "scanner_fast" in pipe:
        return f"war:block scanner_fast — council confirm only when PPO BUY"

    blocked_pipes = ("council:ppo_timeout_lead",)
    if any(b in pipe for b in blocked_pipes):
        if int(ppo_action) != 1 or conf < min_conf:
            return f"war:block {pipe} — PPO BUY + conf≥{min_conf:.0%} required"

    if not is_sniper_flash_pipeline(pipe):
        if int(ppo_action) != 1:
            return None  # handled by ppo_hold_skip upstream
        if conf < min_conf and ppo_conf < min_conf:
            return f"war:block conf {conf:.0%} < {min_conf:.0%}"
        if profit_probability > 0 and profit_probability < min_prob:
            if not is_sniper_strong_enough(cfg, scan_score, spike_ratio):
                return (
                    f"war:block profit_prob {profit_probability:.0%} "
                    f"< lottery {min_prob:.0%}"
                )

    if macro_risk_off_sniper_only(cfg) and is_macro_risk_off(cfg):
        if not is_sniper_flash_pipeline(pipe):
            try:
                from core.sniper_execution import is_sniper_flash_spike, is_sniper_strong_spike
                flash = is_sniper_flash_spike(
                    cfg, scan_score, spike_ratio, int(ppo_action), float(ppo_conf),
                )
                strong = (
                    int(ppo_action) == 1
                    and is_sniper_strong_spike(cfg, scan_score, spike_ratio)
                    and float(ppo_conf) >= _env_float("SNIPER_STRONG_MIN_PPO_CONF", 0.50)
                )
                if not (flash or strong or pipe in ("sniper:flash", "sniper:strong")):
                    return "war:block macro risk_off — flash/strong sniper only"
            except Exception:
                return "war:block macro risk_off — sniper flash only"

    return None


def war_entry_veto(
    cfg: BotConfig,
    *,
    pipeline: str = "",
    confidence: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    profit_probability: float = 0.0,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
) -> Optional[str]:
    """
    Return veto reason if war/sniper should not take this entry.
    None = allowed to proceed. Advisory-only mode always returns None.
    """
    if war_entry_advisory_only(cfg):
        return None
    return _war_entry_veto_reason(
        cfg,
        pipeline=pipeline,
        confidence=confidence,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        profit_probability=profit_probability,
        spike_ratio=spike_ratio,
        scan_score=scan_score,
    )


def war_entry_advisory_context(
    cfg: BotConfig,
    *,
    pipeline: str = "",
    confidence: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    profit_probability: float = 0.0,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
) -> Dict[str, Any]:
    """Non-blocking war posture note for verdict rows and sizing."""
    reason = _war_entry_veto_reason(
        cfg,
        pipeline=pipeline,
        confidence=confidence,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        profit_probability=profit_probability,
        spike_ratio=spike_ratio,
        scan_score=scan_score,
    )
    return {
        "war_advisory_only": war_entry_advisory_only(cfg),
        "war_would_veto": bool(reason),
        "war_advisory_note": (reason or "war:advisory_ok")[:200],
        "war_min_conf": war_min_entry_confidence(cfg),
        "war_min_prob": war_min_profit_probability(cfg),
    }


def is_sniper_strong_enough(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
) -> bool:
    try:
        from core.sniper_execution import is_sniper_strong_spike, sniper_vol_flash
        return (
            is_sniper_strong_spike(cfg, scan_score, spike_ratio)
            or sniper_vol_flash(cfg, scan_score, spike_ratio)
        )
    except Exception:
        return False


def apply_war_entry_veto(
    cfg: BotConfig,
    decision: Dict[str, Any],
    *,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
) -> Dict[str, Any]:
    """In-place style veto on entry decision dict."""
    if not bool(decision.get("enter")):
        return decision
    prob = float(
        decision.get("profit_probability")
        or decision.get("ollama_profit_probability")
        or 0.0
    )
    pipe = str(decision.get("pipeline", ""))
    conf = float(decision.get("confidence", 0) or 0)
    advisory = war_entry_advisory_context(
        cfg,
        pipeline=pipe,
        confidence=conf,
        ppo_action=int(ppo_action),
        ppo_conf=float(ppo_conf),
        profit_probability=prob,
        spike_ratio=float(spike_ratio),
        scan_score=float(scan_score),
    )
    if war_entry_advisory_only(cfg):
        out = dict(decision)
        out["war_advisory"] = advisory
        if advisory.get("war_would_veto"):
            prev = str(out.get("reason", ""))[:100]
            note = str(advisory.get("war_advisory_note", ""))[:80]
            out["reason"] = f"war advisory (no block): {note} | {prev}"[:200]
        return out
    veto = war_entry_veto(
        cfg,
        pipeline=pipe,
        confidence=conf,
        ppo_action=int(ppo_action),
        ppo_conf=float(ppo_conf),
        profit_probability=prob,
        spike_ratio=float(spike_ratio),
        scan_score=float(scan_score),
    )
    if not veto:
        return decision
    decision = dict(decision)
    decision["enter"] = False
    decision["pending"] = False
    decision["reason"] = veto[:200]
    decision["pipeline"] = "war:entry_veto"
    return decision


def block_confidence_raise_on_war(param: str, value: Any, cfg: Optional[BotConfig] = None) -> bool:
    """True when commander/slow-coach must not raise CONFIDENCE_THRESHOLD on war."""
    if not war_gates_active(cfg):
        return False
    if str(param) != "CONFIDENCE_THRESHOLD":
        return False
    if not _env_bool("WAR_BLOCK_CONFIDENCE_RAISE", "true"):
        return False
    try:
        cur = float(getattr(cfg or BotConfig(), "CONFIDENCE_THRESHOLD", 0.55))
        return float(value) > cur
    except (TypeError, ValueError):
        return False
