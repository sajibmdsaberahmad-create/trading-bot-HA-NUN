#!/usr/bin/env python3
"""
core/ppo_swing_train.py — Off-hours PPO weights from swing shadow verdicts.

Writes models/ppo_swing_1h.json (lightweight policy snapshot, not full RL).
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
WEIGHTS_PATH = MODELS_DIR / "ppo_swing_1h.json"
VERDICT_PATH = MODELS_DIR / "swing_shadow_verdicts.jsonl"


def train_ppo_swing_from_shadow(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """Aggregate swing IB trips (preferred) or shadow verdicts into policy file."""
    try:
        from core.swing_learning import read_swing_trips
        trips = read_swing_trips()
        if len(trips) >= int(os.getenv("PPO_SWING_MIN_TRIPS", "5")):
            wins = sum(1 for t in trips if float(t.get("pnl_usd", 0) or 0) > 0)
            multi = sum(1 for t in trips if t.get("multi_day"))
            out = {
                "version": 2,
                "horizon": "swing",
                "source": "ib_trips",
                "trip_rows": len(trips),
                "win_rate_pct": round(wins / max(len(trips), 1) * 100, 1),
                "multi_day_trips": multi,
                "avg_hold_days": round(
                    sum(float(t.get("hold_days", 0) or 0) for t in trips) / len(trips), 2,
                ),
                "symbols": sorted({str(t.get("symbol", "")).upper() for t in trips if t.get("symbol")}),
            }
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            WEIGHTS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
            log.info(f"PPO swing weights (IB trips) → {WEIGHTS_PATH.name} ({len(trips)} trips)")
            return {"ok": True, "path": str(WEIGHTS_PATH), **out}
    except Exception as exc:
        log.debug(f"ppo swing ib trips: {exc}")

    if not VERDICT_PATH.exists():
        return {"ok": False, "reason": "no_verdicts"}
    bias_counts: Counter = Counter()
    symbol_bias: Dict[str, Counter] = {}
    rows = 0
    try:
        for line in VERDICT_PATH.read_text(encoding="utf-8").strip().splitlines():
            row = json.loads(line)
            v = str(row.get("verdict", "hold")).lower()
            sym = str(row.get("symbol", "")).upper()
            bias_counts[v] += 1
            symbol_bias.setdefault(sym, Counter())[v] += 1
            rows += 1
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:80]}

    if rows < int(os.getenv("PPO_SWING_MIN_VERDICTS", "10")):
        return {"ok": False, "reason": "insufficient_verdicts", "rows": rows}

    top_symbols = {
        sym: counts.most_common(1)[0][0]
        for sym, counts in symbol_bias.items()
        if counts
    }
    out = {
        "version": 1,
        "horizon": "swing",
        "bar_size": "1 hour",
        "verdict_rows": rows,
        "global_bias": bias_counts.most_common(1)[0][0] if bias_counts else "hold",
        "bias_distribution": dict(bias_counts),
        "symbol_bias": top_symbols,
        "min_strength": float(os.getenv("SWING_PAPER_MIN_STRENGTH", "0.35")),
    }
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info(f"PPO swing weights → {WEIGHTS_PATH.name} ({rows} verdicts)")
    return {"ok": True, "path": str(WEIGHTS_PATH), **out}
