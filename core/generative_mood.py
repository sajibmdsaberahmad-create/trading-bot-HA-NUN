#!/usr/bin/env python3
"""
core/generative_mood.py — AI-derived mood state (not fixed labels).

When GENERATIVE_MOOD_ENABLED, Ollama assesses recent telemetry and returns a
free-form mood label plus narrative. Falls back to telemetry-only text if LLM
is unavailable — never uses the legacy fixed MOODS dictionary.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

_last_call: Dict[str, float] = {}
_CACHE: Dict[str, Tuple[float, str, str]] = {}


def _telemetry_summary(
    *,
    recent_pnls: List[float],
    consecutive_wins: int = 0,
    consecutive_losses: int = 0,
    total_pnl: float = 0.0,
    trades_observed: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    recent = recent_pnls[-20:] if recent_pnls else []
    wins = sum(1 for p in recent if p > 0)
    wr = wins / len(recent) if recent else 0.5
    return {
        "recent_trade_count": len(recent),
        "win_rate_recent": round(wr, 3),
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
        "session_pnl_usd": round(sum(recent), 2),
        "total_pnl_usd": round(float(total_pnl), 2),
        "trades_observed": trades_observed,
        "last_5_pnls": [round(float(p), 2) for p in recent[-5:]],
        **(extra or {}),
    }


def _fallback_mood(telemetry: Dict[str, Any]) -> Tuple[str, str]:
    """Telemetry-only narrative when Ollama is offline."""
    wr = float(telemetry.get("win_rate_recent", 0.5))
    streak_l = int(telemetry.get("consecutive_losses", 0))
    streak_w = int(telemetry.get("consecutive_wins", 0))
    pnl = float(telemetry.get("session_pnl_usd", 0))

    if telemetry.get("recent_trade_count", 0) < 3:
        return (
            "gathering",
            "Still calibrating — not enough post-session data for a firm read yet.",
        )
    if streak_l >= 4:
        return (
            "recalibrating",
            f"Four losses in a row (${pnl:+.2f} recent). Tightening entries until edge returns.",
        )
    if streak_w >= 4 and pnl > 0:
        return (
            "in rhythm",
            f"Win streak {streak_w} · recent ${pnl:+.2f}. Riding what works, not forcing size.",
        )
    if wr >= 0.6 and pnl > 0:
        return ("focused", f"Win rate {wr:.0%} on recent trades · ${pnl:+.2f}. Executing the plan.")
    if wr < 0.35:
        return ("defensive", f"Win rate {wr:.0%} · ${pnl:+.2f}. Preserving capital over action.")
    return ("steady", f"Mixed session · ${pnl:+.2f}. Watching for cleaner setups.")


def assess_mood(
    cfg: BotConfig,
    *,
    recent_pnls: List[float],
    consecutive_wins: int = 0,
    consecutive_losses: int = 0,
    total_pnl: float = 0.0,
    trades_observed: int = 0,
    think_fn: Optional[Callable[[str], str]] = None,
    extra: Optional[Dict[str, Any]] = None,
    cache_key: str = "default",
) -> Tuple[str, str]:
    """
    Return (mood_label, mood_message). Label is free-form, not from a fixed enum.
    """
    if not getattr(cfg, "GENERATIVE_MOOD_ENABLED", True):
        return _fallback_mood(
            _telemetry_summary(
                recent_pnls=recent_pnls,
                consecutive_wins=consecutive_wins,
                consecutive_losses=consecutive_losses,
                total_pnl=total_pnl,
                trades_observed=trades_observed,
                extra=extra,
            )
        )

    min_gap = float(getattr(cfg, "GENERATIVE_MOOD_MIN_SEC", 45.0))
    now = time.time()
    if now - _last_call.get(cache_key, 0) < min_gap:
        cached = _CACHE.get(cache_key)
        if cached:
            return cached[1], cached[2]

    telemetry = _telemetry_summary(
        recent_pnls=recent_pnls,
        consecutive_wins=consecutive_wins,
        consecutive_losses=consecutive_losses,
        total_pnl=total_pnl,
        trades_observed=trades_observed,
        extra=extra,
    )

    mood, message = _fallback_mood(telemetry)
    from core.council_budget import is_market_hours_active
    mood_api = getattr(cfg, "COUNCIL_MOOD_API_ENABLED", False)
    if think_fn and mood_api and not is_market_hours_active(cfg):
        prompt = (
            "You are HANOON trading pilot AI assessing your own mental state from telemetry.\n"
            "Do NOT use canned labels like euphoric/confident/anxious unless they truly fit.\n"
            "Invent a short mood label (1-3 words, lowercase) and one sentence explaining it.\n"
            f"TELEMETRY:\n{json.dumps(telemetry, default=str)}\n\n"
            'Respond ONLY JSON: {"mood": "...", "message": "..."}'
        )
        try:
            raw = (think_fn(prompt) or "").strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            parsed = json.loads(raw)
            if parsed.get("mood") and parsed.get("message"):
                mood = str(parsed["mood"]).strip()[:40]
                message = str(parsed["message"]).strip()[:240]
        except Exception as exc:
            log.debug(f"Generative mood: {exc}")

    _last_call[cache_key] = now
    _CACHE[cache_key] = (now, mood, message)
    return mood, message
