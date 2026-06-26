#!/usr/bin/env python3
"""
core/council_nanny.py — Smart cloud-council budget for profit-first trading.

Council is the nanny (advisory), not the engine. PPO + local gates execute;
Groq/Gemini only weigh in on setups worth the RPM and on open positions.
"""

from __future__ import annotations

from typing import Tuple

from core.config import BotConfig

# Live profit path — always try when budget allows
_HIGH_PRIORITY = frozenset({
    "exit_decision", "position_manage", "risk_exit",
})

# Entry deliberation — only when setup passes quality bar
_MEDIUM_PRIORITY = frozenset({"entry_decision"})

# Background / learning — off in nanny mode (biggest RPM burn)
_LOW_PRIORITY = frozenset({
    "stagnation_check", "scan_score", "rank_scan",
    "pick_next_target", "lock_review",
})

# PPO-led elite entries — one async council ring after fill for distillation only
STRONG_SPIKE_PIPELINES = frozenset({
    "ppo:strong_spike",
    "council:ppo_strong_lead",
})


def nanny_mode_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "COUNCIL_NANNY_MODE", True))


def prefetch_enabled(cfg: BotConfig) -> bool:
    """Background hotline prefetch — off by default in nanny mode."""
    if not nanny_mode_enabled(cfg):
        return bool(getattr(cfg, "LIVE_AI_PREFETCH_ENABLED", True))
    return bool(getattr(cfg, "LIVE_AI_PREFETCH_ENABLED", False))


def learning_ring_enabled(cfg: BotConfig) -> bool:
    """Async council ring after PPO fast-path — off in nanny mode."""
    if not nanny_mode_enabled(cfg):
        return bool(getattr(cfg, "COUNCIL_LEARNING_RING_ENABLED", True))
    return bool(getattr(cfg, "COUNCIL_LEARNING_RING_ENABLED", False))


def strong_spike_learning_ring_enabled(cfg: BotConfig) -> bool:
    """One API ring per strong-spike fill — distillation without full learning_ring burn."""
    return bool(getattr(cfg, "COUNCIL_LEARNING_RING_STRONG_SPIKE_ONLY", True))


def is_strong_spike_pipeline(pipeline: str) -> bool:
    p = str(pipeline or "").strip()
    return p in STRONG_SPIKE_PIPELINES or p.startswith("ppo:strong")


def learning_ring_for_pipeline(cfg: BotConfig, pipeline: str) -> bool:
    """Allow deferred council learning for this entry pipeline."""
    if _providers_hot(cfg):
        return False
    if learning_ring_enabled(cfg):
        return True
    if strong_spike_learning_ring_enabled(cfg) and is_strong_spike_pipeline(pipeline):
        return True
    return False


def council_budget_headroom(cfg: BotConfig) -> float:
    """Fraction of decision RPM budget still available (0–1)."""
    if not getattr(cfg, "COUNCIL_BUDGET_ENABLED", True):
        return 1.0
    try:
        from core.council_budget import _bucket_count, _max_per_minute, PURPOSE_DECISION
        used = _bucket_count(PURPOSE_DECISION)
        cap = max(1, _max_per_minute(cfg, PURPOSE_DECISION))
        return max(0.0, 1.0 - (used / cap))
    except Exception:
        return 1.0


def _providers_hot(cfg: BotConfig) -> bool:
    try:
        from core.council_budget import providers_rate_limited
        return providers_rate_limited(cfg)
    except Exception:
        return False


def should_ring_council(
    cfg: BotConfig,
    task: str,
    *,
    for_learning: bool = False,
    spike_ratio: float = 0.0,
    scan_score: float = 0.0,
    in_position: bool = False,
    pipeline: str = "",
) -> Tuple[bool, str]:
    """
    Return (allowed, reason). Gates LiveAILine.ring / decide_call hot path.
    """
    if not getattr(cfg, "COUNCIL_ENABLED", True):
        return False, "council_disabled"

    if _providers_hot(cfg):
        return False, "provider_429_cooldown"

    if not nanny_mode_enabled(cfg):
        return True, "nanny_off"

    task = str(task or "entry_decision")

    if for_learning and not learning_ring_for_pipeline(cfg, pipeline):
        return False, "nanny_no_learning_ring"

    if task in _LOW_PRIORITY:
        if not bool(getattr(cfg, "COUNCIL_NANNY_LOW_TASKS", False)):
            return False, f"nanny_low_task_{task}"

    headroom = council_budget_headroom(cfg)
    reserve = float(getattr(cfg, "COUNCIL_NANNY_RESERVE_PCT", 0.25))

    if task in _HIGH_PRIORITY or in_position:
        if headroom <= 0 and _providers_hot(cfg):
            return False, "nanny_budget_exhausted"
        return True, "position_priority"

    if task in _MEDIUM_PRIORITY:
        min_spike = float(getattr(cfg, "COUNCIL_NANNY_MIN_SPIKE", 1.25))
        min_score = float(getattr(cfg, "COUNCIL_NANNY_MIN_SCORE", 55.0))
        if spike_ratio < min_spike:
            return False, f"nanny_weak_spike_{spike_ratio:.2f}"
        if scan_score < min_score:
            return False, f"nanny_weak_score_{scan_score:.0f}"
        if for_learning and not learning_ring_for_pipeline(cfg, pipeline):
            return False, "nanny_no_learning_ring"
        if headroom <= reserve:
            return False, f"nanny_reserve_{reserve:.0%}"
        return True, "ok"

    if for_learning and not learning_ring_for_pipeline(cfg, pipeline):
        return False, "nanny_no_learning_ring"
    return False, f"nanny_unknown_task_{task}"


def effective_min_ring_sec(cfg: BotConfig) -> float:
    """Longer gap between rings in nanny mode; much longer when APIs are 429."""
    base = float(getattr(cfg, "LIVE_AI_MIN_RING_SEC", 0.8))
    if nanny_mode_enabled(cfg):
        nanny = float(getattr(cfg, "COUNCIL_NANNY_MIN_RING_SEC", 3.0))
        base = max(base, nanny)
    if _providers_hot(cfg):
        hot = float(getattr(cfg, "COUNCIL_NANNY_HOT_RING_SEC", 20.0))
        base = max(base, hot)
    return base
