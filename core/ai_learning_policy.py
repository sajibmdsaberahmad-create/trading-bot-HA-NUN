#!/usr/bin/env python3
"""
core/ai_learning_policy.py — Learn from failures instead of hard-blocking.

When enabled, IB rejects, bracket mistakes, and venue quirks become training
signals (experience buffer + pilot XP) with short soft cooldowns — not permanent
blacklists or rigid rule gates.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log
from core.reward_shaping import reward_from_bracket_reject


def learn_dont_block(cfg: Optional[BotConfig] = None) -> bool:
    """
    True → prefer learning + retry over permanent skips and static filters.
    Default ON for paper free-learning + AI full control.
    """
    cfg = cfg or BotConfig()
    env = os.getenv("AI_LEARN_DONT_BLOCK", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    if not getattr(cfg, "AI_LEARN_DONT_BLOCK", True):
        return False
    if getattr(cfg, "AI_FULL_CONTROL", True) and getattr(cfg, "AI_PAPER_FREE_LEARNING", True):
        return True
    return bool(getattr(cfg, "AI_LEARN_DONT_BLOCK", True))


def failure_cooldown_sec(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    if learn_dont_block(cfg):
        return float(getattr(cfg, "AI_FAILURE_SOFT_COOLDOWN_SEC", 30.0))
    return float(getattr(cfg, "AI_FAILURE_HARD_COOLDOWN_SEC", 3600.0))


def should_permanent_blacklist(cfg: Optional[BotConfig] = None, reason: str = "") -> bool:
    """Only block symbols that are structurally untradeable."""
    cfg = cfg or BotConfig()
    r = (reason or "").lower()
    if "no ib contract" in r or "contract not found" in r:
        return True
    if learn_dont_block(cfg):
        return False
    return True


def record_failure_for_learning(
    cfg: BotConfig,
    *,
    ticker: str,
    reason: str,
    event: str = "ib_failure",
    spike_ratio: float = 1.0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Write a negative-reward experience so PPO / distiller can learn."""
    try:
        from core.experience_buffer import append as buffer_append

        reward = reward_from_bracket_reject(cfg, spike_ratio=spike_ratio)
        if event == "ib_failure":
            reward = min(reward, -0.35)
        rec: Dict[str, Any] = {
            "source": event,
            "ticker": ticker,
            "action": "FAILURE",
            "reason": (reason or "")[:300],
            "reward": reward,
            "win": False,
            "confidence": 0.0,
            "spike_ratio": spike_ratio,
        }
        if extra:
            rec.update(extra)
        buffer_append(rec)
    except Exception as exc:
        log.debug(f"Failure learning record: {exc}")

    try:
        from core.post_mortem_audit import append_post_mortem

        append_post_mortem(
            ticker=ticker,
            event=event,
            reason=reason,
            learn_mode=True,
        )
    except Exception:
        pass
