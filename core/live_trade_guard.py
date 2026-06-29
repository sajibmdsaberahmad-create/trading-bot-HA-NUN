#!/usr/bin/env python3
"""
core/live_trade_guard.py — Live entry guards: PPO-alignment, ticker loss cooldown,
loss-streak bypass block. In-memory only (no SSD churn).
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig

_ticker_loss_until: Dict[str, float] = {}
_ticker_loss_streak: Dict[str, int] = {}
_session_loss_tickers: Dict[str, int] = {}
_tight_mode_until: float = 0.0


def activate_loss_streak_tight_mode(duration_sec: Optional[float] = None) -> None:
    """In-memory tighten — no config file / SSD write."""
    global _tight_mode_until
    dur = float(duration_sec or os.getenv("LOSS_STREAK_TIGHT_SEC", "1800"))
    _tight_mode_until = time.time() + dur


def loss_streak_tight_active() -> bool:
    return time.time() < _tight_mode_until


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def on_trade_closed(ticker: str, pnl_usd: float, cfg: Optional[BotConfig] = None) -> None:
    """Per-ticker loss cooldown — memory only."""
    t = str(ticker or "").upper()
    if not t:
        return
    pnl = float(pnl_usd or 0)
    if pnl >= 0:
        _ticker_loss_streak[t] = 0
        return
    _ticker_loss_streak[t] = int(_ticker_loss_streak.get(t, 0)) + 1
    _session_loss_tickers[t] = int(_session_loss_tickers.get(t, 0)) + 1
    base_cd = float(os.getenv("TICKER_LOSS_COOLDOWN_SEC", "180"))
    repeat_cd = float(os.getenv("TICKER_LOSS_COOLDOWN_REPEAT_SEC", "600"))
    streak = _ticker_loss_streak[t]
    cd = repeat_cd if streak >= 2 or _session_loss_tickers[t] >= 3 else base_cd
    _ticker_loss_until[t] = time.time() + cd


def ticker_cooldown_remaining(ticker: str) -> float:
    t = str(ticker or "").upper()
    return max(0.0, _ticker_loss_until.get(t, 0) - time.time())


def check_ticker_cooldown(ticker: str) -> Optional[str]:
    rem = ticker_cooldown_remaining(ticker)
    if rem > 0:
        return f"ticker loss cooldown {ticker.upper()} ({rem:.0f}s)"
    return None


def ppo_bypass_requires_buy(cfg: Optional[BotConfig] = None) -> bool:
    return _env_bool("PPO_BYPASS_REQUIRES_BUY", "true")


def loss_streak_block_at(cfg: Optional[BotConfig] = None) -> int:
    return max(1, int(os.getenv("LOSS_STREAK_BLOCK_BYPASS_AT", "2")))


def check_fast_entry_bypass(
    cfg: Optional[BotConfig],
    *,
    ticker: str = "",
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    consecutive_losses: int = 0,
    pipeline: str = "",
) -> Optional[str]:
    """
    Return block reason if spike/scanner fast path must not fire.
    None = allowed to proceed (still subject to other gates).
    """
    cfg = cfg or BotConfig()
    cd_msg = check_ticker_cooldown(ticker)
    if cd_msg:
        return cd_msg
    if consecutive_losses >= loss_streak_block_at(cfg):
        return f"loss streak {consecutive_losses} — fast bypass blocked"
    if loss_streak_tight_active():
        return "loss-streak tight mode — fast bypass blocked"
    if not ppo_bypass_requires_buy(cfg):
        return None
    if int(ppo_action) == 1:
        min_conf = float(os.getenv("PPO_BYPASS_MIN_CONF", "0.48"))
        if float(ppo_conf) >= min_conf:
            return None
        return f"PPO BUY conf {ppo_conf:.0%} < {min_conf:.0%}"
    # PPO says HOLD — only allow bypass on exceptional aligned setups
    min_score = float(os.getenv("PPO_OVERRIDE_MIN_SCAN_SCORE", "94"))
    min_spike = float(os.getenv("PPO_OVERRIDE_MIN_SPIKE_RATIO", "2.5"))
    min_conf = float(os.getenv("PPO_OVERRIDE_MIN_CONF", "0.55"))
    if (
        float(ppo_conf) >= min_conf
        and str(pipeline).startswith("ppo:")
    ):
        return None
    return (
        f"PPO HOLD (action={int(ppo_action)}) — "
        f"spike/scanner bypass blocked (need BUY or score≥{min_score:.0f})"
    )


def strong_spike_allowed(
    cfg: Optional[BotConfig],
    *,
    ticker: str,
    scan_score: float,
    spike_ratio: float,
    ppo_action: int,
    ppo_conf: float,
    consecutive_losses: int = 0,
    micro: Optional[dict] = None,
) -> Tuple[bool, str]:
    """Gate for disciplined strong-spike entries."""
    from core.fast_execution import _passes_entry_quality_gate

    block = check_fast_entry_bypass(
        cfg,
        ticker=ticker,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        consecutive_losses=consecutive_losses,
        pipeline="ppo:strong_spike",
    )
    if block:
        return False, block
    if int(ppo_action) == 1:
        min_ppo = float(getattr(cfg or BotConfig(), "CAPITAL_STRONG_MIN_PPO_CONF", 0.48))
        if float(ppo_conf) >= min_ppo and _passes_entry_quality_gate(
            cfg, micro or {}, spike_ratio, scan_score, ppo_action, ppo_conf,
        ):
            return True, "PPO BUY aligned"
        return False, f"PPO BUY conf {ppo_conf:.0%} below {min_ppo:.0%} or quality fail"
    return False, "PPO HOLD — strong-spike requires PPO BUY"


def loss_streak_heuristic_mutations(cfg: BotConfig, streak: int) -> list:
    """Extra deterministic mutations when AI plan parse fails."""
    muts = []
    if streak >= 2:
        muts.append({
            "param": "CAPITAL_STRONG_SPIKE_FAST",
            "value": False,
            "reason": f"Loss streak {streak} — disable strong-spike PPO bypass",
        })
    if streak >= 3:
        muts.append({
            "param": "PPO_LEAD_WHILE_COUNCIL_PENDING",
            "value": False,
            "reason": f"Loss streak {streak} — no PPO lead while council pending",
        })
        muts.append({
            "param": "AI_SPIKE_FAST_ENTRY",
            "value": False,
            "reason": f"Loss streak {streak} — disable raw spike-fast entries",
        })
    cur = float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.55))
    muts.append({
        "param": "CONFIDENCE_THRESHOLD",
        "value": min(0.78, cur + 0.04),
        "reason": f"Loss streak {streak} — raise entry confidence",
    })
    return muts[:4]
