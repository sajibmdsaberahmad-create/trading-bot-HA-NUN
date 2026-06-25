#!/usr/bin/env python3
"""
core/reward_shaping.py — Risk-shaped rewards for PPO / experience buffer learning.

Penalizes architectural mistakes (bracket rejects, late spike chase) not just PnL.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.config import BotConfig


def shaped_reward(
    cfg: BotConfig,
    base_reward: float,
    *,
    event: str = "trade",
    bracket_rejected: bool = False,
    inverted_bracket: bool = False,
    spike_ratio: float = 1.0,
    late_chase: bool = False,
    slippage_pct: float = 0.0,
) -> float:
    """
    R = base_reward - λ₁·reject - λ₂·late_chase - λ₃·slippage
    """
    r = float(base_reward)
    if bracket_rejected or inverted_bracket:
        r += float(getattr(cfg, "RL_BRACKET_REJECT_PENALTY", -1.0))
    vol_thr = float(getattr(cfg, "RL_LATE_SPIKE_VOL_THRESHOLD", 3.0))
    if late_chase or (spike_ratio >= vol_thr and event == "entry_reject"):
        r += float(getattr(cfg, "RL_LATE_SPIKE_PENALTY", -0.5))
    max_slip = float(getattr(cfg, "MAX_ACCEPTABLE_SLIPPAGE_PCT", 0.004))
    if abs(slippage_pct) > max_slip * 2:
        r += -0.25
    return round(r, 4)


def reward_from_trade(pnl_usd: float, cfg: BotConfig, **kwargs) -> float:
    return shaped_reward(cfg, pnl_usd, event="trade", **kwargs)


def reward_from_bracket_reject(cfg: BotConfig, spike_ratio: float = 1.0, inverted: bool = False) -> float:
    return shaped_reward(
        cfg, 0.0,
        event="entry_reject",
        bracket_rejected=True,
        inverted_bracket=inverted,
        spike_ratio=spike_ratio,
        late_chase=spike_ratio >= float(getattr(cfg, "RL_LATE_SPIKE_VOL_THRESHOLD", 3.0)),
    )
