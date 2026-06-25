#!/usr/bin/env python3
"""
core/connector.py — IB Gateway connection management with self-healing.

Handles connect, disconnect, contract qualification, and automatic
reconnection with exponential backoff. Includes keepalive pings and
anti-flap protection to prevent reconnect storms during idle periods.
"""

import time
from typing import Optional, Dict, Any

try:
    from ib_insync import IB, Stock
    import ib_insync as ibi
except ImportError:
    raise SystemExit(
        "\nERROR: ib_insync is not installed.\n"
        "Fix:   pip install ib_insync\n"
    )

from core.config import BotConfig
from core.notify import log, Notifier


# Common LSE tickers and exchanges
LSE_EXCHANGES = {"LSE", "LSE.FPX", "LSE.GBP"}
LSE_CURRENCY = "GBP"


class IBConnector:
    """
    Manages the IB Gateway TCP connection with self-healing:
    - Keepalive pings every 60s to prevent idle disconnection
    - Anti-flap: min 30s between reconnects to break loop
    - Connection health verified with actual reqCurrentTime call before flagging stale
    """

    def __init__(self, cfg: BotConfig, notifier: Optional[Notifier] = None):
        self.cfg       = cfg
        self.notifier  = notifier
        self.ib        = IB()
        self._contract = None

        # Track last time we saw ANY event from IB, used to detect a
        # silently-dead connection (socket open but Gateway frozen/crashed)
        self._last_event_ts: float = time.time()
        self._last_ping_ts: float = 0.0
        self._last_reconnect_ts: float = 0.0
        self._reconnect_count: int = 0  # total reconnects in this session
        self._order_errors: Dict[int, Dict[str, Any]] = {}
        self._md_error_handlers: list = []
        self._tick_limit_handlers: list = []
        
        self.ib.connectedEvent  += self._on_connected
        self.ib.disconnectedEvent += self._on_disconnected
        self.ib.errorEvent += self._on_error

    # ── Public API ─────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Establish the IB Gateway connection. Returns True on success."""
        try:
            # Suppress ib_insync INFO chatter that fills the client output buffer
            try:
                import ib_insync as ibi
                ibi.util.log.level = 30  # WARNING
                # Allow tick/stream subscriptions from the synchronous main loop.
                ibi.util.patchAsyncio()
            except Exception:
                pass
            self.ib.connect(
                host=self.cfg.IB_HOST,
                port=self.cfg.IB_PORT,
                clientId=self.cfg.IB_CLIENT_ID,
                timeout=20,
            )
            accounts = self.ib.managedAccounts()
            log.info(f"IB Gateway connected → {self.cfg.IB_HOST}:{self.cfg.IB_PORT}")
            log.info(f"Account(s): {accounts}")

            mode_label = "PAPER" if self.cfg.PAPER_TRADING else "LIVE"
            log.info(f"Mode: {mode_label} | Account: {accounts[0] if accounts else 'unknown'}")

            self._last_event_ts = time.time()
            self._last_reconnect_ts = time.time()
            return True

        except Exception as exc:
            log.error(f"IB connection failed: {exc}")
            log.error(
                "Troubleshooting checklist:\n"
                "  1. Is IB Gateway running and logged in?\n"
                "  2. API enabled? (Configure -> Settings -> API -> Enable Socket)\n"
                f"  3. Correct port? (Paper={self.cfg.IB_PORT}, Live=7496)\n"
                "  4. Read-Only API = OFF?\n"
                "  5. Allow connections from 127.0.0.1?\n"
                f"  6. Client ID {self.cfg.IB_CLIENT_ID} not already in use?\n"
            )
            return False

    def disconnect(self):
        try:
            self.ib.disconnect()
            log.info("Disconnected from IB Gateway")
        except Exception:
            pass

    def get_contract(self, symbol: Optional[str] = None):
        """Qualify and cache the contract for symbol (default: cfg.TICKER)."""
        sym = (symbol or self.cfg.TICKER or "").upper()
        if not sym:
            raise RuntimeError("No ticker symbol for contract qualification")
        if self._contract is None or getattr(self._contract, "symbol", None) != sym:
            raw = Stock(sym, self.cfg.EXCHANGE, self.cfg.CURRENCY)
            qualified = self.ib.qualifyContracts(raw)
            if not qualified:
                raise RuntimeError(
                    f"Could not qualify contract for '{sym}'.\n"
                    "Possible causes:\n"
                    "  - Ticker symbol is wrong\n"
                    "  - Your market data subscription does not cover this stock\n"
                    "  - IB Gateway not fully logged in yet (wait 30 sec after login)"
                )
            self._contract = qualified[0]
            log.debug(f"Contract qualified: {self._contract}")
        return self._contract

    def is_connected(self) -> bool:
        """
        Returns True if IB connection is alive.
        Uses multi-layered health check:
        1. ib_insync's internal isConnected()
        2. Event heartbeat within timeout
        3. Actual server ping to confirm (not just socket level)
        """
        # Layer 1: socket-level check
        if not self.ib.isConnected():
            return False
        
        now = time.time()
        elapsed = now - self._last_event_ts
        
        # Layer 2: if we've seen events recently, connection is likely fine
        # Use adaptive timeout: 120s during market hours, 300s when closed
        timeout = getattr(self.cfg, 'HEARTBEAT_TIMEOUT_SEC', 60)
        # Adaptive: during US market hours (9:30-16:00 ET) use configured timeout,
        # otherwise double it since data may not flow
        try:
            from core.market_hours import now_et
            current_et = now_et()
            hour_min = current_et.hour * 60 + current_et.minute
            if not (9*60+30 <= hour_min < 16*60):
                timeout = max(timeout * 4, 300)  # Off-hours: 5 min timeout
        except Exception:
            pass  # Use default timeout
        
        if elapsed < timeout:
            return True
        
        # Layer 3: elapsed > timeout, send keepalive ping to verify
        try:
            self._send_keepalive_ping()
            self._last_event_ts = time.time()  # Reset on successful ping
            return True
        except Exception:
            return False

    def _send_keepalive_ping(self):
        """
        Send a lightweight ping to keep the IB connection alive.
        Uses reqCurrentTime as it's the cheapest IB request.
        """
        now = time.time()
        # Only ping every 30 seconds max to avoid flooding
        if now - self._last_ping_ts < 30:
            return
        
        self._last_ping_ts = now
        try:
            # reqCurrentTime is lightweight and always available
            self.ib.reqCurrentTime()
            log.debug("Keepalive ping sent to IB Gateway")
        except Exception:
            raise

    def touch(self):
        """Call whenever any IB event arrives, to mark the connection alive."""
        self._last_event_ts = time.time()

    def reconnect(self) -> bool:
        """
        Attempt reconnection with:
        - Anti-flap: minimum 30s between reconnects to prevent storms
        - Exponential backoff capped at RECONNECT_MAX_DELAY_SEC
        - Up to RECONNECT_MAX_ATTEMPTS times
        """
        now = time.time()
        
        # Anti-flap: if we just reconnected within the last 30s, skip
        if now - self._last_reconnect_ts < 30:
            log.debug(f"Anti-flap: skipping reconnect (last was {now - self._last_reconnect_ts:.0f}s ago)")
            return self.ib.isConnected()
        
        self._contract = None  # force re-qualification after reconnect
        
        for attempt in range(1, self.cfg.RECONNECT_MAX_ATTEMPTS + 1):
            wait = min(
                self.cfg.RECONNECT_BASE_DELAY_SEC * (2 ** (attempt - 1)),
                self.cfg.RECONNECT_MAX_DELAY_SEC,
            )
            log.warning(
                f"Reconnect attempt {attempt}/{self.cfg.RECONNECT_MAX_ATTEMPTS} "
                f"in {wait}s …"
            )
            # Only notify on final failure, not every attempt
            if self.notifier and attempt == self.cfg.RECONNECT_MAX_ATTEMPTS:
                self.notifier.reconnect_event(success=False, attempt=attempt)
            time.sleep(wait)
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
            except Exception:
                pass
            # After reconnect, pause briefly so IB can finish setup before scanner resumes
            if self.connect():
                time.sleep(2)
                self._reconnect_count += 1
                log.info(f"Reconnected successfully. (total reconnects: {self._reconnect_count})")
                if self.notifier:
                    self.notifier.reconnect_event(success=True)
                return True
        
        log.error("All reconnection attempts failed.")
        if self.notifier:
            self.notifier.error(
                "IBConnector.reconnect",
                f"All {self.cfg.RECONNECT_MAX_ATTEMPTS} reconnect attempts failed. "
                "Bot is stopping. Any open position remains live in your IB "
                "account with its protective bracket orders still resting "
                "on IB's servers (they do not depend on this bot staying connected)."
            )
        return False

    # ── Event handlers (connection health tracking) ─────────────────────────

    def _on_connected(self):
        self.touch()

    def _on_disconnected(self):
        log.warning("IB connection dropped (disconnectedEvent fired).")

    def pop_order_error(self, req_id: int) -> Optional[Dict[str, Any]]:
        """Return and clear IB error recorded for an order reqId."""
        return self._order_errors.pop(int(req_id), None)

    def register_market_data_error_handler(self, handler) -> None:
        """Runner callback: stop streams / rotate focus on MD failures."""
        self._md_error_handlers.append(handler)

    def register_tick_limit_handler(self, handler) -> None:
        """Runner callback: downgrade ticker from tick-by-tick to 5s bars (IB 10190)."""
        self._tick_limit_handlers.append(handler)

    def _on_error(self, reqId, errorCode, errorString, contract):
        # Pure informational error codes from IB that aren't real problems
        BENIGN = {2104, 2106, 2107, 2108, 2109, 2119, 2158}
        self.touch()
        # Order lifecycle codes — stored on reqId, not worth WARNING spam
        QUIET_ORDER = {201, 202, 399, 10148}
        self.touch()
        if errorCode in (2161, 399, 201, 202):
            from core.broker import parse_ib_regulatory_cap
            info: Dict[str, Any] = {"code": errorCode, "message": errorString}
            if errorCode == 2161:
                cap = parse_ib_regulatory_cap(errorString)
                if cap:
                    info["price_cap"] = cap
            self._order_errors[int(reqId)] = info
        if errorCode in BENIGN or errorCode in QUIET_ORDER:
            return

        # IB tick-by-tick subscription cap (typically 5) — downgrade to 5s bars
        if errorCode == 10190:
            try:
                from core.market_data_learning import extract_ticker_from_error
                ticker = extract_ticker_from_error(contract, errorString)
                for handler in self._tick_limit_handlers:
                    try:
                        handler(ticker, int(errorCode), str(errorString))
                    except Exception:
                        pass
                log.info(
                    f"IB tick-by-tick cap on {ticker or '?'} — using 5s bars instead"
                )
            except Exception:
                log.info(f"IB tick-by-tick cap (10190) — using 5s bars instead")
            return

        # Market-data failures → learn + avoid (162 no HMDS, 420 no permissions, …)
        try:
            from core.market_data_learning import (
                MARKET_DATA_ERROR_CODES,
                handle_ib_market_data_error,
                extract_ticker_from_error,
            )
            if errorCode in MARKET_DATA_ERROR_CODES:
                entry = handle_ib_market_data_error(
                    self.cfg, int(reqId), int(errorCode), str(errorString), contract,
                )
                if entry:
                    ticker = entry.get("ticker") or extract_ticker_from_error(contract, errorString)
                    for handler in self._md_error_handlers:
                        try:
                            handler(ticker, int(errorCode), str(errorString), entry)
                        except Exception:
                            pass
                    return
        except Exception as exc:
            log.debug(f"MD learning hook: {exc}")

        log.warning(f"IB error {errorCode}: {errorString} (reqId={reqId})")