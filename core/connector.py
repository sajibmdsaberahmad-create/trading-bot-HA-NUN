#!/usr/bin/env python3
"""
core/connector.py — IB Gateway connection management.

Handles connect, disconnect, contract qualification, and automatic
reconnection with exponential backoff. This is the only file that
talks raw ib_insync connection calls — everything else goes through
this class so the reconnect logic lives in exactly one place.
"""

import time
from typing import Optional

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


class IBConnector:
    """
    Manages the IB Gateway TCP connection.

    IB Gateway must be running and logged in BEFORE connect() is called.
    See docs/LAUNCH_GUIDE.md PART 2 for the full Gateway setup walkthrough.
    """

    def __init__(self, cfg: BotConfig, notifier: Optional[Notifier] = None):
        self.cfg       = cfg
        self.notifier  = notifier
        self.ib        = IB()
        self._contract = None

        # Track last time we saw ANY event from IB, used to detect a
        # silently-dead connection (socket open but Gateway frozen/crashed)
        self._last_event_ts: float = time.time()
        self.ib.connectedEvent  += self._on_connected
        self.ib.disconnectedEvent += self._on_disconnected
        self.ib.errorEvent += self._on_error

    # ── Public API ─────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Establish the IB Gateway connection. Returns True on success."""
        try:
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

    def get_contract(self):
        """Qualify and cache the contract for cfg.TICKER. Invalidate cache if ticker changes."""
        if self._contract is None or getattr(self._contract, 'symbol', None) != self.cfg.TICKER:
            raw = Stock(self.cfg.TICKER, self.cfg.EXCHANGE, self.cfg.CURRENCY)
            # Use async qualifier to avoid "coroutine was never awaited" warning on Python 3.13
            qualified = self.ib.qualifyContractsAsync(raw)
            if not qualified:
                raise RuntimeError(
                    f"Could not qualify contract for '{self.cfg.TICKER}'.\n"
                    "Possible causes:\n"
                    "  - Ticker symbol is wrong\n"
                    "  - Your market data subscription does not cover this stock\n"
                    "  - IB Gateway not fully logged in yet (wait 30 sec after login)"
                )
            self._contract = qualified[0]
            log.info(f"Contract qualified: {self._contract}")
        return self._contract

    def is_connected(self) -> bool:
        if not self.ib.isConnected():
            return False
        # Secondary check: have we heard from IB recently? A hung socket
        # can report isConnected()==True for a while after a network drop.
        stale = (time.time() - self._last_event_ts) > self.cfg.HEARTBEAT_TIMEOUT_SEC
        return not stale

    def touch(self):
        """Call whenever any IB event arrives, to mark the connection alive."""
        self._last_event_ts = time.time()

    def reconnect(self) -> bool:
        """
        Attempt reconnection with exponential backoff, capped at
        RECONNECT_MAX_DELAY_SEC, up to RECONNECT_MAX_ATTEMPTS times.
        """
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
            if self.connect():
                log.info("Reconnected successfully.")
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

    def _on_error(self, reqId, errorCode, errorString, contract):
        # Pure informational error codes from IB that aren't real problems
        BENIGN = {2104, 2106, 2107, 2108, 2119, 2158}
        self.touch()
        if errorCode in BENIGN:
            return
        log.warning(f"IB error {errorCode}: {errorString} (reqId={reqId})")
