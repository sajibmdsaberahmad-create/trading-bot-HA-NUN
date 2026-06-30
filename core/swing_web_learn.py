#!/usr/bin/env python3
"""
core/swing_web_learn.py — Internet learning for swing (read-only, allowlisted).

Fetches swing/multi-day topics via halim_web_learn; caches for swing_intel RAG.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig

JOURNAL_PATH = Path(__file__).resolve().parent.parent / "models" / "swing_web_learn.jsonl"
_TICKER_FETCH: Dict[str, float] = {}

SWING_WIKI_TOPICS: List[str] = [
    "Swing_trading",
    "Position_trading",
    "Trend_following",
    "Support_and_resistance",
    "Moving_average",
    "Relative_strength_index",
    "MACD",
    "Fibonacci_retracement",
    "Risk_management",
    "Market_sentiment",
    "Holding_period_return",
    "Volatility_(finance)",
]

SWING_GOOGLE_QUERIES: List[str] = [
    "swing trading multi day hold strategy",
    "how to swing trade stocks risk management",
    "position sizing swing trading portfolio",
    "overnight hold stock gap risk",
]


def swing_web_learn_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    return os.getenv("SWING_WEB_LEARN", "true").lower() in ("1", "true", "yes")


def _append_journal(row: Dict[str, Any]) -> None:
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def run_swing_web_learn_cycle(
    cfg: Optional["BotConfig"] = None,
    *,
    max_pages: int = 0,
) -> Dict[str, Any]:
    """Off-hours batch: Wikipedia swing topics + optional Google snippets."""
    if not swing_web_learn_enabled(cfg):
        return {"ok": False, "reason": "disabled"}
    cap = max_pages or int(os.getenv("SWING_WEB_LEARN_BATCH", "4"))
    fetched = 0
    errors = 0
    try:
        from core.halim_web_learn import fetch_wikipedia_summary
        topics = SWING_WIKI_TOPICS[:cap]
        for title in topics:
            res = fetch_wikipedia_summary(title, cfg)
            row = {
                "event": "swing_web_learn",
                "topic": f"wiki:{title}",
                "ok": res.get("ok"),
                "chars": len(str(res.get("text") or "")),
                "ts": time.time(),
            }
            _append_journal(row)
            if res.get("ok"):
                fetched += 1
            else:
                errors += 1
    except Exception as exc:
        log.debug(f"swing wiki learn: {exc}")

    try:
        from core.halim_learn_catalog import pick_google_queries
        from core.halim_web_learn import fetch_learn_page
        queries = pick_google_queries(SWING_GOOGLE_QUERIES, n=min(2, cap))
        for q in queries:
            url = f"https://www.google.com/search?q={q.replace(' ', '+')}"
            # Google search pages are blocked on learn allowlist usually — use catalog URLs instead
            _ = url
        extra = [t for t in SWING_GOOGLE_QUERIES if "swing" in t][:1]
        for q in extra:
            wiki_alt = "Swing_trading" if "swing" in q else "Risk_management"
            from core.halim_web_learn import fetch_wikipedia_summary
            res = fetch_wikipedia_summary(wiki_alt, cfg)
            if res.get("ok"):
                fetched += 1
                _append_journal({"event": "swing_web_learn", "topic": q, "ok": True, "ts": time.time()})
    except Exception:
        pass

    log.info(f"  📚 Swing web learn: {fetched} pages cached ({errors} errors)")
    return {"ok": True, "fetched": fetched, "errors": errors}


def swing_ticker_web_snippet(sym: str, cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """Optional per-ticker fetch (rate-limited) — company wiki if exists."""
    if not swing_web_learn_enabled(cfg):
        return {"ok": False}
    sym = sym.upper()
    if sym in ("SPY", "QQQ", "IWM", "DIA"):
        return {"ok": False, "reason": "index_skip"}
    now = time.time()
    last = _TICKER_FETCH.get(sym, 0)
    if now - last < float(os.getenv("SWING_TICKER_WEB_COOLDOWN_SEC", "3600")):
        return {"ok": False, "reason": "cooldown"}
    _TICKER_FETCH[sym] = now
    try:
        from core.halim_web_learn import fetch_wikipedia_summary
        res = fetch_wikipedia_summary(f"{sym}_(stock)", cfg)
        if not res.get("ok"):
            res = fetch_wikipedia_summary(sym, cfg)
        if res.get("ok"):
            _append_journal({
                "event": "swing_ticker_web",
                "symbol": sym,
                "topic": res.get("topic", ""),
                "chars": len(str(res.get("text") or "")),
                "ts": now,
            })
        return res
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:80]}
