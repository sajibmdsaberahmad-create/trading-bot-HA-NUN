#!/usr/bin/env python3
"""
core/scan_lock_pools.py — Kill-fit lock pools (penny / mid / large) and tiered vol gates.

Score-first selection across price tiers; soft merge keeps top performers while
refreshing weak slots from live scanner without nuking the whole lock.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

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


def tier_penny_max_price(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("SCAN_TIER_PENNY_MAX_PRICE", 5.0)


def tier_mid_max_price(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("SCAN_TIER_MID_MAX_PRICE", 100.0)


def tier_large_min_price(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("SCAN_TIER_LARGE_MIN_PRICE", 100.0)


def mega_cap_min_price(cfg: Optional[BotConfig] = None) -> float:
    return tier_large_min_price(cfg)


def mega_cap_lock_slots(cfg: Optional[BotConfig] = None) -> int:
    return _env_int("SCAN_LOCK_LARGE_MIN_SLOTS", 2)


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


def tier_name(price: float, cfg: Optional[BotConfig] = None) -> str:
    px = float(price or 0)
    if px <= 0:
        return "mid"
    if px < tier_penny_max_price(cfg):
        return "penny"
    if px < tier_large_min_price(cfg):
        return "mid"
    return "large"


def is_mega_cap_row(
    row: Dict[str, Any],
    hits: Optional[Dict] = None,
    cfg: Optional[BotConfig] = None,
) -> bool:
    return tier_name(row_price(row, hits), cfg) == "large"


def kill_fit_score(
    row: Dict[str, Any],
    hits: Optional[Dict] = None,
    cfg: Optional[BotConfig] = None,
) -> float:
    """Scanner score + small tier bonus — liquidity diversity without rigid slots."""
    base = float(row.get("total_score", 0) or 0)
    tier = tier_name(row_price(row, hits), cfg)
    bonus = {
        "large": _env_float("SCAN_KILL_FIT_LARGE_BONUS", 4.0),
        "mid": _env_float("SCAN_KILL_FIT_MID_BONUS", 2.0),
        "penny": 0.0,
    }.get(tier, 0.0)
    return base + bonus


def tiered_min_spike_ratio(cfg: Optional[BotConfig], price: float) -> float:
    """Price-tier vol floor — lower bar on liquid large caps."""
    px = float(price or 0)
    if px <= 0:
        return _env_float("SNIPER_MIN_ENTRY_SPIKE_RATIO", 1.15)
    tier = tier_name(px, cfg)
    if tier == "penny":
        return _env_float("SCAN_TIER_PENNY_MIN_SPIKE", 2.0)
    if tier == "mid":
        return _env_float("SCAN_TIER_MID_MIN_SPIKE", 1.6)
    return _env_float("SCAN_TIER_MEGA_MIN_SPIKE", 1.35)


def tiered_min_scan_score(cfg: Optional[BotConfig], price: float) -> float:
    px = float(price or 0)
    if px <= 0:
        return _env_float("SNIPER_MIN_ENTRY_SCAN_SCORE", 38.0)
    tier = tier_name(px, cfg)
    if tier == "penny":
        return _env_float("SCAN_TIER_PENNY_MIN_SCORE", 70.0)
    if tier == "mid":
        return _env_float("SCAN_TIER_MID_MIN_SCORE", 65.0)
    return _env_float("SCAN_TIER_MEGA_MIN_SCORE", 60.0)


def _tier_budgets(max_locked: int, cfg: Optional[BotConfig] = None) -> Dict[str, int]:
    penny_max = max(0, int(max_locked * _env_float("SCAN_LOCK_PENNY_MAX_SHARE", 0.45)))
    large_min = min(max_locked, _env_int("SCAN_LOCK_LARGE_MIN_SLOTS", 2))
    mid_min = min(max_locked, _env_int("SCAN_LOCK_MID_MIN_SLOTS", 2))
    return {"penny_max": penny_max, "large_min": large_min, "mid_min": mid_min}


def build_kill_fit_lock_pool(
    cfg: BotConfig,
    pool: List[Dict[str, Any]],
    max_locked: int,
    hits: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """
    Tier-balanced kill pool: reserve mid/large lanes, cap penny share, fill by kill_fit.
    """
    if not pool or max_locked <= 0:
        return []
    if not dual_lock_pool_enabled(cfg):
        ranked = sorted(pool, key=lambda r: kill_fit_score(r, hits, cfg), reverse=True)
        return ranked[:max_locked]

    buckets: Dict[str, List[Dict[str, Any]]] = {"penny": [], "mid": [], "large": []}
    for row in pool:
        buckets[tier_name(row_price(row, hits), cfg)].append(row)
    for tier in buckets:
        buckets[tier].sort(key=lambda r: kill_fit_score(r, hits, cfg), reverse=True)

    budgets = _tier_budgets(max_locked, cfg)
    picked: List[Dict[str, Any]] = []
    used: set = set()

    def take(from_tier: str, n: int) -> None:
        nonlocal picked
        for row in buckets[from_tier]:
            if n <= 0 or len(picked) >= max_locked:
                break
            tk = str(row.get("ticker", "")).upper()
            if tk and tk not in used:
                picked.append(row)
                used.add(tk)
                n -= 1

    take("large", budgets["large_min"])
    take("mid", budgets["mid_min"])
    take("penny", budgets["penny_max"])

    rest: List[Dict[str, Any]] = []
    for tier in ("large", "mid", "penny"):
        for row in buckets[tier]:
            tk = str(row.get("ticker", "")).upper()
            if tk and tk not in used:
                rest.append(row)
    rest.sort(key=lambda r: kill_fit_score(r, hits, cfg), reverse=True)
    for row in rest:
        if len(picked) >= max_locked:
            break
        tk = str(row.get("ticker", "")).upper()
        if tk and tk not in used:
            picked.append(row)
            used.add(tk)

    picked.sort(key=lambda r: kill_fit_score(r, hits, cfg), reverse=True)
    return picked[:max_locked]


def build_dual_lock_pool(
    cfg: BotConfig,
    pool: List[Dict[str, Any]],
    max_locked: int,
    hits: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """Backward-compatible alias."""
    return build_kill_fit_lock_pool(cfg, pool, max_locked, hits)


def merge_kill_fit_lock_pool(
    cfg: BotConfig,
    current_rows: List[Dict[str, Any]],
    fresh_rows: List[Dict[str, Any]],
    max_locked: int,
    hits: Optional[Dict] = None,
    protect_top_n: Optional[int] = None,
    max_swaps: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """
    Merge fresh scanner hits into an existing lock without clearing top names.
    Returns (merged_rows, added_tickers, removed_tickers).
    """
    if max_locked <= 0:
        return [], [], []
    protect_top_n = protect_top_n if protect_top_n is not None else _env_int(
        "SCAN_SOFT_ROTATE_PROTECT", 5,
    )
    max_swaps = max_swaps if max_swaps is not None else _env_int("SCAN_MERGE_MAX_SWAPS", 1)
    margin = _env_float("SCAN_SOFT_ROTATE_SCORE_MARGIN", 5.0)

    if not current_rows:
        merged = build_kill_fit_lock_pool(cfg, fresh_rows, max_locked, hits)
        return merged, [str(r.get("ticker", "")).upper() for r in merged], []

    current_sorted = sorted(
        current_rows, key=lambda r: kill_fit_score(r, hits, cfg), reverse=True,
    )
    lock_tickers = {str(r.get("ticker", "")).upper() for r in current_rows}
    fresh_unique = [
        r for r in fresh_rows
        if str(r.get("ticker", "")).upper() not in lock_tickers
    ]
    fresh_unique.sort(key=lambda r: kill_fit_score(r, hits, cfg), reverse=True)

    added: List[str] = []
    removed: List[str] = []
    working = list(current_sorted)

    # Fill open slots first (after soft rotate drops)
    if len(working) < max_locked:
        for row in fresh_unique:
            if len(working) >= max_locked:
                break
            tk = str(row.get("ticker", "")).upper()
            if tk:
                working.append(row)
                added.append(tk)
        merged = build_kill_fit_lock_pool(cfg, working, max_locked, hits)
        return merged, added, removed

    # Full lock: upgrade at most max_swaps if fresh clearly beats weak tail
    protected = working[:protect_top_n]
    tail = working[protect_top_n:]
    swaps = 0
    for fresh in fresh_unique:
        if swaps >= max_swaps or not tail:
            break
        fresh_score = kill_fit_score(fresh, hits, cfg)
        worst_idx = min(
            range(len(tail)),
            key=lambda i: kill_fit_score(tail[i], hits, cfg),
        )
        worst = tail[worst_idx]
        worst_score = kill_fit_score(worst, hits, cfg)
        if fresh_score < worst_score + margin:
            continue
        old_tk = str(worst.get("ticker", "")).upper()
        new_tk = str(fresh.get("ticker", "")).upper()
        if not old_tk or not new_tk:
            continue
        tail[worst_idx] = fresh
        removed.append(old_tk)
        added.append(new_tk)
        swaps += 1

    merged = build_kill_fit_lock_pool(cfg, protected + tail, max_locked, hits)
    return merged, added, removed


def tier_pool_summary(
    locked: List[Dict[str, Any]],
    hits: Optional[Dict] = None,
    cfg: Optional[BotConfig] = None,
) -> str:
    if not dual_lock_pool_enabled(cfg) or not locked:
        return ""
    counts = {"penny": 0, "mid": 0, "large": 0}
    for row in locked:
        counts[tier_name(row_price(row, hits), cfg)] += 1
    return (
        f" | pool: {counts['large']} large + {counts['mid']} mid + "
        f"{counts['penny']} penny"
    )


def dual_pool_summary(
    locked: List[Dict[str, Any]],
    hits: Optional[Dict] = None,
    cfg: Optional[BotConfig] = None,
) -> str:
    return tier_pool_summary(locked, hits, cfg)
