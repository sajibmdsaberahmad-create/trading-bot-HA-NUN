#!/usr/bin/env python3
"""
core/live_trade_guard.py — Live entry guards: PPO-alignment, ticker loss cooldown,
loss-streak bypass block, session loss memory for council/Halim/copilot. In-memory only.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig

_ticker_loss_until: Dict[str, float] = {}
_ticker_loss_streak: Dict[str, int] = {}
_session_loss_tickers: Dict[str, int] = {}
_ticker_session_pnl: Dict[str, float] = {}
_ticker_loss_log: Dict[str, List[Dict[str, Any]]] = {}
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


def _cooldown_tiers() -> List[float]:
    return [
        float(os.getenv("TICKER_LOSS_COOLDOWN_SEC", "180")),
        float(os.getenv("TICKER_LOSS_COOLDOWN_REPEAT_SEC", "600")),
        float(os.getenv("TICKER_LOSS_COOLDOWN_TIER3_SEC", "1200")),
        float(os.getenv("TICKER_LOSS_COOLDOWN_TIER4_SEC", "1800")),
    ]


def _cooldown_sec_for(streak: int, session_losses: int) -> float:
    tiers = _cooldown_tiers()
    idx = max(streak - 1, session_losses - 1, 0)
    idx = min(idx, len(tiers) - 1)
    return tiers[idx]


def session_loss_count(ticker: str) -> int:
    return int(_session_loss_tickers.get(str(ticker or "").upper(), 0))


def session_pnl_usd(ticker: str) -> float:
    return float(_ticker_session_pnl.get(str(ticker or "").upper(), 0.0))


def get_repeat_losers(min_losses: int = 2) -> List[str]:
    thr = max(1, int(min_losses))
    return sorted(
        tk for tk, n in _session_loss_tickers.items()
        if int(n) >= thr
    )


def ticker_loss_streak(ticker: str) -> int:
    return int(_ticker_loss_streak.get(str(ticker or "").upper(), 0))


def loss_context_for_prompt(ticker: str) -> str:
    """One-line session memory for council / Halim prompts."""
    t = str(ticker or "").upper()
    if not t:
        return ""
    losses = session_loss_count(t)
    if losses <= 0:
        return ""
    pnl = session_pnl_usd(t)
    streak = ticker_loss_streak(t)
    rem = ticker_cooldown_remaining(t)
    last = (_ticker_loss_log.get(t) or [])[-1:]
    last_reason = str(last[0].get("reason", ""))[:50] if last else ""
    parts = [
        f"SESSION LOSS MEMORY {t}: {losses} loss(es) session_pnl=${pnl:+.2f}",
        f"streak={streak}",
    ]
    if rem > 0:
        parts.append(f"cooldown={rem:.0f}s")
    if last_reason:
        parts.append(f"last_exit={last_reason}")
    parts.append(
        "Adapt: require PPO BUY + higher quality; do not chase same fakeout."
    )
    return " | ".join(parts)


def guard_conf_bump(ticker: str) -> float:
    """Extra min_confidence for repeat losers — scales with session losses."""
    losses = session_loss_count(ticker)
    if losses < 2:
        return 0.0
    per = float(os.getenv("GUARD_REPEAT_LOSS_CONF_BUMP", "0.04"))
    cap = float(os.getenv("GUARD_REPEAT_LOSS_CONF_CAP", "0.16"))
    return min(cap, per * (losses - 1))


def ticker_bias_label(ticker: str) -> str:
    losses = session_loss_count(ticker)
    ban_after = int(os.getenv("GUARD_SESSION_BAN_AFTER", "5"))
    if losses >= ban_after:
        return "SKIP"
    if losses >= 3:
        return "CAUTION"
    if losses >= 2:
        return "CAUTION"
    return "OK"


def enrich_brief_from_guard(brief: Any) -> Any:
    """Merge live guard state into copilot brief — no SSD write."""
    from core.trading_copilot import CopilotBrief

    repeat = get_repeat_losers(min_losses=2)
    if not repeat and not _session_loss_tickers:
        return brief
    bias = dict(getattr(brief, "ticker_bias", None) or {})
    merged_repeat = list(dict.fromkeys(
        [str(x).upper() for x in (getattr(brief, "repeat_losers", None) or [])] + repeat
    ))
    lessons = list(getattr(brief, "lessons", None) or [])
    for tk in repeat:
        label = ticker_bias_label(tk)
        prev = str(bias.get(tk, "OK")).upper()
        if prev == "OK" or (label == "SKIP" and prev != "SKIP"):
            bias[tk] = label
        pnl = session_pnl_usd(tk)
        n = session_loss_count(tk)
        lessons.append(
            f"{tk}: {n} session losses (${pnl:+.0f}) — tighten entry, PPO BUY only"
        )
    ppo_hints = dict(getattr(brief, "ppo_hints", None) or {})
    if repeat:
        ppo_hints.setdefault("require_quality_on_repeat", True)
        worst = max((session_loss_count(tk) for tk in repeat), default=0)
        ppo_hints["min_spike_mult"] = max(
            float(ppo_hints.get("min_spike_mult", 1.0)),
            1.12 + 0.03 * min(3, worst),
        )
    posture = getattr(brief, "risk_posture", "normal")
    if len(repeat) >= 2:
        posture = "defensive"
    return CopilotBrief(
        narrative=getattr(brief, "narrative", ""),
        regime_read=getattr(brief, "regime_read", "unknown"),
        risk_posture=posture,
        session_wr=float(getattr(brief, "session_wr", 0)),
        session_pnl=float(getattr(brief, "session_pnl", 0)),
        ticker_bias=bias,
        repeat_losers=merged_repeat,
        ppo_hints=ppo_hints,
        lessons=lessons[-8:],
        updated_at=max(float(getattr(brief, "updated_at", 0)), time.time()),
        source=f"{getattr(brief, 'source', 'none')}+guard",
    )


def on_trade_closed(
    ticker: str,
    pnl_usd: float,
    cfg: Optional[BotConfig] = None,
    *,
    exit_reason: str = "",
) -> None:
    """Per-ticker loss cooldown + session memory — memory only."""
    t = str(ticker or "").upper()
    if not t:
        return
    pnl = float(pnl_usd or 0)
    _ticker_session_pnl[t] = float(_ticker_session_pnl.get(t, 0.0)) + pnl
    if pnl >= 0:
        _ticker_loss_streak[t] = 0
        return
    _ticker_loss_streak[t] = int(_ticker_loss_streak.get(t, 0)) + 1
    _session_loss_tickers[t] = int(_session_loss_tickers.get(t, 0)) + 1
    log = _ticker_loss_log.setdefault(t, [])
    log.append({
        "pnl_usd": round(pnl, 2),
        "reason": str(exit_reason or "")[:80],
        "ts": time.time(),
    })
    _ticker_loss_log[t] = log[-8:]
    streak = _ticker_loss_streak[t]
    session_n = _session_loss_tickers[t]
    cd = _cooldown_sec_for(streak, session_n)
    _ticker_loss_until[t] = time.time() + cd


def ticker_cooldown_remaining(ticker: str) -> float:
    t = str(ticker or "").upper()
    return max(0.0, _ticker_loss_until.get(t, 0) - time.time())


def check_ticker_cooldown(ticker: str) -> Optional[str]:
    rem = ticker_cooldown_remaining(ticker)
    if rem > 0:
        n = session_loss_count(ticker)
        return f"ticker loss cooldown {ticker.upper()} ({rem:.0f}s, {n} session losses)"
    return None


def check_entry_allowed(ticker: str, cfg: Optional[BotConfig] = None) -> Optional[str]:
    """Hard gate for all entry paths (council, PPO, scanner)."""
    cfg = cfg or BotConfig()
    cd_msg = check_ticker_cooldown(ticker)
    if cd_msg:
        return cd_msg
    ban_after = int(os.getenv("GUARD_SESSION_BAN_AFTER", "5"))
    if session_loss_count(ticker) >= ban_after:
        return (
            f"{ticker.upper()} session ban — {session_loss_count(ticker)} losses "
            f"(${session_pnl_usd(ticker):+.0f}); wait for off-hours reset"
        )
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
    block = check_entry_allowed(ticker, cfg)
    if block:
        return block
    if consecutive_losses >= loss_streak_block_at(cfg):
        return f"loss streak {consecutive_losses} — fast bypass blocked"
    if loss_streak_tight_active():
        return "loss-streak tight mode — fast bypass blocked"
    losses = session_loss_count(ticker)
    if losses >= 3 and int(ppo_action) != 1:
        return f"{ticker.upper()} {losses} session losses — PPO BUY required"
    if not ppo_bypass_requires_buy(cfg):
        return None
    if int(ppo_action) == 1:
        min_conf = float(os.getenv("PPO_BYPASS_MIN_CONF", "0.48"))
        bump = guard_conf_bump(ticker)
        if float(ppo_conf) >= min_conf + bump:
            return None
        return f"PPO BUY conf {ppo_conf:.0%} < {min_conf + bump:.0%} (repeat-loser bar)"
    return (
        f"PPO HOLD (action={int(ppo_action)}) — "
        f"spike/scanner bypass blocked (need PPO BUY on repeat losers)"
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
        min_ppo += guard_conf_bump(ticker)
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
    repeat = get_repeat_losers(min_losses=2)
    if repeat:
        muts.append({
            "param": "PPO_BYPASS_REQUIRES_BUY",
            "value": True,
            "reason": f"Repeat losers {','.join(repeat[:4])} — PPO BUY only",
        })
    return muts[:5]
