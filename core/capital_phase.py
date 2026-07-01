#!/usr/bin/env python3
"""
core/capital_phase.py — Session capital phases (IB Truth + war sizing window).

premarket_full  — pre-market: scalp + swing (swing = IB account, not war)
rth_war         — RTH: war ~$1k **scalp only**; swing on IB account when enabled
rth_full        — RTH after war pool dry / after-hours: full IB scalp + swing
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.market_hours import get_market_state

if TYPE_CHECKING:
    from core.config import BotConfig

PHASE_PREMARKET_FULL = "premarket_full"
PHASE_RTH_WAR = "rth_war"
PHASE_RTH_FULL = "rth_full"
PHASE_OFF = "off"

_ALL = (PHASE_PREMARKET_FULL, PHASE_RTH_WAR, PHASE_RTH_FULL, PHASE_OFF)


def capital_phases_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    return os.getenv("CAPITAL_PHASES_ENABLED", "true").lower() in ("1", "true", "yes")


def skip_lab_use_full_ib(cfg: Optional["BotConfig"] = None) -> bool:
    """When war pool dry, jump to full IB instead of LAB pool."""
    return os.getenv("CAPITAL_PHASE_SKIP_LAB", "true").lower() in ("1", "true", "yes")


def war_pool_exhausted(cfg: Optional["BotConfig"] = None) -> bool:
    """True when RTH war window is done — full IB + swing sizing unlocks."""
    try:
        from core.war_account import war_account_enabled, load_state, war_pool_depleted, _recompute_mode
        if not war_account_enabled(cfg):
            return True
        state = load_state(cfg)
        if war_pool_depleted(state, cfg):
            return True
        mode = _recompute_mode(state, cfg)
        if mode == "OBSERVE":
            return True
        if skip_lab_use_full_ib(cfg) and mode == "LAB_ACTIVE":
            return True
    except Exception:
        pass
    return False


def swing_live_during_rth_war(cfg: Optional["BotConfig"] = None) -> bool:
    """
    Swing IB entries during rth_war scalp window — uses account balance, not war pool.
    Legacy env: SWING_PARALLEL_WITH_WAR.
    """
    raw = os.getenv("SWING_LIVE_DURING_RTH_WAR", "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    return os.getenv("SWING_PARALLEL_WITH_WAR", "true").lower() in ("1", "true", "yes")


def swing_parallel_with_war_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    """Alias — swing on IB account while war runs scalp ledger."""
    return swing_live_during_rth_war(cfg)


def capital_phase(
    cfg: Optional["BotConfig"] = None,
    runner: Any = None,
    *,
    market_state: Optional[str] = None,
) -> str:
    """
    Current sizing phase. IB is always economic truth; this picks deploy rules.
    """
    if not capital_phases_enabled(cfg):
        try:
            from core.rth_session import is_rth
            if is_rth(cfg):
                return PHASE_RTH_WAR
        except Exception:
            pass
        return PHASE_PREMARKET_FULL

    state = market_state
    if state is None and runner is not None:
        state = getattr(runner, "_last_market_state", None)
    if state is None:
        state = get_market_state(cfg)

    if state == "pre_market":
        return PHASE_PREMARKET_FULL
    if state == "open":
        return PHASE_RTH_FULL if war_pool_exhausted(cfg) else PHASE_RTH_WAR
    if state == "after_hours":
        return PHASE_RTH_FULL
    return PHASE_OFF


def uses_war_sizing(
    cfg: Optional["BotConfig"] = None,
    runner: Any = None,
    *,
    market_state: Optional[str] = None,
    horizon: str = "scalp",
) -> bool:
    """War $1k ledger + bullet caps apply only to scalp in rth_war."""
    from core.war_account import war_applies_to_horizon

    if not war_applies_to_horizon(horizon):
        return False
    if not capital_phases_enabled(cfg):
        try:
            from core.war_account import war_account_enabled
            return bool(war_account_enabled(cfg))
        except Exception:
            return False
    return capital_phase(cfg, runner, market_state=market_state) == PHASE_RTH_WAR


def allows_horizon_live(
    horizon: str,
    cfg: Optional["BotConfig"] = None,
    runner: Any = None,
) -> bool:
    """Which horizons may place IB orders in this phase."""
    phase = capital_phase(cfg, runner)
    if phase == PHASE_OFF:
        return False
    if horizon == "swing":
        if phase == PHASE_RTH_WAR and not swing_live_during_rth_war(cfg):
            return False
        try:
            from core.trade_horizon import swing_ib_live_enabled
            return swing_ib_live_enabled(cfg, phase)
        except Exception:
            return False
    # scalp
    return phase in (PHASE_PREMARKET_FULL, PHASE_RTH_WAR, PHASE_RTH_FULL)


def capital_phase_context(
    cfg: Optional["BotConfig"] = None,
    runner: Any = None,
) -> Dict[str, Any]:
    phase = capital_phase(cfg, runner)
    return {
        "capital_phases_enabled": capital_phases_enabled(cfg),
        "capital_phase": phase,
        "uses_war_sizing": uses_war_sizing(cfg, runner),
        "war_pool_exhausted": war_pool_exhausted(cfg),
        "swing_live_allowed": allows_horizon_live("swing", cfg, runner),
        "scalp_live_allowed": allows_horizon_live("scalp", cfg, runner),
    }
