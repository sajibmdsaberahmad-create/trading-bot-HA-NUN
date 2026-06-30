#!/usr/bin/env python3
"""
core/swing_train.py — Train swing policy from IB trips + analysis features + web learn.

Updates models/swing_policy.json thresholds used by swing_intel scoring.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
POLICY_PATH = MODELS_DIR / "swing_policy.json"
ANALYSIS_LOG = MODELS_DIR / "swing_analysis_log.jsonl"


def _read_jsonl(path: Path, limit: int = 5000) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:
        pass
    return rows


def train_swing_policy(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """
    Learn from closed IB swing trips + logged analyses.
    Adjusts min_score / min_strength recommendations per regime.
    """
    from core.swing_learning import read_swing_trips

    trips = read_swing_trips()
    analyses = _read_jsonl(ANALYSIS_LOG)
    web_rows = _read_jsonl(MODELS_DIR / "swing_web_learn.jsonl")

    if len(trips) < 1 and len(analyses) < int(os.getenv("SWING_TRAIN_MIN_ANALYSES", "20")):
        return {"ok": False, "reason": "insufficient_data", "trips": len(trips), "analyses": len(analyses)}

    wins = [t for t in trips if float(t.get("pnl_usd", 0) or 0) > 0]
    losses = [t for t in trips if float(t.get("pnl_usd", 0) or 0) <= 0]
    multi_day_wins = [t for t in wins if t.get("multi_day")]

    win_scores: List[float] = []
    loss_scores: List[float] = []
    sym_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0})

    for t in trips:
        sym = str(t.get("symbol", "")).upper()
        if float(t.get("pnl_usd", 0) or 0) > 0:
            sym_stats[sym]["wins"] += 1
        else:
            sym_stats[sym]["losses"] += 1

    for row in analyses:
        v = row.get("verdict") or {}
        sc = float(v.get("score", 0) or 0)
        sym = str(row.get("symbol", "")).upper()
        matched = any(str(t.get("symbol", "")).upper() == sym for t in trips)
        if not matched:
            continue
        if any(float(t.get("pnl_usd", 0) or 0) > 0 for t in trips if str(t.get("symbol")) == sym):
            win_scores.append(sc)
        else:
            loss_scores.append(sc)

    min_score = float(os.getenv("SWING_INTEL_MIN_SCORE", "28"))
    if win_scores and loss_scores:
        avg_win = sum(win_scores) / len(win_scores)
        avg_loss = sum(loss_scores) / len(loss_scores)
        min_score = max(20.0, min(45.0, (avg_win + avg_loss) / 2.0))

    policy = {
        "version": 1,
        "horizon": "swing",
        "trained_from": {
            "ib_trips": len(trips),
            "analyses": len(analyses),
            "web_pages": len(web_rows),
        },
        "win_rate_pct": round(len(wins) / max(len(trips), 1) * 100, 1),
        "multi_day_win_rate_pct": round(len(multi_day_wins) / max(len(wins), 1) * 100, 1),
        "avg_hold_days": round(
            sum(float(t.get("hold_days", 0) or 0) for t in trips) / max(len(trips), 1), 2,
        ),
        "min_score_recommended": round(min_score, 2),
        "min_strength_recommended": float(os.getenv("SWING_IB_MIN_STRENGTH", "0.35")),
        "symbol_stats": dict(sym_stats),
        "feature_hints": {
            "prefer_tf_aligned_long_gte": 2,
            "penalize_high_atr_above": 8.0,
            "boost_macro_favorable": True,
            "use_web_sentiment": True,
        },
    }

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    POLICY_PATH.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    log.info(
        f"  🎓 Swing policy trained → {POLICY_PATH.name} "
        f"({len(trips)} trips, min_score={min_score:.1f})"
    )

    try:
        from core.ppo_swing_train import train_ppo_swing_from_shadow
        ppo = train_ppo_swing_from_shadow(cfg)
        policy["ppo"] = ppo
        POLICY_PATH.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    except Exception as exc:
        log.debug(f"swing ppo merge: {exc}")

    return {"ok": True, "path": str(POLICY_PATH), **policy}


def load_swing_policy() -> Dict[str, Any]:
    try:
        if POLICY_PATH.is_file():
            return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def apply_policy_to_min_score() -> float:
    """Runtime threshold from trained policy."""
    pol = load_swing_policy()
    if pol.get("min_score_recommended"):
        return float(pol["min_score_recommended"])
    return float(os.getenv("SWING_INTEL_MIN_SCORE", "28"))
