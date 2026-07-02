#!/usr/bin/env python3
"""
core/replay_connector.py — Offline IB stand-in for replay-live ScalperRunner.

ib.sleep() advances the replay market hub — same loop cadence as live HANOON.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, List, Optional, TYPE_CHECKING

try:
    import core.ib_client as ibi
except ImportError:
    ibi = None  # type: ignore

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.replay_market_hub import ReplayMarketHub


class _FakeClient:
    def __init__(self):
        self._next = 9000

    def getReqId(self) -> int:
        self._next += 1
        return self._next


class _FakeIB:
    """Minimal IB API surface used by ScalperRunner offline."""

    def __init__(self, cfg: BotConfig, hub: Optional["ReplayMarketHub"] = None):
        self._cfg = cfg
        self._hub = hub
        self._connected = True
        self.client = _FakeClient()

    def isConnected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    def set_hub(self, hub: "ReplayMarketHub") -> None:
        self._hub = hub

    def sleep(self, secs: float = 0) -> None:
        if self._hub is not None:
            self._hub.advance_step()
        if secs and secs > 0:
            time.sleep(float(secs))

    def accountValues(self) -> List[Any]:
        cash = float(getattr(self._cfg, "INITIAL_CASH", 1000.0))
        acct = getattr(self._cfg, "IB_ACCOUNT", "REPLAY")
        return [
            SimpleNamespace(tag="NetLiquidation", value=str(cash), currency="USD", account=acct),
            SimpleNamespace(tag="TotalCashValue", value=str(cash), currency="USD", account=acct),
            SimpleNamespace(tag="AvailableFunds", value=str(cash), currency="USD", account=acct),
        ]

    def qualifyContracts(self, contract: Any) -> List[Any]:
        return [contract]

    def reqPositions(self) -> None:
        pass

    def positions(self) -> List[Any]:
        return []

    def openOrders(self) -> List[Any]:
        return []

    def openTrades(self) -> List[Any]:
        return []

    def trades(self) -> List[Any]:
        return []

    def reqAllOpenOrders(self) -> None:
        pass

    def cancelOrder(self, order: Any) -> None:
        pass

    def placeOrder(self, contract: Any, order: Any) -> Any:
        return SimpleNamespace(order=order, orderStatus=SimpleNamespace(status="Filled", filled=0))


class ReplayConnector:
    """Drop-in connector for replay — never opens a TCP socket."""

    def __init__(self, cfg: BotConfig, notifier: Optional[Any] = None, hub: Optional["ReplayMarketHub"] = None):
        self.cfg = cfg
        self.notifier = notifier
        self.hub = hub
        self.ib = _FakeIB(cfg, hub)
        self._connected = False
        self._contract = None
        self._md_error_handlers: list = []
        self._tick_limit_handlers: list = []
        self._session_reclaim_handlers: list = []
        self._connectivity_handlers: list = []
        self._stream_managers: dict = {}
        self.fill_cache = None
        self._connectivity_outage_active = False
        self._pending_session_reclaim = False

    def in_connectivity_outage(self) -> bool:
        """Replay never loses IB — CSV fake-live stays connected."""
        return False

    def request_session_reclaim(self) -> None:
        pass

    def clear_pending_session_reclaim(self) -> None:
        self._pending_session_reclaim = False

    def connect(self, reclaim: Optional[bool] = None) -> bool:
        self._connected = True
        log.info("Replay connector ready (no IB Gateway — CSV fake-live synced to main loop)")
        return True

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    def get_contract(self, symbol: Optional[str] = None):
        sym = (symbol or self.cfg.TICKER).upper()
        if ibi is not None:
            c = ibi.Stock(sym, self.cfg.EXCHANGE, self.cfg.CURRENCY)
            return self.ib.qualifyContracts(c)[0]
        return SimpleNamespace(symbol=sym, exchange="SMART", currency="USD")

    def register_market_data_error_handler(self, handler) -> None:
        self._md_error_handlers.append(handler)

    def register_tick_limit_handler(self, handler) -> None:
        self._tick_limit_handlers.append(handler)

    def register_session_reclaim_handler(self, handler) -> None:
        self._session_reclaim_handlers.append(handler)

    def register_connectivity_handler(self, handler) -> None:
        self._connectivity_handlers.append(handler)

    def register_stream_manager(self, ticker: str, mgr: Any) -> None:
        self._stream_managers[ticker] = mgr

    def run_pending_session_reclaim(self) -> None:
        pass

    def consume_resubscribe_pending(self) -> bool:
        return False

    def pop_order_error(self, req_id: int):
        return None

    def market_data_paused(self) -> bool:
        return False

    def touch(self) -> None:
        pass

    def reconnect(self) -> bool:
        return True

    def unregister_stream_manager(self, ticker: str) -> None:
        self._stream_managers.pop(ticker, None)

    def set_market_data_active(self, active: bool) -> None:
        pass

    def attach_hub(self, hub: "ReplayMarketHub") -> None:
        self.hub = hub
        self.ib.set_hub(hub)
