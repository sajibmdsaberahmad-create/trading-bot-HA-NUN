"""
Compact startup logging — one structured banner instead of dozens of INFO lines.
Set STARTUP_LOG_COMPACT=false for the full verbose boot trace.
"""

from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig

_quiet_phase: bool = False


def startup_compact(cfg: Optional["BotConfig"] = None) -> bool:
    if cfg is not None:
        return getattr(cfg, "STARTUP_LOG_COMPACT", True)
    return True


def set_quiet_phase(active: bool) -> None:
    global _quiet_phase
    _quiet_phase = active


def in_quiet_phase() -> bool:
    return _quiet_phase


def sinfo(cfg: Optional["BotConfig"], msg: str, *, force: bool = False) -> None:
    """INFO when verbose boot; DEBUG when compact (unless force=True)."""
    if force or not startup_compact(cfg):
        log.info(msg)
    else:
        log.debug(msg)


def slog(msg: str, *, force: bool = False) -> None:
    """INFO during boot unless quiet phase or compact default."""
    if force or not _quiet_phase:
        log.info(msg)
    else:
        log.debug(msg)


def log_block(title: str, lines: List[str], *, width: int = 62) -> None:
    bar = "─" * width
    log.info(bar)
    log.info(f"  {title}")
    for line in lines:
        if line:
            log.info(f"  {line}")
    log.info(bar)
