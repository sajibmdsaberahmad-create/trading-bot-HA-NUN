#!/usr/bin/env python3
"""
core/paper_mode.py — Paper-account free learning (no small-account caps).

When PAPER_TRADING + AI_PAPER_FREE_LEARNING, the AI sizes from live IB equity
(~$1M paper) and learns from outcomes without $50/$1k training-wheel limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import BotConfig


def is_paper_free_learning(cfg: "BotConfig") -> bool:
    return bool(getattr(cfg, "PAPER_TRADING", False)) and bool(
        getattr(cfg, "AI_PAPER_FREE_LEARNING", True)
    )


def account_equity(cfg: "BotConfig") -> float:
    """Best available equity — IB live balance preferred."""
    for attr in ("_latest_account_balance",):
        val = float(getattr(cfg, attr, 0) or 0)
        if val > 0:
            return val
    if is_paper_free_learning(cfg):
        return float(getattr(cfg, "PAPER_EQUITY_HINT", 1_000_000))
    return float(getattr(cfg, "INITIAL_CASH", 1000.0))
