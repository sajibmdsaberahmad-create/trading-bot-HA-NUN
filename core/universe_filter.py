#!/usr/bin/env python3
"""
core/universe_filter.py — Profit-hunt universe: major US listings only.

Filters PINK/OTC/ARCAEDGE and distressed tickers at scanner lock time so the
bot never wastes streams or historical requests on untradeable junk.
"""

from __future__ import annotations

from typing import Optional, Tuple

from core.config import BotConfig

# Liquid US listing venues — NASDAQ, NYSE, listed ETFs on ARCA, etc.
ALLOWED_PRIMARY_EXCHANGES = frozenset({
    "NASDAQ", "NYSE", "ARCA", "BATS", "AMEX", "NMS", "ISLAND", "IEX",
    "BYX", "EDGX", "EDGA", "DRCTEDGE", "MEMX", "PEARL", "PEARLQ", "SAPPHIRE",
})

# Pink sheets, OTC, grey market, permission-blocked routes
BLOCKED_PRIMARY_EXCHANGES = frozenset({
    "PINK", "OTC", "OTCBB", "OTCQB", "OTCQX", "OTCMKTS",
    "GREY", "GRAY", "ARCAEDGE", "VALUE", "PINX", "PS", "EXOTIC",
})

# Scanner codes that surface liquid giants + momentum pennies on major venues
PROFIT_HUNT_SCAN_CODES = (
    "MOST_ACTIVE",
    "HOT_BY_VOLUME",
    "TOP_PERC_GAIN",
    "HOT_BY_PRICE",
    "TOP_VOLUME",
)

EXCHANGE_SCORE_BONUS = {
    "NASDAQ": 10.0,
    "NYSE": 10.0,
    "ARCA": 6.0,
    "BATS": 5.0,
    "AMEX": 4.0,
}


def major_exchanges_only(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "PROFIT_HUNT_MAJOR_EXCHANGES_ONLY", True))


def ticker_red_flag(ticker: str) -> Optional[str]:
    """Distressed / OTC-style symbols without reliable IB data."""
    t = (ticker or "").upper().strip()
    if not t:
        return "empty"
    if len(t) > 5:
        return "too_long"
    if "." in t or "-" in t:
        return "otc_suffix"
    # Bankruptcy / liquidation tickers (e.g. TMPOQ)
    if t.endswith("Q") and len(t) >= 4:
        return "bankruptcy_q"
    return None


def normalize_exchange(exchange: str) -> str:
    return (exchange or "").upper().strip()


def passes_profit_hunt_universe(
    cfg: BotConfig,
    ticker: str,
    primary_exchange: str = "",
    *,
    price: float = 0.0,
) -> Tuple[bool, str]:
    """
    True when ticker is suitable for fast profit hunting on major US venues.
    """
    t = (ticker or "").upper().strip()
    if not t:
        return False, "empty"

    flag = ticker_red_flag(t)
    if flag:
        return False, flag

    prim = normalize_exchange(primary_exchange)
    if prim in BLOCKED_PRIMARY_EXCHANGES:
        return False, f"blocked_exchange:{prim}"

    if major_exchanges_only(cfg):
        if prim and prim not in ALLOWED_PRIMARY_EXCHANGES:
            return False, f"not_major:{prim}"
        if not prim:
            # Strict: unknown venue — likely OTC that IB didn't label yet
            if getattr(cfg, "PROFIT_HUNT_REJECT_UNKNOWN_EXCHANGE", True):
                return False, "unknown_exchange"

    min_px = float(getattr(cfg, "PROFIT_HUNT_MIN_PRICE", 0.50))
    max_px = float(getattr(cfg, "PROFIT_HUNT_MAX_PRICE", 500.0))
    if price > 0:
        if price < min_px:
            return False, f"below_min_price:{price:.2f}"
        if price > max_px:
            return False, f"above_max_price:{price:.2f}"

    return True, ""


def exchange_score_bonus(primary_exchange: str) -> float:
    return float(EXCHANGE_SCORE_BONUS.get(normalize_exchange(primary_exchange), 0.0))


def is_tradeable_ticker(
    ticker: str,
    exchange: str = "",
    cfg: Optional[BotConfig] = None,
) -> bool:
    """Backward-compatible gate — delegates to profit-hunt universe rules."""
    cfg = cfg or BotConfig()
    ok, _ = passes_profit_hunt_universe(cfg, ticker, exchange)
    return ok


def filter_profit_hunt_universe(
    cfg: BotConfig,
    items: list,
    *,
    ticker_key: str = "ticker",
    exchange_key: str = "primary_exchange",
) -> list:
    """Filter list of dicts or objects with ticker + optional exchange."""
    out = []
    for item in items:
        if isinstance(item, str):
            t, ex, px = item, "", 0.0
        elif isinstance(item, dict):
            t = str(item.get(ticker_key, ""))
            ex = str(item.get(exchange_key, item.get("exchange", "")))
            px = float(item.get("price", 0) or 0)
        else:
            t = str(getattr(item, ticker_key, ""))
            ex = str(getattr(item, "primary_exchange", getattr(item, "exchange", "")))
            px = float(getattr(item, "price", 0) or 0)
        ok, reason = passes_profit_hunt_universe(cfg, t, ex, price=px)
        if ok:
            out.append(item)
        else:
            from core.notify import log
            log.debug(f"  ⏭ universe skip {t}: {reason}")
    return out
