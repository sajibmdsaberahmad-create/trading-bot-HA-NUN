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
  - The trailing stop/profit logic in core/risk.py RE-SUBMITS bracket children
    via cancel-and-replace (IB does not allow in-place OCA order edits).

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

import time
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any

try:
    from ib_insync import MarketOrder, LimitOrder, StopOrder, StopLimitOrder, Trade
except ImportError:
    raise SystemExit("ERROR: ib_insync not installed. Fix: pip install ib_insync")

from core.config import BotConfig
from core.connector import IBConnector
from core.market_hours import (
    should_use_extended_hours_orders,
    orders_allowed,
    allowed_trading_sessions_label,
)
from core.notify import log


def parse_ib_regulatory_cap(error_string: str) -> Optional[float]:
    """Extract IB price cap from error 2161 text."""
    if not error_string:
        return None
    patterns = (
        r"cap \(or limit\) the price of your Limit Order to\s+([0-9.]+)",
        r"Limit Order to\s+([0-9.]+)",
        r"price of your.*?to\s+([0-9.]+)",
    )
    for pat in patterns:
        m = re.search(pat, error_string, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def parse_ib_order_block(error: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return permanent block reason when IB will not allow new entries."""
    if not error:
        return None
    code = int(error.get("code") or 0)
    msg = (error.get("message") or "").lower()
    if code != 201:
        return None
    if "closing-only" in msg or "closing only" in msg:
        return "IB closing-only — cannot open new positions"
    if "no trading permission" in msg or "ineligible" in msg or "customer ineligible" in msg:
        return "IB trading permission denied"
    if "not available for trading" in msg:
        return "IB symbol not available for trading"
    return "IB order rejected (201)"


@dataclass
class BracketHandle:
    """References to the live IB orders for an open position, so we can modify/cancel them."""
    parent_order_id: int
    stop_trade: Optional[Trade] = None
    target_trade: Optional[Trade] = None
    parent_trade: Optional[Trade] = None
    oca_group: str = ""
    quantity: int = 0
    symbol: str = ""
    last_stop_price: float = 0.0
    last_target_price: float = 0.0
    _last_replace_ts: float = field(default=0.0, repr=False)


class BrokerExecutor:
    """Places and manages real (paper or live) IB orders, including brackets."""

    def __init__(self, connector: IBConnector, cfg: BotConfig):
        self.conn = connector
        self.ib = connector.ib
        self.cfg = cfg

    # ── Entry: bracket order (parent + stop child + target child) ───────────

    def _configure_order(self, order):
        """Apply TIF and extended-hours settings for IB Gateway compatibility."""
        order.tif = "DAY"
        if should_use_extended_hours_orders(self.cfg):
            order.outsideRth = True

    def _session_allows_orders(self, action: str = "order") -> bool:
        allowed, state = orders_allowed(self.cfg)
        if not allowed:
            log.debug(f"IB {action} skipped — session {state} (not tradable)")
        return allowed

    def _round_price(self, price: float) -> float:
        """Sub-dollar stocks need 4dp stops/targets on IB."""
        return round(price, 4) if price < 1.0 else round(price, 2)

    def place_bracket_buy(
        self,
        quantity: int,
        limit_or_market_price: Optional[float],
        stop_price: float,
        target_price: float,
        *,
        symbol: Optional[str] = None,
    ) -> BracketHandle:
        """
        Submit a bracket BUY: parent entry + stop-loss child + take-profit
        child, OCA-linked so a fill on one cancels the other.

        limit_or_market_price: if None, parent is a MARKET order. If a
        float, parent is a marketable LIMIT order at that price (used
        when slippage protection is active).
        """
        if not self._session_allows_orders("bracket"):
            raise RuntimeError("Market session closed — bracket entry blocked")
        contract = self.conn.get_contract(symbol)

        parent_id = self.ib.client.getReqId()

        if limit_or_market_price is None:
            parent = MarketOrder("BUY", quantity)
        else:
            parent = LimitOrder("BUY", quantity, self._round_price(limit_or_market_price))

        self._configure_order(parent)
        parent.orderId = parent_id
        parent.transmit = False

        stop_child = StopOrder("SELL", quantity, self._round_price(stop_price))
        self._configure_order(stop_child)
        stop_child.orderId = self.ib.client.getReqId()
        stop_child.parentId = parent_id
        stop_child.transmit = False

        target_child = LimitOrder("SELL", quantity, self._round_price(target_price))
        self._configure_order(target_child)
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
            oca_group=oca_group,
            quantity=quantity,
            symbol=(symbol or self.cfg.TICKER or getattr(contract, "symbol", "") or "").upper(),
            last_stop_price=self._round_price(stop_price),
            last_target_price=self._round_price(target_price),
        )

    def _contract_for_handle(self, handle: BracketHandle):
        """Always use the bracket's own contract — never cfg.TICKER (multi-position safe)."""
        for trade in (handle.parent_trade, handle.stop_trade, handle.target_trade):
            if trade is not None and getattr(trade, "contract", None) is not None:
                return trade.contract
        sym = (handle.symbol or "").upper()
        if sym:
            return self.conn.get_contract(sym)
        return self.conn.get_contract()

    # ── Cancel + replace bracket children (IB rejects in-place OCA edits: 10326) ──

    _MIN_REPLACE_INTERVAL_SEC = 2.0

    def _order_done(self, trade: Optional[Trade]) -> bool:
        if trade is None or trade.orderStatus is None:
            return True
        return trade.orderStatus.status in ("Cancelled", "Inactive", "Filled", "ApiCancelled")

    def _cancel_and_wait(self, trade: Optional[Trade], timeout: float = 2.0) -> bool:
        if trade is None or self._order_done(trade):
            return True
        try:
            self.ib.cancelOrder(trade.order)
        except Exception as exc:
            log.debug(f"Cancel order #{getattr(trade.order, 'orderId', '?')}: {exc}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._order_done(trade):
                return True
            self.ib.sleep(0.15)
        return self._order_done(trade)

    def _throttle_replace(self, handle: BracketHandle) -> bool:
        now = time.time()
        if now - handle._last_replace_ts < self._MIN_REPLACE_INTERVAL_SEC:
            return False
        handle._last_replace_ts = now
        return True

    def update_stop_price(self, handle: BracketHandle, new_stop_price: float):
        """
        Re-price the live stop on IB via cancel-and-replace.

        IB error 10326 blocks in-place edits to OCA bracket children; we cancel
        the resting stop and submit a new one in the same OCA group.
        """
        if not self._session_allows_orders("stop update"):
            return
        if handle.stop_trade is None:
            return
        rounded = self._round_price(new_stop_price)
        if handle.last_stop_price and abs(rounded - handle.last_stop_price) < 0.0001:
            return
        if not self._throttle_replace(handle):
            return

        old = handle.stop_trade
        old_order = old.order
        qty = int(old_order.totalQuantity or handle.quantity or 0)
        if qty < 1:
            return
        parent_id = int(old_order.parentId or handle.parent_order_id)
        oca_group = handle.oca_group or f"bracket_{handle.parent_order_id}"
        parent_filled = (
            handle.parent_trade is not None
            and handle.parent_trade.orderStatus is not None
            and handle.parent_trade.orderStatus.status == "Filled"
        )

        if not self._cancel_and_wait(old):
            log.debug("Stop cancel pending — skipping replace this cycle")
            return

        stop_child = StopOrder("SELL", qty, rounded)
        self._configure_order(stop_child)
        stop_child.orderId = self.ib.client.getReqId()
        if not parent_filled:
            stop_child.parentId = parent_id
        stop_child.transmit = True
        stop_child.ocaGroup = oca_group
        stop_child.ocaType = 1

        contract = self._contract_for_handle(handle)
        handle.stop_trade = self.ib.placeOrder(contract, stop_child)
        handle.last_stop_price = rounded
        log.debug(f"Stop replaced -> ${rounded:.2f} (order #{stop_child.orderId})")

    def update_target_price(self, handle: BracketHandle, new_target_price: float):
        """Re-price take-profit via cancel-and-replace (same OCA constraint as stop)."""
        if not self._session_allows_orders("target update"):
            return
        if handle.target_trade is None:
            return
        rounded = self._round_price(new_target_price)
        if handle.last_target_price and abs(rounded - handle.last_target_price) < 0.0001:
            return
        if not self._throttle_replace(handle):
            return

        old = handle.target_trade
        old_order = old.order
        qty = int(old_order.totalQuantity or handle.quantity or 0)
        if qty < 1:
            return
        parent_id = int(old_order.parentId or handle.parent_order_id)
        oca_group = handle.oca_group or f"bracket_{handle.parent_order_id}"
        parent_filled = (
            handle.parent_trade is not None
            and handle.parent_trade.orderStatus is not None
            and handle.parent_trade.orderStatus.status == "Filled"
        )

        if not self._cancel_and_wait(old):
            log.debug("Target cancel pending — skipping replace this cycle")
            return

        target_child = LimitOrder("SELL", qty, rounded)
        self._configure_order(target_child)
        target_child.orderId = self.ib.client.getReqId()
        if not parent_filled:
            target_child.parentId = parent_id
        target_child.transmit = True
        target_child.ocaGroup = oca_group
        target_child.ocaType = 1

        contract = self._contract_for_handle(handle)
        handle.target_trade = self.ib.placeOrder(contract, target_child)
        handle.last_target_price = rounded
        log.debug(f"Target replaced -> ${rounded:.2f} (order #{target_child.orderId})")

    # ── Flatten: cancel brackets and market-sell ─────────────────────────────

    def cancel_open_orders_for_symbol(self, symbol: str) -> int:
        """Cancel resting orders for a symbol (prevents duplicate bracket stacks)."""
        cancelled = 0
        sym = (symbol or "").upper()
        if not sym:
            return 0
        try:
            self.ib.reqAllOpenOrders()
            self.ib.sleep(0.3)
            for trade in list(self.ib.openTrades()):
                try:
                    if getattr(trade.contract, "symbol", "").upper() != sym:
                        continue
                    st = trade.orderStatus.status if trade.orderStatus else ""
                    if st in ("Filled", "Cancelled", "Inactive", "ApiCancelled"):
                        continue
                    self.ib.cancelOrder(trade.order)
                    cancelled += 1
                except Exception:
                    pass
            if cancelled:
                self.ib.sleep(0.3)
        except Exception as exc:
            log.debug(f"Cancel orders for {sym}: {exc}")
        return cancelled

    def cancel_stale_open_orders(self) -> int:
        """Cancel all resting orders on startup — clears orphaned brackets from prior runs."""
        cancelled = 0
        try:
            self.ib.reqAllOpenOrders()
            self.ib.sleep(0.5)
            for trade in list(self.ib.openTrades()):
                try:
                    oid = int(getattr(trade.order, "orderId", 0) or 0)
                    if oid <= 0:
                        continue
                    st = trade.orderStatus.status if trade.orderStatus else ""
                    if st in (
                        "PendingCancel", "Cancelled", "Filled",
                        "Inactive", "ApiCancelled",
                    ):
                        continue
                    self.ib.cancelOrder(trade.order)
                    cancelled += 1
                except Exception:
                    pass
            if cancelled:
                self.ib.sleep(0.5)
                log.info(f"🧹 Cancelled {cancelled} stale open order(s) from prior session")
            # Clear stuck PendingSubmit orders (block new brackets on paper)
            pending = 0
            for trade in list(self.ib.openTrades()):
                try:
                    st = trade.orderStatus.status if trade.orderStatus else ""
                    if st == "PendingSubmit":
                        oid = int(getattr(trade.order, "orderId", 0) or 0)
                        if oid > 0:
                            self.ib.cancelOrder(trade.order)
                            pending += 1
                except Exception:
                    pass
            if pending:
                self.ib.sleep(0.3)
                log.info(f"🧹 Cancelled {pending} stuck PendingSubmit order(s)")
        except Exception as exc:
            log.debug(f"Stale order cleanup: {exc}")
        return cancelled

    def flatten_orphan_short_positions(self) -> int:
        """Buy to cover unexpected short positions left on the paper account."""
        covered = 0
        try:
            from ib_insync import Stock
            self.ib.reqPositions()
            self.ib.sleep(0.5)
            for p in list(self.ib.positions()):
                qty = float(p.position)
                if qty >= -0.5:
                    continue
                sym = getattr(p.contract, "symbol", "") or ""
                if not sym:
                    continue
                cover_qty = int(abs(qty))
                if cover_qty < 1:
                    continue
                self.cancel_open_orders_for_symbol(sym)
                try:
                    qualified = self.ib.qualifyContracts(Stock(sym, "SMART", "USD"))
                    contract = qualified[0] if qualified else p.contract
                except Exception:
                    contract = p.contract
                order = MarketOrder("BUY", cover_qty)
                self._configure_order(order)
                trade = self.ib.placeOrder(contract, order)
                self.ib.sleep(0.35)
                st = trade.orderStatus.status if trade.orderStatus else ""
                if st in ("Filled", "Submitted", "PreSubmitted"):
                    log.info(f"🧹 Covering orphan short: BUY {cover_qty:,} {sym} ({st})")
                    covered += 1
                elif st in ("PendingSubmit", "PendingCancel"):
                    log.warning(
                        f"Orphan short cover pending for {sym} ({st}) — "
                        f"skipping (IB still processing prior order)"
                    )
                else:
                    log.warning(
                        f"Orphan short cover rejected for {sym} ({st}) — "
                        f"ignored until position clears"
                    )
            if covered:
                self.ib.sleep(0.5)
        except Exception as exc:
            log.debug(f"Orphan short cleanup: {exc}")
        return covered

    def flatten_position(self, quantity: int, handle: Optional[BracketHandle] = None,
                          urgent: bool = True, symbol: Optional[str] = None) -> Optional[Trade]:
        """
        Immediately exit the position. Cancels any resting bracket
        children first (so we don't end up double-selling), then sends
        a market order. `urgent=True` is used for stop/circuit-breaker
        exits, where guaranteed execution matters more than price.
        """
        if not self._session_allows_orders("flatten"):
            _, state = orders_allowed(self.cfg)
            log.info(
                f"⏸ Flatten skipped — session {state} "
                f"(orders only during {allowed_trading_sessions_label(self.cfg)})"
            )
            return None
        sym = (symbol or self.cfg.TICKER or "").upper()
        contract = self.conn.get_contract(sym or None)
        if sym:
            n = self.cancel_open_orders_for_symbol(sym)
            if n:
                log.debug(f"Cleared {n} open {sym} order(s) before flatten")
            self.ib.sleep(0.4)

        if handle is not None:
            try:
                for trade in (handle.stop_trade, handle.target_trade, handle.parent_trade):
                    if trade is None:
                        continue
                    st = trade.orderStatus.status if trade.orderStatus else ""
                    if st in ("Filled", "Cancelled", "Inactive", "ApiCancelled", "PendingCancel"):
                        continue
                    try:
                        self.ib.cancelOrder(trade.order)
                    except Exception:
                        pass
                self.ib.sleep(0.3)
            except Exception as exc:
                log.warning(f"Could not cancel bracket children cleanly: {exc}")

        order = MarketOrder("SELL", quantity)
        self._configure_order(order)
        trade = self.ib.placeOrder(contract, order)
        log.info(f"Flatten order submitted: SELL {quantity} sh (market)")
        return trade

    # ── Slippage-aware entry price decision ──────────────────────────────────

    def decide_smart_entry(
        self,
        last_price: float,
        bid: Optional[float],
        ask: Optional[float],
        shares: int,
        avg_volume: float = 0.0,
    ) -> Tuple[Optional[float], str]:
        """
        Pick entry order type for IB compliance.

        Penny / NASDAQ-SCM stocks: NEVER plain MARKET — IB converts to a
        regulatory-capped limit that can sit below the ask (error 2161) and
        never fill. Large orders on thin books get the same treatment.

        Returns (limit_price_or_None, mode) where None limit => MARKET parent.
        """
        if last_price <= 0 or shares < 1:
            return None, "invalid"

        penny_thr = float(getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0))
        is_penny = last_price < penny_thr
        max_market_sh = int(getattr(self.cfg, "MAX_MARKET_ENTRY_SHARES", 400))
        thin_book = avg_volume > 0 and shares > avg_volume * float(
            getattr(self.cfg, "LIQUIDITY_MAX_VOL_PCT", 0.08)
        )

        allow_market = (
            not is_penny
            and not thin_book
            and shares <= max_market_sh
            and getattr(self.cfg, "PENNY_USE_MARKET_ENTRY", False)
        )

        if allow_market:
            limit_px, used = self.decide_entry_price(last_price, bid, ask)
            if used and limit_px:
                return self._round_price(limit_px), "limit_spread"
            return None, "market"

        ref = ask if ask and ask > 0 else last_price
        reg_pct = float(getattr(self.cfg, "IB_REGULATORY_LIMIT_PCT", 0.01))
        ib_max = last_price * (1.0 + reg_pct)

        if bid and ask and ask > bid > 0:
            spread_pct = (ask - bid) / last_price
            if spread_pct > float(getattr(self.cfg, "MAX_ACCEPTABLE_SLIPPAGE_PCT", 0.004)) * 2:
                # IB error 2161 — cap limit inside regulatory band, not above ask
                limit = min(ask, ib_max)
                log.info(
                    f"Wide spread {spread_pct:.2%} — limit entry @ ${limit:.4f} "
                    f"(bid ${bid:.4f} ask ${ask:.4f} IB max ${ib_max:.4f})"
                )
                return self._round_price(limit), "limit_wide_spread"

        buf = float(
            getattr(self.cfg, "PENNY_LIMIT_BUFFER_PCT", 0.006)
            if is_penny
            else getattr(self.cfg, "ENTRY_LIMIT_BUFFER_PCT", 0.003)
        )
        limit = min(ref * (1.0 + buf), ib_max)
        limit = max(limit, last_price * 1.0005)

        mode = "limit_penny" if is_penny else ("limit_thin" if thin_book else "limit_smart")
        return self._round_price(limit), mode

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
