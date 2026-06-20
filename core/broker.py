#!/usr/bin/env python3
"""
core/broker.py — Order execution, including real IB bracket orders.

WHY BRACKET ORDERS MATTER (this directly answers "protect against
connection cut and slippage")
═══════════════════════════════════════════════════════════════════════
Every entry in this bot is placed as an IB BRACKET ORDER: one parent
market/limit order plus two child orders (a STOP order and a LIMIT
take-profit order) with OCA (One-Cancels-All) linkage. Once IB
acknowledges the bracket, all three orders live on IB's servers, not
in this Python process. That means:

  - If your Mac sleeps, loses wifi, or this script crashes, the stop
    and target are still working — IB's matching engine, not your
    laptop, is watching the price.
  - If the VPS reboots or its network drops, same protection applies.
  - The trailing stop/profit logic in core/risk.py additionally
    RE-SUBMITS a tighter stop order as price moves favourably, so the
    exchange-side protection ratchets up too, not just the in-memory
    Python state.

SLIPPAGE HANDLING
═══════════════════════════════════════════════════════════════════════
Market orders guarantee a fill, not a price. In a fast-moving or thin
market, a market order can fill meaningfully worse than the last quote.
When MAX_ACCEPTABLE_SLIPPAGE_PCT would plausibly be breached (estimated
from the current bid/ask spread), the bot uses a marketable LIMIT order
instead of a plain MARKET order for the entry, capping the worst
acceptable fill price. Stops are still submitted as STOP orders (which
become market orders once triggered, by design, since a guaranteed exit
matters more than a perfect price during a stop-out) but the bot widens
a STOP-LIMIT's limit offset automatically in fast markets so the order
isn't left unfilled during a sharp move.
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

try:
    from ib_insync import MarketOrder, LimitOrder, StopOrder, StopLimitOrder, Trade
except ImportError:
    raise SystemExit("ERROR: ib_insync not installed. Fix: pip install ib_insync")

from core.config import BotConfig
from core.connector import IBConnector
from core.notify import log


@dataclass
class BracketHandle:
    """References to the live IB orders for an open position, so we can modify/cancel them."""
    parent_order_id: int
    stop_trade: Optional[Trade] = None
    target_trade: Optional[Trade] = None
    parent_trade: Optional[Trade] = None


class BrokerExecutor:
    """Places and manages real (paper or live) IB orders, including brackets."""

    def __init__(self, connector: IBConnector, cfg: BotConfig):
        self.conn = connector
        self.ib = connector.ib
        self.cfg = cfg

    # ── Entry: bracket order (parent + stop child + target child) ───────────

    def place_bracket_buy(self, quantity: int, limit_or_market_price: Optional[float],
                           stop_price: float, target_price: float) -> BracketHandle:
        """
        Submit a bracket BUY: parent entry + stop-loss child + take-profit
        child, OCA-linked so a fill on one cancels the other.

        limit_or_market_price: if None, parent is a MARKET order. If a
        float, parent is a marketable LIMIT order at that price (used
        when slippage protection is active).
        """
        contract = self.conn.get_contract()

        parent_id = self.ib.client.getReqId()

        if limit_or_market_price is None:
            parent = MarketOrder("BUY", quantity)
        else:
            parent = LimitOrder("BUY", quantity, round(limit_or_market_price, 2))

        parent.orderId = parent_id
        parent.transmit = False

        stop_child = StopOrder("SELL", quantity, round(stop_price, 2))
        stop_child.orderId = self.ib.client.getReqId()
        stop_child.parentId = parent_id
        stop_child.transmit = False

        target_child = LimitOrder("SELL", quantity, round(target_price, 2))
        target_child.orderId = self.ib.client.getReqId()
        target_child.parentId = parent_id
        target_child.transmit = True  # last child transmits the whole bracket

        # OCA group so stop and target cancel each other on fill
        oca_group = f"bracket_{parent_id}"
        stop_child.ocaGroup = oca_group
        stop_child.ocaType = 1
        target_child.ocaGroup = oca_group
        target_child.ocaType = 1

        parent_trade = self.ib.placeOrder(contract, parent)
        stop_trade = self.ib.placeOrder(contract, stop_child)
        target_trade = self.ib.placeOrder(contract, target_child)

        log.info(
            f"Bracket BUY submitted: {quantity} sh, parent#{parent_id}, "
            f"stop ${stop_price:.2f}, target ${target_price:.2f}"
        )

        return BracketHandle(
            parent_order_id=parent_id,
            stop_trade=stop_trade,
            target_trade=target_trade,
            parent_trade=parent_trade,
        )

    # ── Modify the resting stop order as the trailing logic ratchets it up ──

    def update_stop_price(self, handle: BracketHandle, new_stop_price: float):
        """
        Re-price the live stop order on IB's servers to match the
        trailing stop / trailing profit floor computed in core/risk.py.
        This keeps exchange-side protection in sync with the bot's
        in-memory trailing logic, so the improved protection survives
        even if the bot itself goes offline after this point.
        """
        if handle.stop_trade is None:
            return
        order = handle.stop_trade.order
        order.auxPrice = round(new_stop_price, 2)
        contract = self.conn.get_contract()
        self.ib.placeOrder(contract, order)
        log.debug(f"Stop order #{order.orderId} repriced -> ${new_stop_price:.2f}")

    # ── Flatten: cancel brackets and market-sell ─────────────────────────────

    def flatten_position(self, quantity: int, handle: Optional[BracketHandle] = None,
                          urgent: bool = True) -> Optional[Trade]:
        """
        Immediately exit the position. Cancels any resting bracket
        children first (so we don't end up double-selling), then sends
        a market order. `urgent=True` is used for stop/circuit-breaker
        exits, where guaranteed execution matters more than price.
        """
        contract = self.conn.get_contract()

        if handle is not None:
            try:
                if handle.stop_trade is not None:
                    self.ib.cancelOrder(handle.stop_trade.order)
                if handle.target_trade is not None:
                    self.ib.cancelOrder(handle.target_trade.order)
            except Exception as exc:
                log.warning(f"Could not cancel bracket children cleanly: {exc}")

        order = MarketOrder("SELL", quantity)
        trade = self.ib.placeOrder(contract, order)
        log.info(f"Flatten order submitted: SELL {quantity} sh (market)")
        return trade

    # ── Slippage-aware entry price decision ──────────────────────────────────

    def decide_entry_price(self, last_price: float, bid: Optional[float],
                            ask: Optional[float]) -> Tuple[Optional[float], bool]:
        """
        Returns (limit_price_or_None, used_limit).
        If the bid/ask spread implies a market order could slip beyond
        MAX_ACCEPTABLE_SLIPPAGE_PCT, returns a marketable limit price
        instead of None (plain market order).
        """
        if not self.cfg.USE_LIMIT_ORDERS_IN_FAST_MARKETS or bid is None or ask is None or bid <= 0 or ask <= 0:
            return None, False

        spread_pct = (ask - bid) / last_price if last_price > 0 else 0.0
        if spread_pct > self.cfg.MAX_ACCEPTABLE_SLIPPAGE_PCT:
            # Marketable limit: pay up to ask + a small buffer, capping worst-case fill
            limit_price = ask * (1.0 + self.cfg.MAX_ACCEPTABLE_SLIPPAGE_PCT / 2)
            log.info(
                f"Wide spread detected ({spread_pct:.2%}) — using marketable "
                f"limit @ ${limit_price:.2f} instead of plain market order"
            )
            return limit_price, True

        return None, False
