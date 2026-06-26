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
        self._session_reclaim_handlers: list = []
        self._stream_managers: Dict[str, Any] = {}
        self._10197_count: int = 0
        self._10197_window_start: float = 0.0
        self._last_md_reclaim_ts: float = 0.0
        self._10197_reclaim_attempts: int = 0
        self._10197_storm_until: float = 0.0
        self._pending_session_reclaim: bool = False
        self._connectivity_handlers: list = []
        self._connectivity_lost: bool = False
        self._pending_resubscribe: bool = False
        self._md_paused: bool = False
        self._10197_last_log_ts: float = 0.0
        self._md_type_logged: bool = False
        
        self.ib.connectedEvent  += self._on_connected
        self.ib.disconnectedEvent += self._on_disconnected
        self.ib.errorEvent += self._on_error
        self.fill_cache = None

    # ── Public API ─────────────────────────────────────────────────────────

    def connect(self, reclaim: Optional[bool] = None) -> bool:
        """Establish the IB Gateway connection. Returns True on success."""
        try:
            # Suppress ib_insync INFO chatter that fills the client output buffer
            try:
                import ib_insync as ibi
                ibi.util.log.level = 30  # WARNING
                ibi.util.logToConsole(False)
                # Allow tick/stream subscriptions from the synchronous main loop.
                ibi.util.patchAsyncio()
            except Exception:
                pass
            do_reclaim = (
                getattr(self.cfg, "IB_RECLAIM_SESSION_ON_START", True)
                if reclaim is None
                else reclaim
            )
            if do_reclaim:
                self.prepare_fresh_connection()
            self._connect_ib_socket()
            accounts = self.ib.managedAccounts()
            mode_label = "PAPER" if self.cfg.PAPER_TRADING else "LIVE"
            acct = accounts[0] if accounts else "unknown"
            from core.startup_log import startup_compact, sinfo
            if startup_compact(self.cfg):
                log.info(
                    f"IB connected {self.cfg.IB_HOST}:{self.cfg.IB_PORT} | "
                    f"{mode_label} {acct}"
                )
            else:
                log.info(f"IB Gateway connected → {self.cfg.IB_HOST}:{self.cfg.IB_PORT}")
                log.info(f"Account(s): {accounts}")
                log.info(f"Mode: {mode_label} | Account: {acct}")

            self._last_event_ts = time.time()
            self._last_reconnect_ts = time.time()
            self._apply_market_data_type()
            try:
                from core.market_data_learning import (
                    clear_competing_session_blocks,
                    clear_reconnect_transient_blocks,
                )
                clear_competing_session_blocks()
                clear_reconnect_transient_blocks()
            except Exception:
                pass
            if self.fill_cache is None:
                try:
                    from core.fill_reconciler import FillExecutionCache
                    self.fill_cache = FillExecutionCache(self.ib)
                    self.fill_cache.seed_from_ib_fills()
                except Exception:
                    pass
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
                f"  6. Client ID {self.cfg.IB_CLIENT_ID} must be free — "
                "bot never uses another ID (run ./stop.sh first)\n"
            )
            return False

    def _cancel_ib_subscriptions(self, ib: IB) -> None:
        """Cancel market data and realtime bars so IB releases the MD slot."""
        try:
            for t in list(ib.tickers()):
                try:
                    ib.cancelMktData(t.contract)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            for rt in list(getattr(ib, "realTimeBars", ()) or ()):
                try:
                    ib.cancelRealTimeBars(rt)
                except Exception:
                    pass
        except Exception:
            pass

    def prepare_fresh_connection(self) -> int:
        """
        Clear zombie API session on the configured client ID before the real connect.

        Uses ONLY cfg.IB_CLIENT_ID — never rotates to another ID. Opening extra
        client IDs leaves ghost sessions that steal live market data (IB 10197).
        """
        host = self.cfg.IB_HOST
        port = int(self.cfg.IB_PORT)
        cid = int(self.cfg.IB_CLIENT_ID)
        pause = float(getattr(self.cfg, "IB_SESSION_RECLAIM_PAUSE_SEC", 2.0))
        retries = int(getattr(self.cfg, "IB_CLIENT_ID_RECLAIM_RETRIES", 5))
        retry_sec = float(getattr(self.cfg, "IB_CLIENT_ID_RECLAIM_RETRY_SEC", 3.0))

        for attempt in range(1, retries + 1):
            probe = IB()
            try:
                from core.startup_log import sinfo
                probe.connect(host, port, clientId=cid, timeout=8)
                # Do not reqMarketDataType on probe — it grabs the live MD slot.
                sinfo(
                    self.cfg,
                    f"IB pre-connect: reclaimed client_id={cid} "
                    f"(released zombie session)",
                )
                self._cancel_ib_subscriptions(probe)
                probe.disconnect()
                time.sleep(pause)
                return cid
            except Exception as exc:
                msg = str(exc).lower()
                busy = "326" in msg or "already in use" in msg
                try:
                    if probe.isConnected():
                        self._cancel_ib_subscriptions(probe)
                        probe.disconnect()
                except Exception:
                    pass
                if busy:
                    if attempt < retries:
                        log.warning(
                            f"IB client_id={cid} busy ({attempt}/{retries}) — "
                            f"waiting {retry_sec}s for slot (will NOT use another client ID)"
                        )
                        time.sleep(retry_sec)
                        continue
                    log.error(
                        f"IB client_id={cid} still busy after {retries} attempts. "
                        "Run ./stop.sh, close TWS/other API apps, or restart IB Gateway. "
                        "Bot will not open a second client ID — that causes 10197 MD conflicts."
                    )
                else:
                    log.debug(f"IB pre-connect probe failed: {exc}")
                break
        return cid

    def _connect_ib_socket(self) -> None:
        """Connect self.ib with retries on client-id-in-use (326)."""
        cid = int(self.cfg.IB_CLIENT_ID)
        retries = int(getattr(self.cfg, "IB_CLIENT_ID_RECLAIM_RETRIES", 5))
        retry_sec = float(getattr(self.cfg, "IB_CLIENT_ID_RECLAIM_RETRY_SEC", 3.0))
        last_exc: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                if self.ib.isConnected():
                    try:
                        self.ib.disconnect()
                    except Exception:
                        pass
                    time.sleep(1.0)
                self.ib.connect(
                    host=self.cfg.IB_HOST,
                    port=self.cfg.IB_PORT,
                    clientId=cid,
                    timeout=20,
                )
                return
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                if ("326" in msg or "already in use" in msg) and attempt < retries:
                    log.warning(
                        f"IB connect client_id={cid} busy ({attempt}/{retries}) — "
                        f"retrying in {retry_sec}s"
                    )
                    time.sleep(retry_sec)
                    continue
                raise
        if last_exc:
            raise last_exc

    def request_session_reclaim(self) -> None:
        """Schedule reclaim on main loop — never reconnect from IB error callbacks."""
        if self._md_paused:
            return
        self._pending_session_reclaim = True

    def clear_pending_session_reclaim(self) -> None:
        self._pending_session_reclaim = False

    def run_pending_session_reclaim(self) -> bool:
        """Run deferred 10197 reclaim from the trading loop (safe event loop context)."""
        if self._md_paused:
            self._pending_session_reclaim = False
            return False
        if not self._pending_session_reclaim:
            return False
        self._pending_session_reclaim = False
        try:
            ok = self.reclaim_live_market_data_session()
            if ok:
                self._pending_resubscribe = True
            return ok
        except Exception as exc:
            log.warning(f"IB session reclaim failed: {exc}")
            return False

    def reclaim_live_market_data_session(self) -> bool:
        """
        Full disconnect/reconnect to reclaim live MD after 10197 competing session.
        Cancels streams first via registered handlers.
        """
        storm_threshold = int(getattr(self.cfg, "IB_10197_STORM_THRESHOLD", 3))
        storm_backoff = float(getattr(self.cfg, "IB_10197_STORM_BACKOFF_SEC", 300.0))
        if time.time() < self._10197_storm_until:
            return False

        self._10197_reclaim_attempts += 1
        if self._10197_reclaim_attempts > storm_threshold:
            self._10197_storm_until = time.time() + storm_backoff
            self._10197_reclaim_attempts = 0
            log.error(
                f"IB 10197 reclaim storm — pausing reclaims for {storm_backoff:.0f}s. "
                "Run ./stop.sh, quit IB Gateway fully, wait 60s, restart Gateway, "
                "then ./START.command once."
            )
            return False

        log.warning(
            "IB 10197 — reclaiming live market data slot "
            "(stop streams → disconnect → reconnect → LIVE)"
        )
        for handler in list(self._session_reclaim_handlers):
            try:
                handler()
            except Exception as exc:
                log.debug(f"Session reclaim handler: {exc}")
        self._stream_managers.clear()
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            pass
        base_pause = float(getattr(self.cfg, "IB_SESSION_RECLAIM_PAUSE_SEC", 8.0))
        pause = base_pause * min(4, max(1, self._10197_reclaim_attempts))
        time.sleep(pause)
        self._contract = None
        if not self.connect(reclaim=True):
            return False
        self._connectivity_lost = False
        self._apply_market_data_type(force=True)
        log.info("IB live market data session reclaimed — streams can restart")
        return True

    def disconnect(self):
        """Release all IB subscriptions and close the API socket."""
        for handler in list(self._session_reclaim_handlers):
            try:
                handler()
            except Exception:
                pass
        self._stream_managers.clear()
        try:
            if self.ib.isConnected():
                try:
                    for t in list(self.ib.tickers()):
                        try:
                            self.ib.cancelMktData(t.contract)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    for rt in list(getattr(self.ib, "realTimeBars", ()) or ()):
                        try:
                            self.ib.cancelRealTimeBars(rt)
                        except Exception:
                            pass
                except Exception:
                    pass
                self.ib.disconnect()
            log.info("Disconnected from IB Gateway (subscriptions cancelled)")
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

    def _apply_market_data_type(self, force: bool = False) -> None:
        """Request live (or configured) market data from IB — paper + live accounts."""
        mdt = 1 if getattr(self.cfg, "IB_FORCE_LIVE_MARKET_DATA", True) else int(
            getattr(self.cfg, "IB_MARKET_DATA_TYPE", 1)
        )
        if force:
            mdt = 1
        labels = {1: "LIVE", 2: "FROZEN", 3: "DELAYED", 4: "DELAYED_FROZEN"}
        try:
            self.ib.reqMarketDataType(mdt)
            from core.startup_log import sinfo
            msg = (
                f"IB market data → {labels.get(mdt, str(mdt))} "
                f"(reqMarketDataType={mdt})"
            )
            if force or not self._md_type_logged:
                log.info(msg)
                self._md_type_logged = True
            else:
                sinfo(self.cfg, msg)
        except Exception as exc:
            log.warning(f"reqMarketDataType({mdt}) failed: {exc}")

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
            if self.connect(reclaim=(attempt >= 2)):
                time.sleep(2)
                self._reconnect_count += 1
                self._connectivity_lost = False
                self._pending_resubscribe = True
                log.info(f"Reconnected successfully. (total reconnects: {self._reconnect_count})")
                if self.notifier:
                    self.notifier.reconnect_event(success=True)
                return True
            if attempt == 1:
                try:
                    self.prepare_fresh_connection()
                except Exception as exc:
                    log.debug(f"IB pre-reconnect reclaim: {exc}")
        
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
        self._last_event_ts = time.time()
        self._apply_market_data_type()
        try:
            from core.fill_reconciler import FillExecutionCache
            self.fill_cache = FillExecutionCache(self.ib)
            self.fill_cache.seed_from_ib_fills()
        except Exception as exc:
            log.debug(f"Fill cache init: {exc}")

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

    def register_session_reclaim_handler(self, handler) -> None:
        """Called before disconnect/reconnect to cancel live streams."""
        self._session_reclaim_handlers.append(handler)

    def register_connectivity_handler(self, handler) -> None:
        """Runner callback: connectivity_lost | data_lost | data_ok | resubscribe."""
        self._connectivity_handlers.append(handler)

    def consume_resubscribe_pending(self) -> bool:
        """True once when streams must be re-requested after reconnect/reclaim."""
        if not self._pending_resubscribe:
            return False
        self._pending_resubscribe = False
        return True

    def set_market_data_active(self, active: bool) -> None:
        """Runner sets False during off-hours to suppress MD reclaim noise."""
        self._md_paused = not active

    def market_data_paused(self) -> bool:
        return bool(self._md_paused)

    def _notify_connectivity(self, event: str) -> None:
        for handler in list(self._connectivity_handlers):
            try:
                handler(event)
            except Exception as exc:
                log.debug(f"Connectivity handler: {exc}")

    def register_stream_manager(self, ticker: str, manager: Any) -> None:
        """Per-ticker DataManager — immediate 5s-bar fallback on IB 10189/10190."""
        if ticker:
            self._stream_managers[str(ticker).upper()] = manager

    def unregister_stream_manager(self, ticker: str) -> None:
        if ticker:
            self._stream_managers.pop(str(ticker).upper(), None)

    def _on_error(self, reqId, errorCode, errorString, contract):
        # Pure informational error codes from IB that aren't real problems
        BENIGN = {2104, 2106, 2107, 2108, 2109, 2119, 2158}
        self.touch()
        # Order lifecycle codes — stored on reqId, not worth WARNING spam
        QUIET_ORDER = {201, 202, 399, 10147, 10148}
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

        # Expected when we time out / rotate scanner subscriptions
        if errorCode == 162 and "scanner subscription cancelled" in (errorString or "").lower():
            return

        # Stale cancel after failed tick-by-tick subscribe
        if errorCode == 300 and "can't find eid" in (errorString or "").lower():
            return

        # Connectivity lost / restored — IB docs 1100/1101/1102
        if errorCode == 1100:
            self._connectivity_lost = True
            log.warning(
                "IB 1100 — connectivity between IB and Gateway lost "
                "(waiting for restore before MD reclaim)"
            )
            self._notify_connectivity("connectivity_lost")
            return
        if errorCode == 1101:
            self._connectivity_lost = False
            self._pending_resubscribe = True
            log.warning(
                "IB 1101 — connectivity restored, market data lost (re-subscribe queued)"
            )
            return
        if errorCode == 1102:
            self._connectivity_lost = False
            log.info("IB 1102 — connectivity restored, market data maintained")
            self._notify_connectivity("data_ok")
            return

        # Competing live session — reclaim MD slot after repeated failures
        if errorCode == 10197:
            if self._md_paused:
                return
            now = time.time()
            if now < self._10197_storm_until:
                if self._10197_count == 0:
                    remain = int(self._10197_storm_until - now)
                    log.warning(
                        f"IB 10197 — reclaim paused ({remain}s storm backoff). "
                        "Quit Gateway fully, wait 60s, restart, then ./START.command"
                    )
                self._10197_count += 1
                return
            if self._connectivity_lost:
                return
            self._apply_market_data_type(force=True)
            if now - self._10197_window_start > 20.0:
                self._10197_count = 0
                self._10197_window_start = now
            self._10197_count += 1
            threshold = int(getattr(self.cfg, "IB_10197_RECLAIM_THRESHOLD", 3))
            cooldown = float(getattr(self.cfg, "IB_10197_RECLAIM_COOLDOWN_SEC", 90.0))
            if (
                self._10197_count >= threshold
                and now - self._last_md_reclaim_ts >= cooldown
            ):
                self._10197_count = 0
                self._last_md_reclaim_ts = now
                log.warning(
                    f"IB 10197 burst ({threshold}+ errors) — scheduling session reclaim"
                )
                self.request_session_reclaim()
            elif now - self._10197_last_log_ts >= 5.0:
                self._10197_last_log_ts = now
                log.warning(
                    "IB 10197 competing session — forced LIVE "
                    f"({min(self._10197_count, threshold)}/{threshold} before reclaim)"
                )
            return

        # IB tick-by-tick unsupported (10189) or cap (10190) — downgrade to 5s bars
        if errorCode in (10189, 10190):
            try:
                from core.market_data_learning import extract_ticker_from_error
                ticker = extract_ticker_from_error(contract, errorString)
                dm = self._stream_managers.get((ticker or "").upper())
                if dm is not None:
                    try:
                        dm.fallback_to_realtime_bars()
                    except Exception:
                        pass
                for handler in self._tick_limit_handlers:
                    try:
                        handler(ticker, int(errorCode), str(errorString))
                    except Exception:
                        pass
                from core.startup_log import sinfo
                if errorCode == 10189:
                    sinfo(self.cfg, f"IB tick-by-tick unavailable on {ticker or '?'} — using 5s bars")
                else:
                    sinfo(self.cfg, f"IB tick-by-tick cap on {ticker or '?'} — using 5s bars instead")
            except Exception:
                log.debug(f"IB tick-by-tick issue ({errorCode}) — using 5s bars instead")
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