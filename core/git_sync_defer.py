#!/usr/bin/env python3
"""
core/git_sync_defer.py — Session git push deferral policy (extracted from git_sync).

Live HANOON queues learning checkpoints until shutdown unless GIT_PUSH_DURING_SESSION=true.
Replay batches everything until teardown flush.
"""

from __future__ import annotations

import os
from threading import Lock, Timer
from typing import Any, Callable, Dict, Optional, Set

from core.config import BotConfig

cfg_bot: Optional[BotConfig] = None

checkpoint_lock = Lock()
checkpoint_batched_reasons: Set[str] = set()
checkpoint_flush_timer: Optional[Timer] = None
deferred_push_count: int = 0

_flush_hook: Optional[Callable[[], None]] = None


def set_defer_config(cfg: Optional[BotConfig]) -> None:
    global cfg_bot
    cfg_bot = cfg


def register_session_flush_hook(fn: Callable[[], None]) -> None:
    global _flush_hook
    _flush_hook = fn


def is_replay_live() -> bool:
    return os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")


def git_session_push_enabled() -> bool:
    if is_replay_live():
        return False
    if cfg_bot is not None:
        return bool(getattr(cfg_bot, "GIT_PUSH_DURING_SESSION", False))
    return os.getenv("GIT_PUSH_DURING_SESSION", "false").lower() in ("1", "true", "yes")


def batch_checkpoints_enabled() -> bool:
    if is_replay_live():
        return True
    if os.getenv("GIT_BATCH_CHECKPOINTS", "true").lower() in ("0", "false", "no"):
        return False
    return git_session_push_enabled()


def should_defer_git_push(category: str = "general") -> bool:
    from core.git_sync import is_standalone_mode

    if is_standalone_mode() and not is_replay_live():
        return False
    if category in ("shutdown", "manual_sync", "replay_end"):
        return False
    if is_replay_live():
        return True
    if batch_checkpoints_enabled() and category in (
        "training", "trade", "checkpoint", "auto", "general", "daily",
        "guardrail", "model", "release",
    ):
        return True
    if cfg_bot is not None and not getattr(cfg_bot, "GIT_PUSH_DURING_SESSION", False):
        return True
    if not git_session_push_enabled():
        return True
    return False


def queue_batched_checkpoint(reason: str) -> None:
    global deferred_push_count
    r = (reason or "checkpoint").strip()[:120]
    with checkpoint_lock:
        if r not in checkpoint_batched_reasons:
            checkpoint_batched_reasons.add(r)
            deferred_push_count += 1


def schedule_batched_checkpoint_flush() -> None:
    if is_replay_live():
        return
    if not git_session_push_enabled():
        return
    global checkpoint_flush_timer
    delay = float(os.getenv("GIT_CHECKPOINT_DEBOUNCE_SEC", "180"))
    with checkpoint_lock:
        if checkpoint_flush_timer is not None:
            checkpoint_flush_timer.cancel()
        checkpoint_flush_timer = Timer(delay, batched_checkpoint_flush_callback)
        checkpoint_flush_timer.daemon = True
        checkpoint_flush_timer.start()


def batched_checkpoint_flush_callback() -> None:
    if not git_session_push_enabled():
        return
    if _flush_hook is not None:
        try:
            _flush_hook()
        except Exception as exc:
            from core.notify import log
            log.debug(f"Batched git flush: {exc}")


def shutdown_git_reason(summary_reason: str, combined: str = "") -> bool:
    blob = f"{summary_reason} {combined}".lower()
    return any(
        k in blob
        for k in ("pre_shutdown", "shutdown", "replay_end", "manual_sync")
    )


def batched_git_stats() -> Dict[str, Any]:
    with checkpoint_lock:
        pending = sorted(checkpoint_batched_reasons)
    return {
        "replay_live": is_replay_live(),
        "batch_enabled": batch_checkpoints_enabled(),
        "deferred_skips": deferred_push_count,
        "pending_reasons": pending,
        "pending_count": len(pending),
    }


# Back-compat aliases (git_sync re-exports with leading underscore)
_git_session_push_enabled = git_session_push_enabled
_batch_checkpoints_enabled = batch_checkpoints_enabled
_should_defer_git_push = should_defer_git_push
_queue_batched_checkpoint = queue_batched_checkpoint
_schedule_batched_checkpoint_flush = schedule_batched_checkpoint_flush
_shutdown_git_reason = shutdown_git_reason
