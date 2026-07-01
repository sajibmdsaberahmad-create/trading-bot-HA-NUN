#!/usr/bin/env python3
"""
core/ib_sync.py — Shared guards for sync ib_insync calls inside asyncio loops.

qualifyContracts / reqTickers from a background thread orphan coroutines and emit
RuntimeWarning. Callers should use IB Truth / cache when this returns False.
"""
from __future__ import annotations

import inspect
import threading
from typing import Any, List


def ib_blocking_calls_safe(ib) -> bool:
    """True when sync ib_insync blocking calls are safe on this thread."""
    if ib is None:
        return False
    try:
        if not ib.isConnected():
            return False
    except Exception:
        return False
    # patchAsyncio only makes sync wrappers safe on the thread that owns the IB loop.
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        import asyncio

        running = asyncio.get_running_loop()
        ib_loop = getattr(ib, "loop", None)
        if ib_loop is not None and running is not ib_loop:
            return False
    except RuntimeError:
        pass
    return True


def safe_qualify_contracts(ib, *contracts) -> List[Any]:
    """qualifyContracts without orphaning qualifyContractsAsync coroutines."""
    if not contracts or not ib_blocking_calls_safe(ib):
        return []
    try:
        qualified = ib.qualifyContracts(*contracts)
        if inspect.iscoroutine(qualified):
            return []
        return list(qualified or [])
    except Exception:
        return []
