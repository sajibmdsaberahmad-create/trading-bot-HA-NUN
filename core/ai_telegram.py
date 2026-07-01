#!/usr/bin/env python3
"""
core/ai_telegram.py — Single entry for AI-crafted outbound Telegram (copilot + broadcast).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.ai_commander import AICommander
    from core.ai_notifier import TelegramAIComposer
    from core.scalper_runner import ScalperRunner

_global_composer: Optional["TelegramAIComposer"] = None


def register_global_composer(composer: "TelegramAIComposer") -> None:
    global _global_composer
    _global_composer = composer


def get_composer(
    cfg: BotConfig,
    *,
    ai_commander: Optional["AICommander"] = None,
    runner: Optional["ScalperRunner"] = None,
) -> "TelegramAIComposer":
    global _global_composer
    if _global_composer is not None:
        return _global_composer
    if runner is not None:
        notifier = getattr(runner, "notifier", None)
        if notifier and getattr(notifier, "_ai_composer", None):
            return notifier._ai_composer
    from core.ai_notifier import TelegramAIComposer
    composer = TelegramAIComposer(
        cfg,
        ai_commander=ai_commander,
        autopilot=getattr(runner, "autopilot", None) if runner else None,
        consciousness=getattr(runner, "consciousness", None) if runner else None,
        pilot=getattr(runner, "pilot", None) if runner else None,
    )
    if ai_commander:
        composer.attach(ai_commander=ai_commander)
    return composer


def format_outbound_message(
    cfg: BotConfig,
    event_type: str,
    context: Dict[str, Any],
    fallback: str = "",
    *,
    ai_commander: Optional["AICommander"] = None,
    runner: Optional["ScalperRunner"] = None,
    copilot: bool = False,
    max_chars: Optional[int] = None,
) -> str:
    """AI-compose any outbound Telegram text — copilot replies, broadcasts, alerts."""
    if runner is not None:
        try:
            from core.notify_ib_context import merge_ib_telegram_context
            context = merge_ib_telegram_context(
                runner, cfg, context, event_type=event_type,
            )
        except Exception as exc:
            log.debug(f"IB telegram context merge ({event_type}): {exc}")
    try:
        composer = get_composer(cfg, ai_commander=ai_commander, runner=runner)
        return composer.compose_outbound(
            event_type,
            context,
            fallback,
            copilot=copilot,
            max_chars=max_chars,
        )
    except Exception as exc:
        log.debug(f"AI telegram format ({event_type}): {exc}")
        return fallback
