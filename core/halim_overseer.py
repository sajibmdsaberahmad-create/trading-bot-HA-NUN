#!/usr/bin/env python3
"""
core/halim_overseer.py — Halim as silent overseer.

Halim observes every system event, builds periodic digests, and produces
advisory observations. NEVER blocks, vetoes, or skips anything.

Design:
  - EventDigest collects rolling events (spikes, vetoes, entries, exits, etc.)
  - Every OVERSEER_INTERVAL_SEC (default 60s), builds a compact digest text
  - Fires Halim LM async with the digest
  - Halim returns JSON: observations, suggestions, pattern detection
  - Observations logged to training gold for PPO learning
  - Zero impact on trading performance — pure async advisory
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

OVERSEER_LOG = Path("halim/data/overseer/observations.jsonl")


def overseer_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("OVERSEER_ENABLED", "true").lower() in ("1", "true", "yes")


def overseer_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    return float(os.getenv("OVERSEER_INTERVAL_SEC", "60"))


def overseer_max_events(cfg: Optional[BotConfig] = None) -> int:
    return int(os.getenv("OVERSEER_MAX_EVENTS", "200"))


class EventDigest:
    """Thread-safe rolling event collector — lightweight, bounded."""

    def __init__(self, maxlen: int = 200):
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=maxlen)
        self._maxlen = maxlen

    def record(self, category: str, detail: str, meta: Optional[Dict] = None) -> None:
        """Thread-safe record an event."""
        with self._lock:
            self._events.append({
                "t": time.time(),
                "c": category[:20],
                "d": detail[:120],
                "m": meta or {},
            })

    def consume(self) -> List[Dict[str, Any]]:
        """Atomically drain all events and return them."""
        with self._lock:
            out = list(self._events)
            self._events.clear()
            return out


def _summarize_spikes(events: List[Dict]) -> str:
    spikes = [e for e in events if e["c"] == "spike"]
    strong = [e for e in spikes if e["m"].get("score", 0) >= 30 and e["m"].get("ratio", 0) >= 1.3]
    vetoed_green = [e for e in events if e["c"] == "green_veto"]
    vetoed_profit = [e for e in events if e["c"] == "profit_veto"]
    entries = [e for e in events if e["c"] == "entry"]
    exits = [e for e in events if e["c"] == "exit"]
    ppo_holds = [e for e in events if e["c"] == "ppo_hold"]
    ppo_buys = [e for e in events if e["c"] == "ppo_buy"]
    halim_ok = [e for e in events if e["c"] == "halim_ok"]
    halim_empty = [e for e in events if e["c"] == "halim_empty"]
    return (
        f"Spikes:{len(spikes)} strong:{len(strong)} veto-green:{len(vetoed_green)} "
        f"veto-profit:{len(vetoed_profit)} entries:{len(entries)} exits:{len(exits)} "
        f"PPO:hold={len(ppo_holds)} buy={len(ppo_buys)} "
        f"Halim:ok={len(halim_ok)} empty={len(halim_empty)}"
    )


def _summarize_positions(events: List[Dict]) -> str:
    fills = [e for e in events if e["c"] == "fill"]
    rejects = [e for e in events if e["c"] == "reject"]
    pnl_changes = [e for e in events if e["c"] == "pnl"]
    if not fills and not rejects and not pnl_changes:
        return "no position activity"
    out = []
    if fills:
        out.append(f"fills={len(fills)}")
        for f in fills[:5]:
            out.append(f['d'][:40])
    if rejects:
        out.append(f"rejects={len(rejects)}")
    if pnl_changes:
        latest = pnl_changes[-1].get("m", {})
        out.append(f"pnl=${latest.get('pnl', 0):+.2f}")
    return " | ".join(out)


def build_digest(events: List[Dict[str, Any]], tickers: int) -> str:
    """Compact system digest for Halim LM — never exceeds ~800 chars."""
    spike_line = _summarize_spikes(events)
    pos_line = _summarize_positions(events)
    lines = [
        f"SYSTEM DIGEST ({len(events)} events, {tickers} tickers):",
        spike_line,
        pos_line,
    ]
    # Add recent event details for pattern detection
    detail_count = 0
    for e in events[-20:]:  # last 20 events in detail
        if e["c"] in ("green_veto", "profit_veto", "entry", "exit", "halim_empty"):
            ticker = e["m"].get("ticker", "?")
            reason = e["d"][:60]
            lines.append(f"  {e['c']} {ticker}: {reason}")
            detail_count += 1
            if detail_count >= 8:
                break
    return "\n".join(lines)


def _append_gold(digest: str, observations: Dict[str, Any]) -> None:
    """Log overseer observation to training gold."""
    try:
        OVERSEER_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "digest": digest[:1000],
            "observations": observations,
        }
        with open(OVERSEER_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"Overseer gold: {exc}")


def _parse_observation(raw: str) -> Dict[str, Any]:
    """Parse Halim's JSON observation."""
    if not raw:
        return {"ok": False, "error": "empty"}
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {"ok": False, "error": "parse_failed"}


_OVERSEER_TASK = (
    "You are M. A. Halim — the overseer of HANOON trading bot. "
    "You observe every system event. NEVER give trade instructions. NEVER suggest entry/exit. "
    "Your role: detect patterns, surface trends, advise the pilot.\n\n"
    "Read the SYSTEM DIGEST below. Respond with JSON only:\n"
    "{\n"
    '  "observation": "<1-2 sentence summary of what you notice>",\n'
    '  "pattern_detected": "<any recurring pattern, or 'none'>",\n'
    '  "suggestion": "<advisory only — what the pilot might consider, or 'none'>",\n'
    '  "confidence_trend": "<up|down|stable>",\n'
    '  "volatility_observation": "<calm|normal|elevated>",\n'
    '  "watch": "<ANY ticker name that deserves attention, or 'none'>"\n'
    "}\n"
    "Never suggest blocking or skipping any ticker. "
    "Do not include markdown or explanation outside JSON."
)


def run_overseer_digest(
    digest_events: List[Dict[str, Any]],
    tickers: int,
    *,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
) -> None:
    """Build and fire a digest — runs async, never blocks."""
    if not overseer_enabled(cfg):
        return
    if not digest_events:
        return

    try:
        digest = build_digest(digest_events, tickers)
    except Exception as exc:
        log.debug(f"Overseer digest build: {exc}")
        return

    if len(digest) < 30:
        return

    def _run() -> None:
        """Fire digest to Halim — async, no blocking."""
        prompt = f"{_OVERSEER_TASK}\n\n{SYSTEM_DIGEST}\n\n{digest}"
        try:
            from halim.client import complete
            raw = complete(prompt, purpose="overseer", timeout=90.0)
            if raw and raw.get("ok") and raw.get("text"):
                parsed = _parse_observation(raw["text"])
                if parsed.get("ok") is not False:
                    _append_gold(digest, parsed)
                    obs = parsed.get("observation", "")[:120]
                    pat = parsed.get("pattern_detected", "none")[:60]
                    sug = parsed.get("suggestion", "none")[:80]
                    if pat != "none" or sug != "none":
                        log.info(
                            f"👁 Halim observe: {obs} | pattern={pat} | "
                            f"suggest={sug}"
                        )
                    return
            log.debug(f"Overseer: no response")
        except Exception as exc:
            log.debug(f"Overseer: {exc}")

    threading.Thread(target=_run, name="halim-overseer", daemon=True).start()


# Singleton shared across the system
_event_digest: Optional[EventDigest] = None
_digest_lock = threading.Lock()


def get_digest(maxlen: Optional[int] = None) -> EventDigest:
    """Get or create the singleton event digest."""
    global _event_digest
    if _event_digest is None:
        ml = maxlen or 200
        with _digest_lock:
            if _event_digest is None:
                _event_digest = EventDigest(maxlen=ml)
    return _event_digest


def record_event(category: str, detail: str, meta: Optional[Dict] = None) -> None:
    """Convenience: record an event on the singleton digest."""
    try:
        get_digest().record(category, detail, meta)
    except Exception:
        pass


SYSTEM_DIGEST = "SYSTEM DIGEST:"  # marker for prompt template
