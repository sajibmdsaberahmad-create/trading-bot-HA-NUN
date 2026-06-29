#!/usr/bin/env python3
"""
core/sniper_execution.py — War sniper: catch fleeting volume/green before it fades.

Fast path = PPO BUY + live vol spike + scanner rank — no council wait.
Precision = still requires PPO alignment, guard cooldown, war caps.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig


def sniper_active(cfg: Optional[BotConfig] = None) -> bool:
    try:
        from core.war_account import sniper_mode, war_account_enabled
        return war_account_enabled(cfg) and sniper_mode(cfg)
    except Exception:
        return False


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def sniper_strong_spike_thresholds(cfg: Optional[BotConfig] = None) -> Tuple[float, float]:
    """Lower bar than CAPITAL_STRONG_SPIKE_* — tuned for penny flash moves."""
    return (
        _env_float("SNIPER_STRONG_SPIKE_SCORE", 45.0),
        _env_float("SNIPER_STRONG_SPIKE_RATIO", 1.18),
    )


def sniper_entry_quality_floors(cfg: Optional[BotConfig] = None) -> Optional[Dict[str, float]]:
    if not sniper_active(cfg):
        return None
    return {
        "min_scan_score": _env_float("SNIPER_MIN_ENTRY_SCAN_SCORE", 38.0),
        "min_spike_ratio": _env_float("SNIPER_MIN_ENTRY_SPIKE_RATIO", 1.15),
        "max_loss_pressure": _env_float("SNIPER_MAX_LOSS_PRESSURE", 0.55),
        "flash_loss_pressure": _env_float("SNIPER_FLASH_MAX_LOSS_PRESSURE", 0.62),
    }


def is_sniper_strong_spike(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
) -> bool:
    if not sniper_active(cfg):
        return False
    min_sc, min_sp = sniper_strong_spike_thresholds(cfg)
    return float(scan_score) >= min_sc and float(spike_ratio) >= min_sp


def is_sniper_flash_spike(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
    ppo_action: int,
    ppo_conf: float,
) -> bool:
    """Tick/bar vol green flash — PPO must agree."""
    if not sniper_active(cfg):
        return False
    if int(ppo_action) != 1:
        return False
    min_sp = _env_float("SNIPER_FLASH_SPIKE_RATIO", 1.22)
    min_sc = _env_float("SNIPER_FLASH_MIN_SCORE", 35.0)
    min_conf = _env_float("SNIPER_FLASH_MIN_PPO_CONF", 0.50)
    return (
        float(spike_ratio) >= min_sp
        and float(scan_score) >= min_sc
        and float(ppo_conf) >= min_conf
    )


def sniper_max_loss_pressure(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
) -> float:
    floors = sniper_entry_quality_floors(cfg)
    if not floors:
        return 0.50
    min_flash_sp = _env_float("SNIPER_FLASH_SPIKE_RATIO", 1.22)
    min_flash_sc = _env_float("SNIPER_FLASH_MIN_SCORE", 35.0)
    if float(spike_ratio) >= min_flash_sp and float(scan_score) >= min_flash_sc:
        return floors["flash_loss_pressure"]
    if is_sniper_strong_spike(cfg, scan_score, spike_ratio):
        return floors["max_loss_pressure"]
    return 0.50


def sniper_conf_bump_effective(
    cfg: Optional[BotConfig],
    *,
    spike_ratio: float = 0.0,
    scan_score: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    ticker: str = "",
) -> float:
    """Waive or reduce conf bump on live flash — don't miss green while tuning up."""
    if not sniper_active(cfg):
        return 0.0
    try:
        from core.war_account import sniper_conf_bump
        base = sniper_conf_bump(cfg)
    except Exception:
        base = _env_float("WAR_SNIPER_CONF_BUMP", 0.06)
    try:
        from core.live_trade_guard import guard_conf_bump
        base += guard_conf_bump(ticker)
    except Exception:
        pass
    if is_sniper_flash_spike(cfg, scan_score, spike_ratio, ppo_action, ppo_conf):
        return _env_float("SNIPER_CONF_BUMP_ON_FLASH", 0.0)
    if is_sniper_strong_spike(cfg, scan_score, spike_ratio) and int(ppo_action) == 1:
        return min(base, _env_float("SNIPER_CONF_BUMP_ON_STRONG", 0.02))
    return base


def should_sniper_flash_entry(
    cfg: BotConfig,
    spike_ratio: float,
    scan_score: float,
    ppo_action: int,
    ppo_conf: float,
    micro: Optional[dict] = None,
    *,
    ticker: str = "",
    consecutive_losses: int = 0,
) -> bool:
    """Instant sniper entry on PPO-aligned vol flash."""
    if not is_sniper_flash_spike(cfg, scan_score, spike_ratio, ppo_action, ppo_conf):
        return False
    from core.fast_execution import _passes_entry_quality_gate
    from core.live_trade_guard import check_fast_entry_bypass

    block = check_fast_entry_bypass(
        cfg,
        ticker=ticker,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        consecutive_losses=consecutive_losses,
        pipeline="sniper:flash",
    )
    if block:
        return False
    min_ppo = _env_float("SNIPER_FLASH_MIN_PPO_CONF", 0.50)
    try:
        from core.live_trade_guard import guard_conf_bump
        min_ppo += guard_conf_bump(ticker) * 0.5
    except Exception:
        pass
    if float(ppo_conf) < min_ppo:
        return False
    return _passes_entry_quality_gate(
        cfg, micro or {}, spike_ratio, scan_score, ppo_action, ppo_conf,
    )


def sniper_vol_flash(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
) -> bool:
    """Vol/score flash — used by watch gate before PPO is consulted."""
    if not sniper_active(cfg):
        return False
    return (
        float(spike_ratio) >= _env_float("SNIPER_FLASH_SPIKE_RATIO", 1.22)
        and float(scan_score) >= _env_float("SNIPER_FLASH_MIN_SCORE", 35.0)
    )


def effective_watch_gates(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
) -> Tuple[float, float]:
    """Score/spike floors for pre-entry watch gate — flash uses sniper lows."""
    from core.capital_discipline import min_entry_scan_score, min_entry_spike_ratio

    if sniper_vol_flash(cfg, scan_score, spike_ratio):
        floors = sniper_entry_quality_floors(cfg) or {}
        return (
            floors.get("min_scan_score", _env_float("SNIPER_MIN_ENTRY_SCAN_SCORE", 38.0)),
            floors.get("min_spike_ratio", _env_float("SNIPER_MIN_ENTRY_SPIKE_RATIO", 1.15)),
        )
    return min_entry_scan_score(cfg), min_entry_spike_ratio(cfg)


def sniper_council_max_wait_sec(cfg: BotConfig) -> Optional[float]:
    if not sniper_active(cfg):
        return None
    return _env_float("SNIPER_COUNCIL_MAX_WAIT_SEC", 1.5)


def sniper_timing_log_line(cfg: Optional[BotConfig] = None) -> str:
    if not sniper_active(cfg):
        return ""
    min_sc, min_sp = sniper_strong_spike_thresholds(cfg)
    return (
        f"SNIPER FAST: flash≥{_env_float('SNIPER_FLASH_SPIKE_RATIO', 1.22):.2f}x/"
        f"score≥{_env_float('SNIPER_FLASH_MIN_SCORE', 35):.0f} | "
        f"strong≥{min_sp:.2f}x/score≥{min_sc:.0f} | "
        f"council wait≤{_env_float('SNIPER_COUNCIL_MAX_WAIT_SEC', 1.5):.1f}s"
    )
