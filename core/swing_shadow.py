"""
Swing shadow scan — 1h bars, IB marks, log-only verdicts (no orders).

Runs off-hours or when market closed; feeds teacher curriculum with horizon=swing.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.notify import log
from core.ib_truth import get_snapshot, refresh
from core.trade_horizon import HORIZON_SWING, swing_shadow_enabled, tag_record

if TYPE_CHECKING:
    from core.config import BotConfig

VERDICT_PATH = Path(__file__).resolve().parent.parent / "models" / "swing_shadow_verdicts.jsonl"
SCAN_INTERVAL_SEC = float(os.getenv("SWING_SHADOW_INTERVAL_SEC", "900"))


def _symbols_for_scan(runner: Any, cfg: Optional["BotConfig"]) -> List[str]:
    syms: List[str] = []
    snap = get_snapshot()
    for p in snap.positions:
        if p.symbol and p.symbol not in syms:
            syms.append(p.symbol)
    locked = getattr(runner, "locked_targets", None) or []
    for t in locked[:8]:
        s = str(t).upper()
        if s and s not in syms:
            syms.append(s)
    if not syms:
        syms = ["SPY", "QQQ"]
    return syms[: int(os.getenv("SWING_SHADOW_MAX_SYMBOLS", "6"))]


def _simple_swing_signal(bars: List[Any]) -> Dict[str, Any]:
    """Lightweight 1h trend read — signals only; PnL always from IB."""
    if not bars or len(bars) < 20:
        return {"bias": "hold", "strength": 0.0, "reason": "insufficient_bars"}
    closes = [float(getattr(b, "close", 0) or 0) for b in bars[-20:]]
    if not closes or closes[-1] <= 0:
        return {"bias": "hold", "strength": 0.0, "reason": "bad_closes"}
    sma10 = sum(closes[-10:]) / 10.0
    sma20 = sum(closes) / 20.0
    px = closes[-1]
    if px > sma10 > sma20:
        return {"bias": "long", "strength": min(1.0, (px - sma20) / sma20 * 10), "reason": "uptrend_1h"}
    if px < sma10 < sma20:
        return {"bias": "short", "strength": min(1.0, (sma20 - px) / sma20 * 10), "reason": "downtrend_1h"}
    return {"bias": "hold", "strength": 0.0, "reason": "range_1h"}


def run_swing_shadow_scan(
    runner: Any,
    cfg: Optional["BotConfig"] = None,
    *,
    force: bool = False,
) -> int:
    """Return count of verdicts logged."""
    if not swing_shadow_enabled(cfg):
        return 0
    now = time.time()
    last = float(getattr(runner, "_last_swing_shadow", 0) or 0)
    if not force and now - last < SCAN_INTERVAL_SEC:
        return 0
    runner._last_swing_shadow = now

    ib = getattr(getattr(runner, "connector", None), "ib", None)
    if ib is not None:
        try:
            refresh(ib, cfg)
        except Exception as exc:
            log.debug(f"swing shadow ib refresh: {exc}")

    dm = getattr(runner, "data_manager", None)
    if dm is None:
        return 0

    snap = get_snapshot()
    logged = 0
    VERDICT_PATH.parent.mkdir(parents=True, exist_ok=True)

    for sym in _symbols_for_scan(runner, cfg):
        try:
            from core.swing_intel import analyze_swing, swing_intel_line
            analysis = analyze_swing(runner, cfg, sym, log_row=True)
            signal = {
                "bias": analysis.get("bias", "hold"),
                "strength": float(analysis.get("strength", 0) or 0),
                "confidence": float(analysis.get("confidence", 0) or 0),
                "reason": analysis.get("reason", ""),
            }
        except Exception as exc:
            log.debug(f"swing shadow intel {sym}: {exc}")
            try:
                bars = dm.get_bars(sym, "1 hour", duration="5 D")
            except Exception:
                bars = []
            signal = _simple_swing_signal(bars or [])
            analysis = {}
        ib_pos = next((p for p in snap.positions if p.symbol == sym), None)
        row = tag_record(
            {
                "ts": now,
                "symbol": sym,
                "verdict": signal["bias"],
                "strength": round(float(signal.get("strength", 0)), 4),
                "confidence": round(float(signal.get("confidence", 0) or 0), 4),
                "enter": bool(analysis.get("enter")) if analysis else False,
                "reason": signal.get("reason", ""),
                "ib_mark": round(ib_pos.market_price, 4) if ib_pos else 0.0,
                "ib_unrealized": round(ib_pos.unrealized_pnl, 2) if ib_pos else 0.0,
                "ib_qty": ib_pos.qty if ib_pos else 0.0,
                "shadow_only": True,
                "source": "swing_shadow_intel",
                "macro_tone": (analysis.get("macro") or {}).get("risk_tone", ""),
                "web_sentiment": (analysis.get("web") or {}).get("web_sentiment", ""),
            },
            HORIZON_SWING,
        )
        with open(VERDICT_PATH, "a") as f:
            f.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
        logged += 1

    if logged:
        log.info(f"Swing shadow: {logged} verdict(s) → {VERDICT_PATH.name}")
    return logged
