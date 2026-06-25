#!/usr/bin/env python3
"""
core/profit_hunting.py — Primary mission: opportunistic profit extraction.

Profit hunting is THE main goal. Algo + AI have full freedom to pursue profit
within hard risk limits. Every hunt signal, exit, miss, and council decision
is logged to the ledger + experience buffer for continuous learning.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

LEDGER_PATH = Path("models/profit_hunt_ledger.jsonl")
_ledger_lock = threading.Lock()

PROFIT_HUNT_PRIMARY_MISSION = (
    "PRIMARY MISSION — PROFIT HUNTING:\n"
    "Your sole purpose is to extract profit from every opportunity. Full freedom to:\n"
    "- Enter any spike, momentum burst, or scanner signal you believe will profit\n"
    "- Exit immediately on spike tops, hard TP, trailing profit — sell INTO moves\n"
    "- Trail, extend targets, hot-swap — whatever makes money in the moment\n"
    "Hard risk limits (max loss/trade, position count) are the ONLY constraints.\n"
    "Every decision is logged and learned from — adapt aggressively from the ledger."
)

PROFIT_HUNT_DOCTRINE = (
    f"{PROFIT_HUNT_PRIMARY_MISSION}\n"
    "Tactics (AI-tune from outcomes):\n"
    "- Hunt spikes: single-bar momentum + volume = take profit INTO the move.\n"
    "- Do not passively wait for large giveback on clean spike tops (NOK 14:20 waves).\n"
    "- Extended hours: tighter giveback; intra-bar tick bursts before 1-min close.\n"
    "- Missed spike-top exits = training failures — buffer penalties apply.\n"
    "- Tune SPIKE_TOP_MIN_GAIN_PCT, SPIKE_TOP_MIN_VOL_RATIO, PROFIT_HUNT_MIN_PNL_PCT."
)

MECHANICAL_PROFIT_EXIT_REASONS = frozenset({
    "spike_top_exit",
    "spike_top_intrabar",
    "hard_take_profit",
    "trailing_profit",
    "wave_end_spike_fade",
    "profit_lock",
    "profit_hunt",
    "council_exit",
})


def is_profit_hunt_primary(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "PROFIT_HUNT_PRIMARY_GOAL", True))


def is_mechanical_profit_exit(reason: str) -> bool:
    r = (reason or "").lower()
    return any(sig in r for sig in MECHANICAL_PROFIT_EXIT_REASONS)


def mechanical_bypass_council(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "PROFIT_HUNT_MECHANICAL_BYPASS_COUNCIL", True))


def profit_hunt_full_freedom(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "PROFIT_HUNT_FULL_FREEDOM", True))


def profit_exit_bypasses_hold(cfg: BotConfig, pnl_pct: float = 0.0, reason: str = "") -> bool:
    """Profit exits skip min-hold when primary goal is on and position is green."""
    if pnl_pct <= 0:
        return False
    if not getattr(cfg, "PROFIT_HUNT_SKIP_MIN_HOLD", True):
        return False
    if is_profit_hunt_primary(cfg):
        return True
    return is_mechanical_profit_exit(reason)


def profit_exit_bypasses_council(
    cfg: BotConfig,
    reason: str = "",
    pnl_pct: float = 0.0,
) -> bool:
    """Instant profit exits — council cannot veto when hunting is primary."""
    if mechanical_bypass_council(cfg) and is_mechanical_profit_exit(reason):
        return True
    if not profit_hunt_full_freedom(cfg) or pnl_pct <= 0:
        return False
    if is_mechanical_profit_exit(reason):
        return True
    r = (reason or "").lower()
    return any(k in r for k in ("profit", "spike_top", "take_profit", "trailing", "wave_end"))


def _append_ledger(row: Dict[str, Any]) -> None:
    if not getattr(BotConfig(), "PROFIT_HUNT_TRACK_ALL", True):
        pass
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ledger_lock:
            with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"Profit hunt ledger: {exc}")


def track_profit_hunt_event(
    cfg: BotConfig,
    event: str,
    ticker: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    pnl_usd: float = 0.0,
    pnl_pct: float = 0.0,
    record_buffer: bool = True,
    push_git: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Always-on profit hunt telemetry: ledger → buffer → optional git sync.
    Call on every hunt evaluation, spike detect, exit, miss, and council hold.
    """
    ctx = dict(context or {})
    row: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "ticker": ticker,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 4),
        "primary_goal": is_profit_hunt_primary(cfg),
    }
    row.update({k: v for k, v in ctx.items() if k not in row})
    if getattr(cfg, "PROFIT_HUNT_TRACK_ALL", True):
        _append_ledger(row)

    if record_buffer:
        record_profit_hunt_learning(
            cfg,
            event=event,
            ticker=ticker,
            context={**ctx, "reason": ctx.get("reason", event)},
            pnl_usd=pnl_usd,
            won=pnl_usd > 0,
            skip_ledger=True,
        )

    do_push = push_git if push_git is not None else bool(
        getattr(cfg, "LEARNING_PUSH_ON_TRADE", True)
        and event in (
            "hunt_exit", "missed_profit_hunt", "spike_top_exit", "spike_top_intrabar",
            "hard_take_profit", "trailing_profit", "wave_end_spike_fade",
        )
    )
    if do_push:
        try:
            from core.git_sync import push_learning_checkpoint_async
            push_learning_checkpoint_async(f"profit_hunt:{event}:{ticker}")
        except Exception:
            pass

    return row


def _f(cfg: BotConfig, name: str, default: float) -> float:
    return float(getattr(cfg, name, default))


def effective_spike_top_gain_pct(cfg: BotConfig, *, extended: bool = False) -> float:
    base = _f(cfg, "SPIKE_TOP_MIN_GAIN_PCT", 0.005)
    if extended:
        base *= 0.85
    return max(0.002, base)


def effective_spike_top_vol_ratio(cfg: BotConfig, *, extended: bool = False) -> float:
    base = _f(cfg, "SPIKE_TOP_MIN_VOL_RATIO", 1.15)
    if extended:
        base = max(1.05, base - 0.05)
    return base


def effective_profit_hunt_min_pnl(cfg: BotConfig) -> float:
    return _f(cfg, "PROFIT_HUNT_MIN_PNL_PCT", 0.003)


def effective_extended_giveback(cfg: BotConfig) -> float:
    return _f(cfg, "EXTENDED_PROFIT_GIVEBACK_PCT", 0.30)


def _bar_gain_pct(df: pd.DataFrame, idx: int = -1) -> float:
    if len(df) < 2:
        return 0.0
    o = float(df["open"].iloc[idx])
    c = float(df["close"].iloc[idx])
    if o <= 0:
        return 0.0
    return (c - o) / o


def _volume_spike_ratio(df: pd.DataFrame, idx: int = -1) -> float:
    if len(df) < 20:
        return 1.0
    vols = df["volume"].values
    i = len(vols) + idx if idx < 0 else idx
    if i < 1:
        return 1.0
    avg = float(np.mean(vols[max(0, i - 19):i]))
    cur = float(vols[i])
    return cur / avg if avg > 0 else 1.0


def detect_bar_spike_top(
    df: pd.DataFrame,
    current_px: float,
    cfg: BotConfig,
    *,
    extended: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Spike on last completed or forming bar: fast gain + elevated volume."""
    if df is None or len(df) < 5:
        return False, {}
    min_gain = effective_spike_top_gain_pct(cfg, extended=extended)
    min_vol = effective_spike_top_vol_ratio(cfg, extended=extended)
    ctx: Dict[str, Any] = {"extended": extended}

    for label, idx in (("last", -1), ("prev", -2)):
        gain = _bar_gain_pct(df, idx)
        vol_r = _volume_spike_ratio(df, idx)
        high = float(df["high"].iloc[idx])
        ctx[f"{label}_gain_pct"] = round(gain, 5)
        ctx[f"{label}_vol_ratio"] = round(vol_r, 3)
        if gain >= min_gain and vol_r >= min_vol:
            near_high = current_px >= high * 0.998
            ctx.update({
                "spike_detected": True,
                "bar": label,
                "gain_pct": gain,
                "vol_ratio": vol_r,
                "near_high": near_high,
            })
            if near_high:
                return True, ctx
    return False, ctx


def detect_intrabar_spike_top(
    dm: Any,
    df: pd.DataFrame,
    current_px: float,
    cfg: BotConfig,
    *,
    extended: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """5s-bar accumulation + tick burst for mid-minute spike tops."""
    if not getattr(cfg, "SPIKE_TOP_INTRABAR_ENABLED", True):
        return False, {}
    min_gain = effective_spike_top_gain_pct(cfg, extended=extended) * 0.75
    min_vol = effective_spike_top_vol_ratio(cfg, extended=extended)

    fast = dm.get_fast_bar_dataframe(n=12) if dm and hasattr(dm, "get_fast_bar_dataframe") else None
    if fast is not None and len(fast) >= 2:
        recent = fast.tail(3)
        o0 = float(recent["open"].iloc[0])
        c1 = float(recent["close"].iloc[-1])
        if o0 > 0:
            gain = (c1 - o0) / o0
            vol_sum = float(recent["volume"].sum())
            avg_vol = float(df["volume"].tail(20).mean()) if df is not None and len(df) >= 20 else 1.0
            vol_r = vol_sum / avg_vol if avg_vol > 0 else 1.0
            if gain >= min_gain and vol_r >= min_vol:
                return True, {
                    "spike_detected": True,
                    "intrabar": True,
                    "gain_pct": gain,
                    "vol_ratio": vol_r,
                    "near_high": True,
                }

    ticks = list(getattr(dm, "_tick_buffer", [])) if dm else []
    if len(ticks) >= 8 and df is not None and len(df) >= 20:
        recent_vol = sum(int(t.get("size", 0)) for t in ticks[-80:])
        avg_vol = float(df["volume"].tail(20).mean())
        if avg_vol > 0:
            vol_r = recent_vol / avg_vol
            if vol_r >= min_vol:
                prices = [float(t.get("price", 0)) for t in ticks[-40:] if float(t.get("price", 0)) > 0]
                if len(prices) >= 4:
                    p0, p1 = prices[0], prices[-1]
                    if p0 > 0:
                        gain = (p1 - p0) / p0
                        if gain >= min_gain and current_px >= p1 * 0.998:
                            return True, {
                                "spike_detected": True,
                                "tick_burst": True,
                                "gain_pct": gain,
                                "vol_ratio": vol_r,
                            }
    return False, {}


def evaluate_spike_top_exit(
    cfg: BotConfig,
    df: Optional[pd.DataFrame],
    dm: Any,
    current_px: float,
    entry_px: float,
    pnl_pct: float,
    peak_px: float,
    *,
    extended: bool = False,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (should_exit, reason, context).
    Opportunistic spike-top take-profit while in profit.
    """
    if not getattr(cfg, "PROFIT_HUNT_ENABLED", True):
        return False, "", {}
    if not getattr(cfg, "SPIKE_TOP_EXIT_ENABLED", True):
        return False, "", {}
    if entry_px <= 0 or current_px <= 0:
        return False, "", {}
    if pnl_pct < effective_profit_hunt_min_pnl(cfg):
        return False, "", {}

    bar_hit, bar_ctx = detect_bar_spike_top(df, current_px, cfg, extended=extended)
    intra_hit, intra_ctx = detect_intrabar_spike_top(dm, df, current_px, cfg, extended=extended)

    ctx = {**bar_ctx, **intra_ctx}
    if intra_hit:
        return True, (
            f"spike_top_intrabar: +{intra_ctx.get('gain_pct', 0):.2%} "
            f"vol={intra_ctx.get('vol_ratio', 1):.1f}x"
        ), ctx
    if bar_hit:
        return True, (
            f"spike_top_exit: +{bar_ctx.get('gain_pct', 0):.2%} "
            f"vol={bar_ctx.get('vol_ratio', 1):.1f}x"
        ), ctx

    # Spike forming but not at high yet — context for missed-hunt tracking
    if ctx.get("spike_detected") and not ctx.get("near_high", True):
        ctx["forming_spike"] = True
    return False, "", ctx


def evaluate_wave_end_on_spike_fade(
    cfg: BotConfig,
    df: Optional[pd.DataFrame],
    current_px: float,
    entry_px: float,
    peak_px: float,
    pnl_pct: float,
) -> Tuple[bool, str]:
    """
    Exit when spike volume fades after a profitable run (fixes old wave_end skip during spike).
    """
    if not getattr(cfg, "PROFIT_HUNT_ENABLED", True):
        return False, ""
    if df is None or len(df) < 10 or entry_px <= 0:
        return False, ""
    if pnl_pct < effective_profit_hunt_min_pnl(cfg):
        return False, ""

    peak_gain = (peak_px - entry_px) / entry_px if peak_px > entry_px else 0.0
    if peak_gain < effective_spike_top_gain_pct(cfg) * 0.5:
        return False, ""

    vol_recent = float(df["volume"].tail(3).mean())
    vol_avg = float(df["volume"].tail(20).mean())
    vol_r = vol_recent / (vol_avg + 1e-9)
    fade_thr = effective_spike_top_vol_ratio(cfg) * 0.85
    giveback = (peak_px - current_px) / entry_px if peak_px > entry_px else 0.0

    if vol_r < fade_thr and giveback >= peak_gain * 0.25 and pnl_pct > 0:
        return True, (
            f"wave_end_spike_fade: peak +{peak_gain:.2%} now +{pnl_pct:.2%} "
            f"vol={vol_r:.1f}x fading"
        )
    return False, ""


def check_missed_profit_hunt(
    cfg: BotConfig,
    state: Dict[str, Any],
    current_px: float,
    entry_px: float,
    ticker: str,
) -> Optional[Dict[str, Any]]:
    """
    If we saw a spike top in profit but held, and price gave back — record learning signal.
    state keys: spike_peak, spike_seen_at, spike_ctx
    """
    spike_peak = float(state.get("spike_peak", 0) or 0)
    seen_at = float(state.get("spike_seen_at", 0) or 0)
    if spike_peak <= 0 or entry_px <= 0 or seen_at <= 0:
        return None
    if time.time() - seen_at > 600:
        return None

    peak_gain = (spike_peak - entry_px) / entry_px
    if peak_gain < effective_profit_hunt_min_pnl(cfg):
        return None

    cur_gain = (current_px - entry_px) / entry_px
    giveback = peak_gain - cur_gain
    if giveback < peak_gain * 0.35:
        return None
    if cur_gain < effective_profit_hunt_min_pnl(cfg):
        return None

    left_on_table = (spike_peak - current_px) * float(state.get("shares", 0) or 0)
    return {
        "ticker": ticker,
        "entry": entry_px,
        "spike_peak": spike_peak,
        "current_px": current_px,
        "peak_gain_pct": round(peak_gain * 100, 3),
        "current_gain_pct": round(cur_gain * 100, 3),
        "left_on_table_usd": round(left_on_table, 2),
        "spike_ctx": state.get("spike_ctx", {}),
    }


def record_profit_hunt_learning(
    cfg: BotConfig,
    *,
    event: str,
    ticker: str,
    context: Dict[str, Any],
    pnl_usd: float = 0.0,
    won: bool = False,
    skip_ledger: bool = False,
) -> None:
    """Write profit-hunt outcomes to experience buffer for PPO / council."""
    if not skip_ledger and getattr(cfg, "PROFIT_HUNT_TRACK_ALL", True):
        _append_ledger({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "ticker": ticker,
            "pnl_usd": round(pnl_usd, 2),
            "source": "buffer",
            **{k: v for k, v in context.items() if k != "reason"},
            "reason": str(context.get("reason", event))[:300],
        })
    try:
        from core.experience_buffer import append as buffer_append
        from core.reward_shaping import reward_from_profit_hunt

        reward = reward_from_profit_hunt(cfg, event=event, pnl_usd=pnl_usd, context=context)
        buffer_append({
            "source": "profit_hunt",
            "action": event.upper(),
            "ticker": ticker,
            "reason": str(context.get("reason", event))[:300],
            "reward": reward,
            "win": won or pnl_usd > 0,
            "pnl_usd": round(pnl_usd, 2),
            "spike_ratio": float(context.get("vol_ratio", 1.0) or 1.0),
            "gain_pct": float(context.get("gain_pct", 0) or 0),
            "event": event,
            "primary_goal": is_profit_hunt_primary(cfg),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in context.items() if k not in ("reason",)},
        })
    except Exception as exc:
        log.debug(f"Profit hunt learning record: {exc}")


def teach_profit_hunt_lesson(
    autopilot: Any,
    consciousness: Any,
    lesson: str,
) -> None:
    """Push lesson into cognitive state for future Ollama prompts."""
    if not lesson:
        return
    try:
        if autopilot and getattr(autopilot, "core", None):
            core = autopilot.core
            if lesson not in core.state.learned_lessons:
                core.state.learned_lessons.append(lesson)
                if len(core.state.learned_lessons) > 100:
                    core.state.learned_lessons = core.state.learned_lessons[-100:]
    except Exception:
        pass
    try:
        if consciousness and hasattr(consciousness, "_write_thought"):
            consciousness._write_thought("PROFIT_HUNT", lesson, {})
    except Exception:
        pass


def profit_hunt_prompt_block(cfg: BotConfig) -> str:
    """Compact doctrine + current tunable thresholds for AI prompts."""
    ledger_n = 0
    try:
        if LEDGER_PATH.exists():
            ledger_n = sum(1 for _ in open(LEDGER_PATH, encoding="utf-8"))
    except Exception:
        pass
    return (
        f"{PROFIT_HUNT_DOCTRINE}\n"
        f"Hunt ledger: {ledger_n} events tracked | "
        f"full_freedom={profit_hunt_full_freedom(cfg)} | "
        f"params: GAIN={_f(cfg, 'SPIKE_TOP_MIN_GAIN_PCT', 0.005):.3%} "
        f"VOL={_f(cfg, 'SPIKE_TOP_MIN_VOL_RATIO', 1.15):.2f} "
        f"MIN_PNL={effective_profit_hunt_min_pnl(cfg):.3%} "
        f"EXT_GIVEBACK={effective_extended_giveback(cfg):.0%}"
    )
