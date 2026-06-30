#!/usr/bin/env python3
"""
core/swing_intel.py — Full swing analysis: multi-TF technicals, IB fundamentals/news,
macro, and internet-learned context (Halim RAG). PnL always from IB Truth on execution.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.horizon_tags import tag_learning_row
from core.notify import log
from core.trade_horizon import HORIZON_SWING

if TYPE_CHECKING:
    from core.config import BotConfig

ANALYSIS_LOG = Path(__file__).resolve().parent.parent / "models" / "swing_analysis_log.jsonl"
CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL = float(os.getenv("SWING_ANALYSIS_CACHE_SEC", "300"))


def swing_intel_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    return os.getenv("SWING_INTEL_ENABLED", "true").lower() in ("1", "true", "yes")


def _append_analysis(row: Dict[str, Any]) -> None:
    try:
        ANALYSIS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ANALYSIS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"swing analysis log: {exc}")


def _closes_from_bars(bars: List[Any]) -> List[float]:
    out: List[float] = []
    for b in bars or []:
        c = float(getattr(b, "close", 0) or 0)
        if c > 0:
            out.append(c)
    return out


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses <= 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def _atr_pct(bars: List[Any], period: int = 14) -> float:
    if not bars or len(bars) < period + 1:
        return 0.0
    trs: List[float] = []
    for i in range(-period, 0):
        h = float(getattr(bars[i], "high", 0) or getattr(bars[i], "close", 0) or 0)
        l = float(getattr(bars[i], "low", 0) or getattr(bars[i], "close", 0) or 0)
        pc = float(getattr(bars[i - 1], "close", 0) or 0)
        if h > 0 and l > 0:
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    atr = sum(trs) / len(trs)
    px = float(getattr(bars[-1], "close", 0) or 1)
    return (atr / px * 100.0) if px > 0 else 0.0


def _trend_from_closes(closes: List[float], label: str) -> Dict[str, Any]:
    if len(closes) < 10:
        return {"tf": label, "bias": "hold", "strength": 0.0, "reason": "short_history"}
    sma5 = sum(closes[-5:]) / 5.0
    sma10 = sum(closes[-10:]) / 10.0
    sma20 = sum(closes[-20:]) / 20.0 if len(closes) >= 20 else sma10
    px = closes[-1]
    rsi = _rsi(closes)
    if px > sma5 > sma10 > sma20:
        bias, strength = "long", min(1.0, (px / sma20 - 1.0) * 15)
        reason = f"{label}_uptrend"
    elif px < sma5 < sma10 < sma20:
        bias, strength = "short", min(1.0, (sma20 / px - 1.0) * 15)
        reason = f"{label}_downtrend"
    else:
        bias, strength = "hold", 0.15
        reason = f"{label}_range"
    if bias == "long" and rsi > 78:
        strength *= 0.6
        reason += "_overbought"
    if bias == "short" and rsi < 22:
        strength *= 0.6
        reason += "_oversold"
    return {
        "tf": label,
        "bias": bias,
        "strength": round(strength, 3),
        "rsi": round(rsi, 1),
        "px": round(px, 4),
        "reason": reason,
    }


def _fetch_bars(runner: Any, sym: str, bar_size: str, duration: str) -> List[Any]:
    dm = getattr(runner, "data_manager", None)
    if dm is None:
        return []
    try:
        return dm.get_bars(sym, bar_size, duration=duration) or []
    except Exception:
        return []


def analyze_swing_technical(runner: Any, sym: str) -> Dict[str, Any]:
    """Multi-timeframe read: 1h / 4h / 1d."""
    sym = sym.upper()
    bars_1h = _fetch_bars(runner, sym, "1 hour", "10 D")
    bars_4h = _fetch_bars(runner, sym, "4 hours", "30 D")
    bars_1d = _fetch_bars(runner, sym, "1 day", "60 D")
    t1 = _trend_from_closes(_closes_from_bars(bars_1h), "1h")
    t4 = _trend_from_closes(_closes_from_bars(bars_4h), "4h")
    t1d = _trend_from_closes(_closes_from_bars(bars_1d), "1d")
    atr = _atr_pct(bars_1h)
    aligned_long = sum(1 for t in (t1, t4, t1d) if t.get("bias") == "long")
    aligned_short = sum(1 for t in (t1, t4, t1d) if t.get("bias") == "short")
    if aligned_long >= 2:
        bias = "long"
        strength = sum(float(t.get("strength", 0) or 0) for t in (t1, t4, t1d) if t.get("bias") == "long") / max(aligned_long, 1)
    elif aligned_short >= 2:
        bias = "short"
        strength = sum(float(t.get("strength", 0) or 0) for t in (t1, t4, t1d) if t.get("bias") == "short") / max(aligned_short, 1)
    else:
        bias = "hold"
        strength = 0.0
    return {
        "bias": bias,
        "strength": round(min(1.0, strength), 3),
        "atr_pct": round(atr, 2),
        "tf_aligned_long": aligned_long,
        "tf_aligned_short": aligned_short,
        "timeframes": {"1h": t1, "4h": t4, "1d": t1d},
    }


def analyze_swing_ib(sym: str, cfg: Optional["BotConfig"], runner: Any = None) -> Dict[str, Any]:
    """IB fundamentals, news, position mark from hub / extended cache."""
    sym = sym.upper()
    out: Dict[str, Any] = {"symbol": sym}
    try:
        from core.ib_truth import get_snapshot
        snap = get_snapshot()
        pos = snap.long_positions().get(sym)
        if pos:
            out["ib_position"] = {
                "qty": pos.qty,
                "avg_cost": pos.avg_cost,
                "market_price": pos.market_price,
                "unrealized_pnl": pos.unrealized_pnl,
            }
    except Exception:
        pass
    try:
        from core.ib_hub import get_hub_context
        conn = getattr(runner, "conn", None) if runner else None
        hub = get_hub_context(cfg, connector=conn, runner=runner)
        fund = (hub.get("ib_fundamentals") or {}).get(sym) or (hub.get("ib_fundamentals") or {}).get(sym.lower())
        if fund:
            out["fundamentals"] = fund
        headlines = (hub.get("ib_news_headlines") or {}).get(sym) or []
        if headlines:
            out["news_headlines"] = headlines[:5]
        bulletins = hub.get("ib_news_bulletins") or []
        if bulletins:
            out["news_bulletins"] = bulletins[:3]
    except Exception as exc:
        log.debug(f"swing ib context {sym}: {exc}")
    return out


def analyze_swing_macro(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    try:
        from core.market_context import get_macro_context
        ctx = get_macro_context()
        if not ctx:
            return {}
        tone = str(ctx.get("risk_tone", "neutral"))
        swing_ok = tone not in ("high_fear",)
        return {
            "risk_tone": tone,
            "spy_pct": ctx.get("spy_pct", 0),
            "qqq_pct": ctx.get("qqq_pct", 0),
            "vix_level": ctx.get("vix_level", 0),
            "swing_favorable": swing_ok,
            "source": ctx.get("source", ""),
        }
    except Exception:
        return {}


def analyze_swing_web(sym: str, cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """Internet-learned snippets (Halim cache + optional live fetch)."""
    sym = sym.upper()
    query = f"swing trading {sym} multi day trend hold support resistance risk"
    snippets: List[str] = []
    sources: List[str] = []
    try:
        from core.halim_learn_rag import retrieve_learn_context
        hits = retrieve_learn_context(
            query,
            cfg=cfg,
            max_chars=int(os.getenv("SWING_WEB_RAG_CHARS", "800")),
            max_docs=int(os.getenv("SWING_WEB_RAG_DOCS", "2")),
        )
        for h in hits:
            topic = str(h.get("topic", ""))[:60]
            text = str(h.get("excerpt") or h.get("text") or "")[:400]
            if text:
                snippets.append(text)
                sources.append(topic)
    except Exception as exc:
        log.debug(f"swing web rag {sym}: {exc}")
    try:
        from core.swing_web_learn import swing_ticker_web_snippet
        live = swing_ticker_web_snippet(sym, cfg)
        if live.get("ok") and live.get("text"):
            snippets.append(str(live["text"])[:500])
            sources.append(str(live.get("topic", "swing_web")))
    except Exception:
        pass
    sentiment = "neutral"
    blob = " ".join(snippets).lower()
    if any(w in blob for w in ("bullish", "uptrend", "breakout", "accumulation")):
        sentiment = "bullish"
    elif any(w in blob for w in ("bearish", "downtrend", "breakdown", "distribution")):
        sentiment = "bearish"
    return {
        "snippets": snippets,
        "sources": sources,
        "web_sentiment": sentiment,
        "snippet_count": len(snippets),
    }


def _score_analysis(tech: Dict, ib: Dict, macro: Dict, web: Dict) -> Dict[str, Any]:
  """Composite swing score — advisory; execution PnL from IB only."""
    score = 0.0
    reasons: List[str] = []
    bias = str(tech.get("bias", "hold"))
    strength = float(tech.get("strength", 0) or 0)

    if bias == "long":
        score += strength * 40
        reasons.append(tech.get("timeframes", {}).get("1h", {}).get("reason", "tech_long"))
    elif bias == "short":
        score -= strength * 25
        reasons.append("tech_short_skip_long_swing")

    if macro.get("swing_favorable") is True:
        score += 12
        reasons.append(f"macro_{macro.get('risk_tone', 'ok')}")
    elif macro.get("swing_favorable") is False:
        score -= 15
        reasons.append("macro_high_fear")

    web_sent = web.get("web_sentiment", "neutral")
    if web_sent == "bullish":
        score += 8
        reasons.append("web_bullish")
    elif web_sent == "bearish":
        score -= 10
        reasons.append("web_bearish")

    if ib.get("fundamentals"):
        reasons.append("ib_fundamentals_present")
        score += 5

    if ib.get("news_headlines"):
        reasons.append("ib_news_present")
        score += 3

    atr = float(tech.get("atr_pct", 0) or 0)
    if atr > 8:
        score -= 8
        reasons.append("high_atr_chop")
    elif 1.5 <= atr <= 5:
        score += 4
        reasons.append("healthy_atr")

    confidence = min(1.0, max(0.0, (score + 20) / 80.0))
    enter = bias == "long" and score >= float(os.getenv("SWING_INTEL_MIN_SCORE", "28"))
    return {
        "score": round(score, 2),
        "confidence": round(confidence, 3),
        "enter": enter,
        "bias": "long" if enter else ("hold" if bias != "short" else "short"),
        "strength": round(strength if enter else strength * 0.5, 3),
        "reasons": reasons[:12],
    }


def analyze_swing(
    runner: Any,
    cfg: Optional["BotConfig"],
    sym: str,
    *,
    log_row: bool = True,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Full swing understanding bundle for one symbol."""
    if not swing_intel_enabled(cfg):
        from core.swing_shadow import _simple_swing_signal
        bars = _fetch_bars(runner, sym.upper(), "1 hour", "5 D")
        sig = _simple_swing_signal(bars)
        return {"symbol": sym.upper(), "technical": sig, "legacy": True, **sig}

    sym = sym.upper()
    now = time.time()
    if use_cache and sym in _CACHE:
        ts, cached = _CACHE[sym]
        if now - ts < _CACHE_TTL:
            return cached

    tech = analyze_swing_technical(runner, sym)
    ib = analyze_swing_ib(sym, cfg, runner)
    macro = analyze_swing_macro(cfg)
    web = analyze_swing_web(sym, cfg)
    verdict = _score_analysis(tech, ib, macro, web)

    try:
        from core.capital_phase import capital_phase
        phase = capital_phase(cfg, runner)
    except Exception:
        phase = ""

    row = tag_learning_row(
        {
            "event": "swing_analysis",
            "symbol": sym,
            "ts": now,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "technical": tech,
            "ib": {k: v for k, v in ib.items() if k != "fundamentals" or len(str(v)) < 2000},
            "macro": macro,
            "web": {"web_sentiment": web.get("web_sentiment"), "sources": web.get("sources", [])},
            "verdict": verdict,
        },
        horizon=HORIZON_SWING,
        capital_phase=phase,
    )
    if ib.get("fundamentals"):
        row["has_fundamentals"] = True
    if log_row:
        _append_analysis(row)

    out = {
        "symbol": sym,
        "technical": tech,
        "ib": ib,
        "macro": macro,
        "web": web,
        "verdict": verdict,
        "bias": verdict.get("bias", "hold"),
        "strength": verdict.get("strength", 0),
        "confidence": verdict.get("confidence", 0),
        "enter": verdict.get("enter", False),
        "reason": ";".join(verdict.get("reasons", [])[:4]) or "swing_intel",
        "capital_phase": phase,
    }
    _CACHE[sym] = (now, out)
    return out


def swing_intel_line(analysis: Dict[str, Any]) -> str:
    """One-liner for logs / Halim context."""
    sym = analysis.get("symbol", "?")
    v = analysis.get("verdict") or analysis
    return (
        f"SWING INTEL {sym}: {v.get('bias', '?')} conf={v.get('confidence', 0):.2f} "
        f"score={v.get('score', 0):.1f} | {analysis.get('reason', '')[:80]}"
    )
