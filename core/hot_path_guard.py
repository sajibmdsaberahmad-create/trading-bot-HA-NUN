#!/usr/bin/env python3
"""Fail-loud helpers for IB/trading hot paths — replace silent except: pass."""

from __future__ import annotations

import time
from typing import Optional

from core.notify import log

_last_warn: dict[str, float] = {}
_THROTTLE_SEC = 60.0


def log_hot_path_warning(
    context: str,
    exc: Optional[BaseException] = None,
    *,
    ticker: str = "",
    throttle_sec: float = _THROTTLE_SEC,
) -> None:
    """Log IB grounding / hot-path degradation (throttled per context+ticker)."""
    key = f"{context}:{ticker.upper()}" if ticker else context
    now = time.time()
    if now - _last_warn.get(key, 0.0) < throttle_sec:
        return
    _last_warn[key] = now
    suffix = f" {ticker.upper()}" if ticker else ""
    if exc is not None:
        log.warning(f"  ⚠️ Hot-path {context}{suffix}: {exc}")
    else:
        log.warning(f"  ⚠️ Hot-path {context}{suffix}: degraded (check IB/Gateway)")
