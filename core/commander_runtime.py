#!/usr/bin/env python3
"""
core/commander_runtime.py — Apply commander IB report lessons at runtime.

Live, replay, and paper (capital-discipline) paths share ScalperRunner; this module
ensures calculated-lottery floors and execution doctrine are active — not only in SFT gold.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

_LOADED = False

RUNTIME_DOCTRINE = (
    "COMMANDER IB EXECUTION (runtime):\n"
    "• Calculated lottery: ENTER at 80–97% conviction — spike≥2x, profit_prob≥0.80, fakeout≤0.25\n"
    "• Hope-hold was the mistake on CTEV/PLUG/ENVB trips — EXIT fast; never blacklist symbols\n"
    "• PLUG/vol energy OK when setup qualifies (USEG +3.21% same sector); re-enter after exit\n"
    "• SKIP weak setups only (<80%); max single-trip loss ~3% equity; turnover when net edge > fees"
)


def commander_runtime_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    env = os.getenv("COMMANDER_RUNTIME_ENABLED", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    return bool(getattr(cfg, "COMMANDER_RUNTIME_ENABLED", True))


def commander_entry_floors(cfg: Optional[BotConfig] = None) -> Dict[str, float]:
    """Raised entry floors from commander calculated-lottery doctrine."""
    if not commander_runtime_enabled(cfg):
        return {}
    cfg = cfg or BotConfig()
    return {
        "min_profit_probability": float(
            getattr(cfg, "COMMANDER_LOTTERY_MIN_PROFIT_PROB", 0.80)
        ),
        "min_spike_ratio": float(
            getattr(cfg, "COMMANDER_LOTTERY_MIN_SPIKE_RATIO", 2.0)
        ),
        "min_scan_score": float(
            getattr(cfg, "COMMANDER_LOTTERY_MIN_SCAN_SCORE", 70.0)
        ),
        "max_fakeout_risk": float(
            getattr(cfg, "COMMANDER_LOTTERY_MAX_FAKEOUT", 0.25)
        ),
        "max_trip_loss_pct": float(
            getattr(cfg, "COMMANDER_MAX_TRIP_LOSS_PCT", 3.0)
        ),
    }


def commander_runtime_context() -> str:
    return RUNTIME_DOCTRINE


def _learn_cache_ready() -> bool:
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "halim/data/learn_cache"
        if not root.is_dir():
            return False
        return any(p.name.startswith("commander:ib_report_") for p in root.iterdir())
    except Exception:
        return False


def ensure_commander_runtime(
    cfg: Optional[BotConfig] = None,
    *,
    replay: bool = False,
    refresh_learn_cache: bool = True,
) -> Dict[str, Any]:
    """
    Idempotent startup hook for ScalperRunner (live + replay).
    Loads learn-cache sections if missing; applies runtime doctrine log line.
    """
    global _LOADED
    cfg = cfg or BotConfig()
    if not commander_runtime_enabled(cfg):
        return {"ok": False, "reason": "disabled"}

    learn_ok = _learn_cache_ready()
    if refresh_learn_cache and not learn_ok:
        try:
            from core.halim_commander_report_learn import consume_commander_report
            consume_commander_report(
                force_gold=False,
                seed_buffer=False,
                export_action_gold=False,
            )
            learn_ok = True
        except Exception as exc:
            log.debug(f"Commander runtime learn-cache: {exc}")

    floors = commander_entry_floors(cfg)
    mode = "replay" if replay else "live"
    if not _LOADED:
        log.info(
            f"🧭 Commander runtime ON ({mode}) — "
            f"lottery prob≥{floors.get('min_profit_probability', 0):.0%} "
            f"spike≥{floors.get('min_spike_ratio', 0):.1f}x | "
            f"execution lessons (no ticker bans)"
        )
        _LOADED = True

    return {
        "ok": True,
        "mode": mode,
        "learn_cache": learn_ok,
        "floors": floors,
        "doctrine": RUNTIME_DOCTRINE[:200],
    }
