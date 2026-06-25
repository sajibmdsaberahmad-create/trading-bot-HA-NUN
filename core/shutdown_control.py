#!/usr/bin/env python3
"""Graceful shutdown: PID file + stop-request flag for external stop commands."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PID_FILE = ROOT / "logs" / "hanoon.pid"
DEFAULT_SHUTDOWN_FILE = ROOT / "runtime" / "shutdown.request"


def pid_file() -> Path:
    raw = os.getenv("HANOON_PID_FILE") or os.getenv("PID_FILE") or str(DEFAULT_PID_FILE)
    return Path(raw)


def shutdown_file() -> Path:
    raw = os.getenv("HANOON_SHUTDOWN_FILE") or str(DEFAULT_SHUTDOWN_FILE)
    return Path(raw)


def write_pid(pid: Optional[int] = None) -> Path:
    """Record the running bot process id (used by stop_hanoon.sh)."""
    path = pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid if pid is not None else os.getpid()))
    return path


def read_pid() -> Optional[int]:
    path = pid_file()
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except (TypeError, ValueError, OSError):
        return None


def remove_pid_file() -> None:
    try:
        pid_file().unlink(missing_ok=True)
    except OSError:
        pass


def request_shutdown(reason: str = "external") -> Path:
    """Create stop-request file — main loop exits cleanly on next tick."""
    path = shutdown_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time():.0f} {reason}\n")
    return path


def clear_shutdown_request() -> None:
    try:
        shutdown_file().unlink(missing_ok=True)
    except OSError:
        pass


def shutdown_requested() -> bool:
    return shutdown_file().is_file()
