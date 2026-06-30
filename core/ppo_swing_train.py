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
    """Aggregate swing shadow verdicts into a simple bias policy file."""
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
