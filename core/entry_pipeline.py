#!/usr/bin/env python3
"""
core/entry_pipeline.py — IB-confirmed entry fill detection (extracted from scalper_runner).

Parent-only extended-hours orders, position-delta fills, and poll state helpers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from core.market_hours import should_defer_bracket_children, should_use_extended_hours_orders
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


def flatten_exit_limit_px(
    cfg,
    broker,
    bid: float,
    ask: float,
    ref_px: float,
    shares: int,
) -> Tuple[float, str]:
    """Aggressive SELL limit for extended hours — bare MARKET stalls PreSubmitted."""
    if ref_px <= 0:
        return 0.0, "invalid"

    penny_thr = float(getattr(cfg, "PENNY_PRICE_THRESHOLD", 1.0))
    is_penny = ref_px < penny_thr
    reg_pct = float(getattr(cfg, "IB_REGULATORY_LIMIT_PCT", 0.01))
    ib_min = ref_px * (1.0 - reg_pct)

    ref = bid if bid and bid > 0 else ref_px
    buf = float(
        getattr(cfg, "PENNY_LIMIT_BUFFER_PCT", 0.006)
        if is_penny
        else getattr(
            cfg,
            "EXIT_LIMIT_BUFFER_PCT",
            getattr(cfg, "ENTRY_LIMIT_BUFFER_PCT", 0.004),
        )
    )
    limit = max(ref * (1.0 - buf), ib_min)
    limit = min(limit, ref_px)

    if bid and ask and ask > bid > 0 and ref_px > 0:
        spread_pct = (ask - bid) / ref_px
        wide = float(getattr(cfg, "MAX_ACCEPTABLE_SLIPPAGE_PCT", 0.004)) * 2
        if spread_pct > wide:
            return broker._round_price(max(bid, ib_min)), "limit_wide_spread_sell"

    mode = "limit_penny_sell" if is_penny else "limit_ext_hours_sell"
    return broker._round_price(limit), mode


def cover_buy_limit_px(
    cfg,
    broker,
    bid: float,
    ask: float,
    ref_px: float,
    shares: int,
) -> Tuple[float, str]:
    """Aggressive BUY limit to cover shorts outside RTH — bare MARKET stalls."""
    if ref_px <= 0:
        return 0.0, "invalid"

    reg_pct = float(getattr(cfg, "IB_REGULATORY_LIMIT_PCT", 0.01))
    ib_max = ref_px * (1.0 + reg_pct)
    ref = ask if ask and ask > 0 else ref_px
    buf = float(getattr(cfg, "ENTRY_LIMIT_BUFFER_PCT", 0.004))
    limit = min(ref * (1.0 + buf), ib_max)
    limit = max(limit, ref_px * 1.0005)
    return broker._round_price(limit), "limit_cover_buy"


def flatten_order_for_session(
    cfg,
    broker,
    quantity: int,
    last_price: float,
    bid: Optional[float],
    ask: Optional[float],
):
    """
    Pick flatten SELL order type. Extended hours / penny / thin-book: marketable LIMIT.
    IB paper parent MARKET orders stall in PreSubmitted outside RTH.
    """
    from ib_insync import LimitOrder, MarketOrder

    penny_thr = float(getattr(cfg, "PENNY_PRICE_THRESHOLD", 1.0))
    is_penny = last_price > 0 and last_price < penny_thr
    max_market_sh = int(getattr(cfg, "MAX_MARKET_ENTRY_SHARES", 400))
    use_limit = (
        should_defer_bracket_children(cfg)
        or should_use_extended_hours_orders(cfg)
        or is_penny
        or quantity > max_market_sh
    )

    if use_limit and last_price > 0:
        limit_px, mode = flatten_exit_limit_px(
            cfg,
            broker,
            float(bid or 0),
            float(ask or 0),
            last_price,
            quantity,
        )
        if limit_px > 0:
            return LimitOrder("SELL", quantity, limit_px), mode

    return MarketOrder("SELL", quantity), "market"


def cover_order_for_session(
    cfg,
    broker,
    quantity: int,
    last_price: float,
    bid: Optional[float],
    ask: Optional[float],
):
    """Pick orphan-short cover BUY order — limit outside RTH / penny."""
    from ib_insync import LimitOrder, MarketOrder

    penny_thr = float(getattr(cfg, "PENNY_PRICE_THRESHOLD", 1.0))
    is_penny = last_price > 0 and last_price < penny_thr
    use_limit = (
        should_defer_bracket_children(cfg)
        or should_use_extended_hours_orders(cfg)
        or is_penny
    )
    if use_limit and last_price > 0:
        limit_px, mode = cover_buy_limit_px(
            cfg,
            broker,
            float(bid or 0),
            float(ask or 0),
            last_price,
            quantity,
        )
        if limit_px > 0:
            return LimitOrder("BUY", quantity, limit_px), mode
    return MarketOrder("BUY", quantity), "market"
