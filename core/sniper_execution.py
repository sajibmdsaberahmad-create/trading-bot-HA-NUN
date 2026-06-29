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


def sniper_strong_spike_thresholds(
    cfg: Optional[BotConfig] = None,
    *,
    live_px: float = 0.0,
) -> Tuple[float, float]:
    """Tiered when live_px known; else legacy sniper defaults."""
    if live_px > 0:
        try:
            from core.scan_lock_pools import tiered_min_scan_score, tiered_min_spike_ratio
            return tiered_min_scan_score(cfg, live_px), tiered_min_spike_ratio(cfg, live_px)
        except Exception:
            pass
    return (
        _env_float("SNIPER_STRONG_SPIKE_SCORE", 45.0),
        _env_float("SNIPER_STRONG_SPIKE_RATIO", 1.18),
    )


def _sniper_flash_thresholds(
    cfg: Optional[BotConfig],
    live_px: float = 0.0,
) -> Tuple[float, float]:
    if live_px > 0:
        try:
            from core.scan_lock_pools import tiered_min_scan_score, tiered_min_spike_ratio
            return tiered_min_scan_score(cfg, live_px), tiered_min_spike_ratio(cfg, live_px)
        except Exception:
            pass
    return (
        _env_float("SNIPER_FLASH_MIN_SCORE", 35.0),
        _env_float("SNIPER_FLASH_SPIKE_RATIO", 1.22),
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
    live_px: float = 0.0,
) -> bool:
    if not sniper_active(cfg):
        return False
    min_sc, min_sp = sniper_strong_spike_thresholds(cfg, live_px=live_px)
    return float(scan_score) >= min_sc and float(spike_ratio) >= min_sp


def is_sniper_flash_spike(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
    ppo_action: int,
    ppo_conf: float,
    live_px: float = 0.0,
    *,
    halim_enter: bool = False,
    halim_conf: float = 0.0,
) -> bool:
    """Tick/bar vol green flash — PPO BUY or Halim flash (smart stack)."""
    if not sniper_active(cfg):
        return False
    try:
        from core.smart_stack import smart_stack_enabled, sniper_flash_halim_ok
        if smart_stack_enabled(cfg) and sniper_flash_halim_ok(
            cfg, {"enter": halim_enter, "confidence": halim_conf},
        ):
            pass  # allow vol/score check below without PPO BUY
        elif int(ppo_action) != 1:
            return False
    except Exception:
        if int(ppo_action) != 1:
            return False
    min_sc, min_sp = _sniper_flash_thresholds(cfg, live_px)
    min_conf = _env_float("SNIPER_FLASH_MIN_PPO_CONF", 0.50)
    halim_flash = False
    try:
        from core.smart_stack import smart_stack_enabled, sniper_flash_halim_ok
        halim_flash = smart_stack_enabled(cfg) and sniper_flash_halim_ok(
            cfg, {"enter": halim_enter, "confidence": halim_conf},
        )
    except Exception:
        pass
    conf_ok = float(halim_conf) >= min_conf if halim_flash else float(ppo_conf) >= min_conf
    return (
        float(spike_ratio) >= min_sp
        and float(scan_score) >= min_sc
        and conf_ok
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


def should_sniper_strong_entry(
    cfg: BotConfig,
    spike_ratio: float,
    scan_score: float,
    ppo_action: int,
    ppo_conf: float,
    micro: Optional[dict] = None,
    *,
    ticker: str = "",
    consecutive_losses: int = 0,
    live_px: float = 0.0,
) -> bool:
    """Sniper strong tier — PPO BUY + vol/score; no council wait."""
    if not is_sniper_strong_spike(cfg, scan_score, spike_ratio, live_px=live_px):
        return False
    if int(ppo_action) != 1:
        return False
    min_ppo = _env_float("SNIPER_STRONG_MIN_PPO_CONF", 0.50)
    if float(ppo_conf) < min_ppo:
        return False
    from core.fast_execution import _passes_entry_quality_gate
    from core.live_trade_guard import check_fast_entry_bypass

    block = check_fast_entry_bypass(
        cfg,
        ticker=ticker,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        consecutive_losses=consecutive_losses,
        pipeline="sniper:strong",
    )
    if block:
        return False
    return _passes_entry_quality_gate(
        cfg, micro or {}, spike_ratio, scan_score, ppo_action, ppo_conf,
    )


def sniper_min_bars_focus(cfg: Optional[BotConfig] = None) -> int:
    if not sniper_active(cfg):
        return 6
    return int(_env_float("SNIPER_MIN_BARS_FOCUS", 4.0))


def sniper_force_bar_prefetch(cfg: Optional[BotConfig] = None) -> bool:
    if not sniper_active(cfg):
        return False
    return os.getenv("SNIPER_FORCE_BAR_PREFETCH", "true").lower() in ("1", "true", "yes")


def sniper_cold_micro_vol_confirms(
    spike_ratio: float,
    scan_score: float,
    micro: Optional[dict] = None,
    *,
    live_px: float = 0.0,
    cfg: Optional[BotConfig] = None,
) -> bool:
    """Micro=0% but commander-style vol spike — don't treat as no-edge."""
    micro = micro or {}
    sl = float(micro.get("spike_likelihood", 0) or 0)
    if sl >= 0.08:
        return False
    if live_px > 0:
        try:
            from core.scan_lock_pools import tiered_min_scan_score, tiered_min_spike_ratio
            min_sp = tiered_min_spike_ratio(cfg, live_px)
            min_sc = tiered_min_scan_score(cfg, live_px)
        except Exception:
            min_sp = _env_float("SNIPER_COLD_VOL_MIN_SPIKE", 2.0)
            min_sc = _env_float("SNIPER_COLD_VOL_MIN_SCORE", 70.0)
    else:
        min_sp = _env_float("SNIPER_COLD_VOL_MIN_SPIKE", 2.0)
        min_sc = _env_float("SNIPER_COLD_VOL_MIN_SCORE", 70.0)
    if float(spike_ratio) < min_sp:
        return False
    if float(scan_score) <= 0:
        return True
    return float(scan_score) >= min_sc


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
    live_px: float = 0.0,
    halim_enter: bool = False,
    halim_conf: float = 0.0,
) -> bool:
    """Instant sniper entry on PPO-aligned or Halim-led vol flash."""
    if not is_sniper_flash_spike(
        cfg, scan_score, spike_ratio, ppo_action, ppo_conf, live_px=live_px,
        halim_enter=halim_enter, halim_conf=halim_conf,
    ):
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
    live_px: float = 0.0,
) -> bool:
    """Vol/score flash — used by watch gate before PPO is consulted."""
    if not sniper_active(cfg):
        return False
    min_sc, min_sp = _sniper_flash_thresholds(cfg, live_px)
    return float(spike_ratio) >= min_sp and float(scan_score) >= min_sc


def effective_watch_gates(
    cfg: Optional[BotConfig],
    scan_score: float,
    spike_ratio: float,
    live_px: float = 0.0,
) -> Tuple[float, float]:
    """Score/spike floors for pre-entry watch gate — tiered by price when known."""
    from core.capital_discipline import min_entry_scan_score, min_entry_spike_ratio

    if live_px > 0:
        try:
            from core.scan_lock_pools import tiered_min_scan_score, tiered_min_spike_ratio
            return tiered_min_scan_score(cfg, live_px), tiered_min_spike_ratio(cfg, live_px)
        except Exception:
            pass
    if sniper_vol_flash(cfg, scan_score, spike_ratio, live_px=live_px):
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


def sniper_max_confidence_threshold(cfg: Optional[BotConfig] = None) -> Optional[float]:
    """Cap commander-learned CONFIDENCE_THRESHOLD during sniper RTH."""
    if not sniper_active(cfg):
        return None
    return _env_float("SNIPER_MAX_CONFIDENCE_THRESHOLD", 0.65)


def sniper_ppo_hold_skip_sec(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("SNIPER_PPO_HOLD_SKIP_SEC", 2.0)


def should_skip_entry_council_on_ppo_hold(
    cfg: Optional[BotConfig],
    ppo_action: int,
) -> bool:
    """
    Legacy sniper: skip council on PPO HOLD.
    Smart stack (default): PPO HOLD escalates to Halim+council — never silent skip.
    """
    try:
        from core.smart_stack import smart_stack_enabled
        if smart_stack_enabled(cfg):
            return False
    except Exception:
        pass
    if not sniper_active(cfg):
        return False
    if os.getenv("SNIPER_SKIP_COUNCIL_ON_PPO_HOLD", "true").lower() not in ("1", "true", "yes"):
        return False
    return int(ppo_action) != 1


def cap_sniper_confidence_threshold(cfg: BotConfig) -> bool:
    """Clamp cfg.CONFIDENCE_THRESHOLD to sniper cap (e.g. after commander learning)."""
    cap = sniper_max_confidence_threshold(cfg)
    if cap is None:
        return False
    cur = float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.55))
    if cur <= cap:
        return False
    setattr(cfg, "CONFIDENCE_THRESHOLD", cap)
    return True


def sniper_tick_streams_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """Top-N locked names on tick-by-tick for flash detection (rest stay 5s)."""
    if os.getenv("SNIPER_TICK_STREAMS", "true").lower() not in ("1", "true", "yes"):
        return False
    return sniper_active(cfg)


def sniper_tick_stream_count(cfg: Optional[BotConfig] = None) -> Optional[int]:
    if not sniper_tick_streams_enabled(cfg):
        return None
    return max(0, int(os.getenv("SNIPER_TICK_STREAM_COUNT", "4")))


def sniper_timing_log_line(cfg: Optional[BotConfig] = None) -> str:
    if not sniper_active(cfg):
        return ""
    min_sc, min_sp = sniper_strong_spike_thresholds(cfg)
    tick_part = ""
    n = sniper_tick_stream_count(cfg)
    if n:
        tick_part = f" | top-{n} tick sensors (rest 5s)"
    return (
        f"SNIPER FAST: flash≥{_env_float('SNIPER_FLASH_SPIKE_RATIO', 1.22):.2f}x/"
        f"score≥{_env_float('SNIPER_FLASH_MIN_SCORE', 35):.0f} | "
        f"strong≥{min_sp:.2f}x/score≥{min_sc:.0f} "
        f"(PPO≥{_env_float('SNIPER_STRONG_MIN_PPO_CONF', 0.50):.0%}) | "
        f"council wait≤{_env_float('SNIPER_COUNCIL_MAX_WAIT_SEC', 1.5):.1f}s"
        f"{tick_part}"
    )
