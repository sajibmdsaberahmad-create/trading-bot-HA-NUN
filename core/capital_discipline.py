#!/usr/bin/env python3
"""
core/capital_discipline.py — Quality-first capital protection.

Watch always, enter rarely. Every dollar is live capital.
Entries require aligned Ollama + PPO council with strong profit odds.
No spike-fast bypasses. Loss avoidance is mandatory — skip beats bad entry.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig


def capital_discipline_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    env = os.getenv("CAPITAL_DISCIPLINE", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    return bool(getattr(cfg, "CAPITAL_DISCIPLINE", True))


def treat_paper_as_live(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    if not capital_discipline_enabled(cfg):
        return False
    env = os.getenv("TREAT_PAPER_AS_LIVE", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    return bool(getattr(cfg, "TREAT_PAPER_AS_LIVE", True))


def is_strong_spike_setup(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
) -> bool:
    """High scanner rank + elevated bar volume — worth PPO-led entry without council wait."""
    cfg = cfg or BotConfig()
    try:
        from core.sniper_execution import sniper_active, sniper_strong_spike_thresholds
        if sniper_active(cfg):
            min_sc, min_sp = sniper_strong_spike_thresholds(cfg)
            return float(scan_score) >= min_sc and float(spike_ratio) >= min_sp
    except Exception:
        pass
    min_sc = float(getattr(cfg, "CAPITAL_STRONG_SPIKE_SCORE", 78))
    min_sp = float(getattr(cfg, "CAPITAL_STRONG_SPIKE_RATIO", 1.35))
    return float(scan_score) >= min_sc and float(spike_ratio) >= min_sp


def _ai_sure_blocks_fast_paths(cfg: Optional[BotConfig]) -> bool:
    try:
        from core.smart_stack import ai_sure_entry_enabled
        return ai_sure_entry_enabled(cfg)
    except Exception:
        return False


def allows_spike_fast_entry(cfg: Optional[BotConfig] = None) -> bool:
    if _ai_sure_blocks_fast_paths(cfg):
        return False
    if capital_discipline_enabled(cfg):
        return False
    cfg = cfg or BotConfig()
    return bool(getattr(cfg, "AI_SPIKE_FAST_ENTRY", True))


def allows_disciplined_spike_fast(
    cfg: Optional[BotConfig] = None,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> bool:
    """PPO-led entry on elite spikes while capital discipline stays on."""
    if _ai_sure_blocks_fast_paths(cfg):
        return False
    cfg = cfg or BotConfig()
    if not capital_discipline_enabled(cfg):
        return allows_spike_fast_entry(cfg)
    if not bool(getattr(cfg, "CAPITAL_STRONG_SPIKE_FAST", True)):
        return False
    return is_strong_spike_setup(cfg, scan_score, spike_ratio)


def allows_micro_fast_entry(cfg: Optional[BotConfig] = None) -> bool:
    try:
        from core.halim_smart_sprint import sprint_block_micro_fast
        if sprint_block_micro_fast(cfg):
            return False
    except Exception:
        pass
    if _ai_sure_blocks_fast_paths(cfg):
        return False
    if capital_discipline_enabled(cfg):
        return False
    cfg = cfg or BotConfig()
    return bool(getattr(cfg, "AI_FAST_EXECUTION", True))


def allows_ppo_lead_while_pending(
    cfg: Optional[BotConfig] = None,
    *,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> bool:
    cfg = cfg or BotConfig()
    if not capital_discipline_enabled(cfg):
        return bool(cfg.PPO_LEAD_WHILE_COUNCIL_PENDING)
    # Explicit PPO lead — green doctrine still gates at submit; not blocked by ai_sure
    if bool(getattr(cfg, "PPO_LEAD_WHILE_COUNCIL_PENDING", False)):
        return True
    if _ai_sure_blocks_fast_paths(cfg):
        return False
    if bool(getattr(cfg, "CAPITAL_PPO_LEAD_STRONG_SPIKE", True)) and is_strong_spike_setup(
        cfg, scan_score, spike_ratio,
    ):
        return True
    return False


def allows_scanner_fast_bypass(
    cfg: Optional[BotConfig] = None,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> bool:
    if _ai_sure_blocks_fast_paths(cfg):
        return False
    if not capital_discipline_enabled(cfg):
        return True
    if bool(getattr(cfg, "CAPITAL_SCANNER_FAST_STRONG", True)) and is_strong_spike_setup(
        cfg, scan_score, spike_ratio,
    ):
        return True
    return False


def allows_timeout_fallback_entry(
    cfg: Optional[BotConfig] = None,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> bool:
    if _ai_sure_blocks_fast_paths(cfg):
        return False
    try:
        from core.war_entry_gates import war_blocks_scanner_timeout, war_gates_active
        if war_gates_active(cfg) and war_blocks_scanner_timeout(cfg):
            return False
    except Exception:
        pass
    if not capital_discipline_enabled(cfg):
        return True
    if bool(getattr(cfg, "CAPITAL_TIMEOUT_FALLBACK_STRONG", True)) and is_strong_spike_setup(
        cfg, scan_score, spike_ratio,
    ):
        return True
    return False


def requires_council_alignment(cfg: Optional[BotConfig] = None) -> bool:
    if _ai_sure_blocks_fast_paths(cfg):
        return False
    return capital_discipline_enabled(cfg)


def effective_min_confidence(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    base = float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.55))
    if capital_discipline_enabled(cfg):
        floor = float(getattr(cfg, "CAPITAL_MIN_CONFIDENCE", 0.65))
        return max(base, floor)
    return base


def effective_min_profit_probability(
    cfg: Optional[BotConfig] = None,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> float:
    cfg = cfg or BotConfig()
    base = float(getattr(cfg, "MIN_PROFIT_PROBABILITY", 0.42))
    if capital_discipline_enabled(cfg):
        floor = float(getattr(cfg, "CAPITAL_MIN_PROFIT_PROBABILITY", 0.62))
        base = max(base, floor)
    try:
        from core.commander_runtime import commander_entry_floors, commander_runtime_enabled
        if commander_runtime_enabled(cfg):
            base = max(base, commander_entry_floors(cfg).get("min_profit_probability", 0.0))
    except Exception:
        pass
    if is_strong_spike_setup(cfg, scan_score, spike_ratio):
        try:
            from core.smart_stack import strict_profit_prob_enabled, ai_sure_entry_enabled
            if ai_sure_entry_enabled(cfg) or strict_profit_prob_enabled(cfg):
                pass
            else:
                strong = float(getattr(cfg, "CAPITAL_STRONG_PROFIT_PROB_FLOOR", 0.48))
                base = min(base, strong)
        except Exception:
            strong = float(getattr(cfg, "CAPITAL_STRONG_PROFIT_PROB_FLOOR", 0.48))
            base = min(base, strong)
    try:
        from core.war_entry_gates import war_gates_active, war_min_profit_probability
        if war_gates_active(cfg):
            base = max(base, war_min_profit_probability(cfg))
    except Exception:
        pass
    return base


def effective_entry_quality_blend(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    base = float(getattr(cfg, "ENTRY_QUALITY_BLEND_WEIGHT", 0.35))
    if capital_discipline_enabled(cfg):
        return max(base, float(getattr(cfg, "CAPITAL_QUALITY_BLEND_WEIGHT", 0.55)))
    return base


def min_entry_spike_ratio(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    base = float(getattr(cfg, "AI_SPIKE_FAST_MIN_RATIO", 1.15))
    if capital_discipline_enabled(cfg):
        base = float(getattr(cfg, "CAPITAL_MIN_ENTRY_SPIKE_RATIO", 1.25))
        try:
            from core.sniper_execution import sniper_entry_quality_floors
            floors = sniper_entry_quality_floors(cfg)
            if floors:
                base = min(base, floors["min_spike_ratio"])
        except Exception:
            pass
    try:
        from core.commander_runtime import commander_entry_floors, commander_runtime_enabled
        if commander_runtime_enabled(cfg):
            base = max(base, commander_entry_floors(cfg).get("min_spike_ratio", 0.0))
    except Exception:
        pass
    return base


def min_entry_scan_score(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    base = float(getattr(cfg, "AI_SPIKE_FAST_MIN_SCORE", 15))
    if capital_discipline_enabled(cfg):
        base = float(getattr(cfg, "CAPITAL_MIN_ENTRY_SCAN_SCORE", 55))
        try:
            from core.sniper_execution import sniper_entry_quality_floors
            floors = sniper_entry_quality_floors(cfg)
            if floors:
                base = min(base, floors["min_scan_score"])
        except Exception:
            pass
    try:
        from core.commander_runtime import commander_entry_floors, commander_runtime_enabled
        if commander_runtime_enabled(cfg):
            base = max(base, commander_entry_floors(cfg).get("min_scan_score", 0.0))
    except Exception:
        pass
    return base


def entry_cooldown_after_skip(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    cd = float(getattr(cfg, "CAPITAL_ENTRY_COOLDOWN_SEC", 0))
    if cd > 0:
        return cd
    return float(getattr(cfg, "SPIKE_SKIP_SEC", 15.0))


def max_entries_per_hour(cfg: Optional[BotConfig] = None) -> int:
    cfg = cfg or BotConfig()
    try:
        from core.war_account import max_war_entries_per_hour, war_account_enabled, sniper_mode
        if war_account_enabled(cfg) and sniper_mode(cfg):
            war_cap = max_war_entries_per_hour(cfg)
            if war_cap > 0:
                return war_cap
    except Exception:
        pass
    return int(getattr(cfg, "MAX_ENTRIES_PER_HOUR", 0))


def passes_pre_entry_gate(
    cfg: Optional[BotConfig],
    *,
    scan_score: float,
    spike_ratio: float,
    forecast: Optional[Dict[str, Any]] = None,
    live_px: float = 0.0,
) -> Tuple[bool, str]:
    """
    Mechanical pre-filter before council — watch-only if edge is weak.
    Monitoring continues; entry is blocked until quality improves.
    """
    if not capital_discipline_enabled(cfg):
        return True, ""
    try:
        from core.sniper_execution import effective_watch_gates
    except Exception:
        effective_watch_gates = lambda c, s, r: (min_entry_scan_score(c), min_entry_spike_ratio(c))
    min_sc, min_sp = effective_watch_gates(cfg, scan_score, spike_ratio, live_px=live_px)
    opening_note = ""
    try:
        from core.rth_session import apply_opening_entry_adjustments
        min_sc, min_sp, opening_note = apply_opening_entry_adjustments(
            cfg,
            scan_score=scan_score,
            spike_ratio=spike_ratio,
            min_score=min_sc,
            min_spike=min_sp,
        )
    except Exception:
        pass
    if scan_score < min_sc:
        suffix = f" | {opening_note}" if opening_note else ""
        return False, f"quality gate: score {scan_score:.0f} < {min_sc:.0f} (watching){suffix}"
    if spike_ratio < min_sp:
        suffix = f" | {opening_note}" if opening_note else ""
        return False, f"quality gate: vol {spike_ratio:.2f}x < {min_sp:.2f}x (watching){suffix}"
    fc = forecast or {}
    if float(fc.get("dir", 0)) < 0 and not fc.get("breakout"):
        sl = float(fc.get("spike_likelihood", 0))
        try:
            from core.sniper_execution import sniper_vol_flash
            flash = sniper_vol_flash(cfg, scan_score, spike_ratio)
        except Exception:
            flash = False
        if sl < 0.62 and not is_strong_spike_setup(cfg, scan_score, spike_ratio) and not flash:
            return False, "quality gate: bearish micro — watch only"
    fade = float(fc.get("fade_risk", 0))
    if fade >= 0.52:
        return False, f"quality gate: fade_risk {fade:.0%} too high"
    loss_p = float(fc.get("loss_pressure", 0))
    max_lp = 0.50
    try:
        from core.sniper_execution import sniper_max_loss_pressure
        max_lp = sniper_max_loss_pressure(cfg, scan_score, spike_ratio)
    except Exception:
        pass
    if loss_p >= max_lp:
        return False, f"quality gate: loss_pressure {loss_p:.0%}"
    return True, ""


def check_entry_rate_limit(
    entries_this_hour: int,
    hour_window_start: float,
    cfg: Optional[BotConfig] = None,
) -> Tuple[bool, str]:
    """Optional hourly cap — disabled when MAX_ENTRIES_PER_HOUR=0."""
    cap = max_entries_per_hour(cfg)
    if cap <= 0:
        return True, ""
    now = time.time()
    if now - hour_window_start >= 3600:
        return True, ""
    cap = max_entries_per_hour(cfg)
    if entries_this_hour >= cap:
        return False, f"hourly entry cap {cap} reached"
    return True, ""


def discipline_prompt_block(cfg: Optional[BotConfig] = None) -> str:
    if not capital_discipline_enabled(cfg):
        return ""
    return (
        "QUALITY-FIRST CAPITAL DISCIPLINE — watch always, enter rarely:\n"
        "- Every dollar is live money. Loss is NOT acceptable — skip beats a bad entry.\n"
        "- WATCH all locked targets continuously; enter ONLY on calculated high-edge setups.\n"
        "- enter=true ONLY when YOU + PPO agree, profit_probability≥62%, fakeout risk low.\n"
        "- Profit is mandatory — use full AI power: ride winners, trail, raise TP, exit at peak.\n"
        "- Marginal spikes are traps unless council+PPO+profit math align.\n"
        "- No artificial entry caps — full AI capability; skip only when edge is weak.\n"
    )


def startup_log_line(cfg: Optional[BotConfig] = None) -> str:
    if not capital_discipline_enabled(cfg):
        return ""
    strong = ""
    if bool(getattr(cfg or BotConfig(), "CAPITAL_STRONG_SPIKE_FAST", True)):
        sc = float(getattr(cfg or BotConfig(), "CAPITAL_STRONG_SPIKE_SCORE", 78))
        sp = float(getattr(cfg or BotConfig(), "CAPITAL_STRONG_SPIKE_RATIO", 1.35))
        strong = f" | strong-spike PPO lead ≥{sc:.0f} score & ≥{sp:.2f}x vol"
    return (
        f"💎 QUALITY ENTRIES: council nanny + PPO on elite spikes{strong} | "
        f"score≥{min_entry_scan_score(cfg):.0f} spike≥{min_entry_spike_ratio(cfg):.2f}x | "
        f"conf≥{effective_min_confidence(cfg):.0%} prob≥{effective_min_profit_probability(cfg):.0%}"
    )
