#!/usr/bin/env python3
"""
core/entry_pipeline.py — IB-confirmed entry fill detection (extracted from scalper_runner).

Parent-only extended-hours orders, position-delta fills, and poll state helpers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from core.market_hours import should_defer_bracket_children
from core.fill_tracker import (
    confirm_entry_fill,
    ib_position_shares,
    require_ib_fill_sync,
)

if TYPE_CHECKING:
    from ib_insync import IB


def ib_position_shares_for(ib: "IB", ticker: str) -> float:
    return ib_position_shares(ib, ticker)


def new_entry_poll_state(
    *,
    ticker: str,
    shares: int,
    plan: Any,
    current_px: float,
    entry_parent_px: Optional[float],
    fill_polls: int,
    min_fill_ratio: float,
    fail_cd: float,
    attempt: int,
    last_ib_error: Any,
    bracket: Any,
    ib: "IB",
) -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "shares": shares,
        "plan": plan,
        "fill_px": current_px,
        "limit_px": entry_parent_px,
        "ib_pos_baseline": ib_position_shares(ib, ticker),
        "polls": 0,
        "max_polls": fill_polls,
        "min_fill_ratio": min_fill_ratio,
        "fail_cd": fail_cd,
        "attempt": attempt,
        "last_ib_error": last_ib_error,
        "bracket": bracket,
        "started_at": __import__("time").time(),
        "last_heartbeat": 0.0,
    }


def confirm_entry_fill_from_ib(
    ib: "IB",
    *,
    ticker: str,
    st: Dict[str, Any],
    bracket: Any,
    shares: int,
    min_fill_ratio: float,
    quote_px: float,
    fill_cache=None,
    ib_sync_enabled: Optional[bool] = None,
) -> Tuple[float, float, bool, str]:
    """IB-confirmed entry — never treat orphan paper holdings as a new fill."""
    if ib_sync_enabled is None:
        ib_sync_enabled = require_ib_fill_sync()

    if not ib_sync_enabled:
        parent_trade = getattr(bracket, "parent_trade", None)
        filled = 0.0
        fill_px = quote_px
        if parent_trade and parent_trade.orderStatus:
            filled = float(parent_trade.orderStatus.filled or 0)
            avg = float(parent_trade.orderStatus.avgFillPrice or 0)
            if avg > 0:
                fill_px = avg
        status = (
            parent_trade.orderStatus.status
            if parent_trade and parent_trade.orderStatus else ""
        )
        if filled >= shares * min_fill_ratio or status == "Filled":
            return filled or float(shares), fill_px, True, "legacy"
        return 0.0, 0.0, False, ""

    return confirm_entry_fill(
        ib,
        symbol=ticker,
        parent_trade=getattr(bracket, "parent_trade", None),
        cache=fill_cache,
        order_shares=float(shares),
        min_fill_ratio=min_fill_ratio,
        ib_pos_baseline=float(st.get("ib_pos_baseline", 0)),
        started_at=float(st.get("started_at", 0)),
        quote_px=quote_px,
    )


def entry_price_mode_for_session(
    cfg,
    broker,
    current_px: float,
    bid: float,
    ask: float,
    shares: int,
    avg_volume: float,
) -> Tuple[Optional[float], str]:
    """
    Paper RTH: MARKET by default. Extended hours: aggressive LIMIT only —
    IB paper parent MARKET orders stall in PreSubmitted outside RTH.
    """
    if should_defer_bracket_children(cfg):
        limit_px, mode = broker.decide_smart_entry(
            current_px, bid, ask, shares, avg_volume,
        )
        if limit_px and limit_px > 0:
            return limit_px, f"ext_hours_{mode}"
        ref = ask if ask and ask > 0 else current_px
        buf = float(getattr(cfg, "ENTRY_LIMIT_BUFFER_PCT", 0.003))
        return broker._round_price(ref * (1.0 + buf)), "ext_hours_limit_ask"
    if (
        getattr(cfg, "PAPER_TRADING", False)
        and getattr(cfg, "PAPER_MARKET_ENTRIES", True)
    ):
        return None, "paper_market"
    return broker.decide_smart_entry(current_px, bid, ask, shares, avg_volume)


def stuck_entry_limit_px(
    cfg,
    broker,
    bid: float,
    ask: float,
    ref_px: float,
    shares: int,
) -> Tuple[float, str]:
    """Limit price for PreSubmitted recovery — never re-submit bare MARKET ext-hours."""
    limit_px, mode = broker.decide_smart_entry(
        ref_px, bid, ask, shares, 0.0,
    )
    if limit_px and limit_px > 0:
        return limit_px, mode
    ref = ask if ask and ask > 0 else ref_px
    buf = float(getattr(cfg, "ENTRY_LIMIT_BUFFER_PCT", 0.004))
    return broker._round_price(ref * (1.0 + buf)), "limit_chase"
