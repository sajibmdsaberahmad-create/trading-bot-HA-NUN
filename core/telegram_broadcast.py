#!/usr/bin/env python3
"""
core/telegram_broadcast.py — AI-crafted ops feed to all verified commander chats.
"""

from __future__ import annotations

import threading
import weakref
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.ai_telegram import format_outbound_message
from core.config import BotConfig
from core.notify import log
from core.telegram_auth import outbound_chat_ids, send_telegram_to_chat

if TYPE_CHECKING:
    from core.telegram_listener import TelegramCommandListener

_listener_ref: Optional[weakref.ReferenceType] = None


def register_listener(listener: "TelegramCommandListener") -> None:
    global _listener_ref
    _listener_ref = weakref.ref(listener)


def _compose(cfg: BotConfig, event: str, context: Dict[str, Any], fallback: str) -> str:
    listener = _listener_ref() if _listener_ref else None
    runner = getattr(listener, "runner", None) if listener else None
    ai_commander = getattr(listener, "ai_commander", None) if listener else None
    return format_outbound_message(
        cfg,
        event,
        context,
        fallback,
        ai_commander=ai_commander,
        runner=runner,
        copilot=True,
    )


def broadcast_ops(
    cfg: BotConfig,
    event: str,
    context: Dict[str, Any],
    fallback: str = "",
    *,
    legacy_message: Optional[str] = None,
) -> None:
    """AI-compose and send ops update to every verified commander chat."""
    if not getattr(cfg, "TELEGRAM_ENABLED", True):
        return
    if not getattr(cfg, "TELEGRAM_BROADCAST_OPS", True):
        return

    if legacy_message and not fallback:
        fallback = legacy_message
        context = dict(context or {})
        context.setdefault("raw_briefing", legacy_message)

    token = (getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        return

    if not outbound_chat_ids(cfg):
        log.debug(f"broadcast_ops ({event}): no verified chats")
        return

    def _run():
        text = _compose(cfg, event, context, fallback)
        if not text:
            text = fallback
        listener = _listener_ref() if _listener_ref else None
        for cid in outbound_chat_ids(cfg):
            try:
                if listener and hasattr(listener, "send"):
                    listener.send(cid, text)
                else:
                    send_telegram_to_chat(token, cid, text)
            except Exception as exc:
                log.debug(f"broadcast_ops ({event}) chat {cid}: {exc}")

    threading.Thread(target=_run, name=f"tg-broadcast-{event}", daemon=True).start()


def notify_git_push(cfg: BotConfig, message: str, category: str = "general", *, ok: bool = True) -> None:
    if not getattr(cfg, "TELEGRAM_BROADCAST_GIT", True):
        return
    fallback = f"GIT {'PUSH' if ok else 'FAIL'} [{category}]: {message}"
    broadcast_ops(
        cfg,
        "git_push",
        {"message": message, "category": category, "ok": ok, "success": ok},
        fallback,
    )


def notify_model_release(cfg: BotConfig, version: str, tag: str, notes: str = "") -> None:
    fallback = f"MODEL RELEASE v{version} tag {tag}\n{notes}"
    broadcast_ops(
        cfg,
        "model_release",
        {"version": version, "tag": tag, "notes": notes},
        fallback,
    )


def notify_learning_checkpoint(cfg: BotConfig, reason: str, *, ok: bool = True) -> None:
    fallback = f"LEARNING CHECKPOINT: {reason}"
    broadcast_ops(
        cfg,
        "learning_checkpoint",
        {"reason": reason, "ok": ok},
        fallback,
    )
