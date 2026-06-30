#!/usr/bin/env python3
"""Structured IB connectivity audit — append-only jsonl + matching log lines."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.notify import log

_JOURNAL_PATH = Path("models/ib_connectivity.jsonl")


def log_ib_connectivity(
    event: str,
    *,
    source: str = "ib_connector",
    level: str = "info",
    **fields: Any,
) -> Dict[str, Any]:
    """Append one connectivity event and emit a single detailed log line."""
    row: Dict[str, Any] = {
        "ts": time.time(),
        "event": event,
        "source": source,
        **{k: v for k, v in fields.items() if v is not None},
    }
    try:
        _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_JOURNAL_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:
        log.debug(f"ib_connectivity journal write failed: {exc}")

    detail = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    msg = f"IB connectivity | {event}"
    if detail:
        msg = f"{msg} | {detail}"

    lvl = (level or "info").lower()
    if lvl == "warning":
        log.warning(msg)
    elif lvl == "error":
        log.error(msg)
    else:
        log.info(msg)
    return row


def format_duration(seconds: float) -> str:
    sec = max(0.0, float(seconds))
    if sec < 60:
        return f"{sec:.0f}s"
    mins, rem = divmod(int(sec), 60)
    if mins < 60:
        return f"{mins}m {rem}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m"
