#!/usr/bin/env python3
"""
core/experience_buffer.py — Unified experience buffer for AI learning.

Stores every meaningful event from backtests, live trading, scans,
and daily sessions. This is the single source of truth for training.

Schema per record:
- source: backtest | live_trade | scan_pick | daily | finetune
- timestamp
- ticker, regime
- features: list[float]
- action: BUY | SELL | HOLD | SCAN_PICK
- confidence: float
- scan_score: float
- entry_price, stop_dist, tp_dist
- exit_price, exit_reason
- reward, pnl_usd, win, bars_held
- model_version: str
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

MODELS_DIR = Path("models")
BUFFER_PATH = MODELS_DIR / "experience_buffer.jsonl"
MODELS_DIR.mkdir(exist_ok=True)

_lock = threading.Lock()


def append(record: Dict[str, Any]) -> None:
    """Append one experience record to the buffer."""
    record.setdefault("timestamp", datetime.utcnow().isoformat())
    record.setdefault("model_version", "scalper_v1")
    line = json.dumps(record, separators=(",", ":"))
    with _lock:
        with open(BUFFER_PATH, "a") as f:
            f.write(line + "\n")


def load_all() -> list:
    """Load all records from the buffer."""
    if not BUFFER_PATH.exists():
        return []
    records = []
    with open(BUFFER_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def load_recent(n: int = 1000) -> list:
    """Return the last n records."""
    all_recs = load_all()
    return all_recs[-n:]


def count() -> int:
    if not BUFFER_PATH.exists():
        return 0
    with open(BUFFER_PATH, "r") as f:
        return sum(1 for _ in f)


def clear() -> None:
    with _lock:
        if BUFFER_PATH.exists():
            BUFFER_PATH.unlink()


def tail(n: int = 50) -> list:
    return load_recent(n)


def stats() -> Dict[str, Any]:
    recs = load_all()
    if not recs:
        return {"total": 0}
    sources = {}
    wins = 0
    total_reward = 0.0
    for r in recs:
        src = r.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        if r.get("win"):
            wins += 1
        total_reward += float(r.get("reward", 0.0))
    return {
        "total": len(recs),
        "sources": sources,
        "win_rate": wins / len(recs) if recs else 0.0,
        "avg_reward": total_reward / len(recs) if recs else 0.0,
    }