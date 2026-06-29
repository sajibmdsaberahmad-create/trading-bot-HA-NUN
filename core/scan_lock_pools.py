#!/usr/bin/env python3
"""
core/scan_lock_pools.py — Dual lock pool (lottery + blue-chip) and tiered vol gates.

Reserves mega-cap slots in the lock list; spike/score floors scale by price tier.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.config import BotConfig


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def dual_lock_pool_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return _env_bool("SCAN_LOCK_DUAL_POOL", "true")


def mega_cap_min_price(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("SCAN_MEGA_CAP_MIN_PRICE", 20.0)


def mega_cap_lock_slots(cfg: Optional[BotConfig] = None) -> int:
    return _env_int("SCAN_LOCK_MEGA_CAP_SLOTS", 3)


def tier_penny_max_price(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("SCAN_TIER_PENNY_MAX_PRICE", 5.0)


def tier_mid_max_price(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("SCAN_TIER_MID_MAX_PRICE", 50.0)


def row_price(row: Dict[str, Any], hits: Optional[Dict] = None) -> float:
    px = float(row.get("price", 0) or 0)
    if px > 0:
        return px
    ticker = str(row.get("ticker", "")).upper()
    if hits and ticker:
        hit = hits.get(ticker)
        if hit is not None:
            px = float(getattr(hit, "price", 0) or 0)
            if px > 0:
                return px
    return 0.0


def is_mega_cap_row(
    row: Dict[str, Any],
    hits: Optional[Dict] = None,
    cfg: Optional[BotConfig] = None,
) -> bool:
    px = row_price(row, hits)
    return px >= mega_cap_min_price(cfg)


def tiered_min_spike_ratio(cfg: Optional[BotConfig], price: float) -> float:
    """Price-tier vol floor — lower bar on liquid large caps."""
    px = float(price or 0)
    if px <= 0:
        return _env_float("SNIPER_MIN_ENTRY_SPIKE_RATIO", 1.15)
    penny_max = tier_penny_max_price(cfg)
    mid_max = tier_mid_max_price(cfg)
    if px < penny_max:
        return _env_float("SCAN_TIER_PENNY_MIN_SPIKE", 2.0)
    if px < mid_max:
        return _env_float("SCAN_TIER_MID_MIN_SPIKE", 1.6)
    return _env_float("SCAN_TIER_MEGA_MIN_SPIKE", 1.35)


def tiered_min_scan_score(cfg: Optional[BotConfig], price: float) -> float:
    px = float(price or 0)
    if px <= 0:
        return _env_float("SNIPER_MIN_ENTRY_SCAN_SCORE", 38.0)
    penny_max = tier_penny_max_price(cfg)
    mid_max = tier_mid_max_price(cfg)
    if px < penny_max:
        return _env_float("SCAN_TIER_PENNY_MIN_SCORE", 70.0)
    if px < mid_max:
        return _env_float("SCAN_TIER_MID_MIN_SCORE", 65.0)
    return _env_float("SCAN_TIER_MEGA_MIN_SCORE", 60.0)


def build_dual_lock_pool(
    cfg: BotConfig,
    pool: List[Dict[str, Any]],
    max_locked: int,
    hits: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """
    Reserve mega-cap slots, fill remainder with lottery names; final order by score.
    """
    if not pool or max_locked <= 0:
        return []
    if not dual_lock_pool_enabled(cfg):
        return pool[:max_locked]

    mega_min = mega_cap_min_price(cfg)
    mega_slots = min(mega_cap_lock_slots(cfg), max_locked)

    mega: List[Dict[str, Any]] = []
    lottery: List[Dict[str, Any]] = []
    for row in pool:
        px = row_price(row, hits)
        if px >= mega_min:
            mega.append(row)
        else:
            lottery.append(row)

    mega.sort(key=lambda x: float(x.get("total_score", 0)), reverse=True)
    lottery.sort(key=lambda x: float(x.get("total_score", 0)), reverse=True)

    picked: List[Dict[str, Any]] = []
    used: set = set()
    for row in mega[:mega_slots]:
        tk = str(row.get("ticker", "")).upper()
        if tk and tk not in used:
            picked.append(row)
            used.add(tk)

    for row in lottery:
        if len(picked) >= max_locked:
            break
        tk = str(row.get("ticker", "")).upper()
        if tk and tk not in used:
            picked.append(row)
            used.add(tk)

    # Backfill if mega lane thin
    if len(picked) < max_locked:
        for row in mega[mega_slots:]:
            if len(picked) >= max_locked:
                break
            tk = str(row.get("ticker", "")).upper()
            if tk and tk not in used:
                picked.append(row)
                used.add(tk)

    picked.sort(key=lambda x: float(x.get("total_score", 0)), reverse=True)
    return picked[:max_locked]


def dual_pool_summary(
    locked: List[Dict[str, Any]],
    hits: Optional[Dict] = None,
    cfg: Optional[BotConfig] = None,
) -> str:
    if not dual_lock_pool_enabled(cfg):
        return ""
    mega_n = sum(1 for r in locked if is_mega_cap_row(r, hits, cfg))
    lot_n = len(locked) - mega_n
    return f" | pool: {mega_n} mega + {lot_n} lottery"
