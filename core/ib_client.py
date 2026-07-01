#!/usr/bin/env python3
"""
core/ib_client.py — IB API backend shim (ib_async or legacy ib_insync).

Set IB_CLIENT_BACKEND=ib_async for the maintained fork (default: ib_insync).
Falls back to the other package when the preferred one is not installed.
"""

from __future__ import annotations

import os
from typing import Any

_BACKEND = os.getenv("IB_CLIENT_BACKEND", "ib_insync").strip().lower()
_LOADED_NAME = ""
_MODULE: Any = None


def ib_backend_name() -> str:
    """Active backend: 'ib_async' or 'ib_insync'."""
    _ensure_loaded()
    return _LOADED_NAME


def ib_module() -> Any:
    """Loaded IB client module."""
    _ensure_loaded()
    return _MODULE


def _ensure_loaded() -> None:
    global _MODULE, _LOADED_NAME
    if _MODULE is not None:
        return

    prefer = _BACKEND if _BACKEND in ("ib_async", "ib_insync") else "ib_insync"
    candidates = [prefer, "ib_insync" if prefer == "ib_async" else "ib_async"]
    last_err: Exception | None = None
    for name in candidates:
        try:
            _MODULE = __import__(name)
            _LOADED_NAME = name
            return
        except ImportError as exc:
            last_err = exc

    raise SystemExit(
        "\nERROR: No IB client library found.\n"
        "Fix:   pip install ib-insync\n"
        "   or: pip install ib_async\n"
        f"Detail: {last_err}\n"
    )


def __getattr__(name: str) -> Any:
    _ensure_loaded()
    return getattr(_MODULE, name)
