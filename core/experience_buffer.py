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
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.time_utils import utc_now, utc_now_iso, utc_today

MODELS_DIR = Path("models")
BUFFER_PATH = MODELS_DIR / "experience_buffer.jsonl"
MODELS_DIR.mkdir(exist_ok=True)

_lock = threading.Lock()
_stats_cache: Optional[Dict[str, Any]] = None
_stats_cache_ts = 0.0
_line_count: Optional[int] = None


def _max_load_lines() -> int:
    try:
        return max(500, int(os.getenv("EXPERIENCE_BUFFER_MAX_LOAD", "5000")))
    except (TypeError, ValueError):
        return 5000


def _tail_jsonl(path: Path, n: int) -> List[Dict[str, Any]]:
    """Read last n JSONL records without loading the whole file."""
    if not path.exists() or n <= 0:
        return []
    cap = min(n, _max_load_lines())
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = deque(f, maxlen=cap)
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _replay_gold_quality_ok(record: Dict[str, Any]) -> bool:
    """Drop low-edge replay BUY rows when match-live quality filter is on."""
    if record.get("source") != "replay_live":
        return True
    if os.getenv("REPLAY_GOLD_QUALITY_FILTER", "false").lower() not in ("1", "true", "yes"):
        return True
    action = str(record.get("action", "")).upper()
    if action not in ("BUY", "ENTER"):
        return True
    try:
        pp = float(
            record.get("profit_probability", record.get("quality_conf", 0)) or 0,
        )
    except (TypeError, ValueError):
        pp = 0.0
    if pp <= 0:
        return True
    try:
        floor = float(
            os.getenv(
                "REPLAY_GOLD_MIN_PROFIT_PROB",
                os.getenv("MIN_PROFIT_PROBABILITY", "0.58"),
            ),
        )
    except (TypeError, ValueError):
        floor = 0.58
    return pp >= floor


def append(record: Dict[str, Any]) -> None:
    """Append one experience record to the buffer."""
    if not _replay_gold_quality_ok(record):
        return
    record.setdefault("timestamp", utc_now_iso())
    record.setdefault("model_version", "scalper_v1")
    line = json.dumps(record, separators=(",", ":"))
    global _stats_cache, _line_count
    with _lock:
        with open(BUFFER_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        if _line_count is not None:
            _line_count += 1
    if record.get("source") in ("live_trade", "replay_live") and record.get("action") in (
        "SELL", "TRADE", None,
    ):
        try:
            from core.hybrid_distiller import note_closed_trade_for_distill
            note_closed_trade_for_distill()
        except Exception:
            pass
    try:
        from core.learning_coordinator import maybe_trim_experience_buffer
        maybe_trim_experience_buffer()
    except Exception:
        pass
    _stats_cache = None


def load_all(max_records: Optional[int] = None) -> list:
    """Load recent records — never unbounded full-file parse by default."""
    cap = max_records if max_records is not None else _max_load_lines()
    return load_recent(cap)


def load_recent(n: int = 1000) -> list:
    """Return the last n records (tail read — O(tail) not O(file))."""
    cap = min(max(1, int(n)), _max_load_lines())
    return _tail_jsonl(BUFFER_PATH, cap)


def count() -> int:
    """Approximate line count — cached after first scan, incremented on append."""
    global _line_count
    if _line_count is not None:
        return _line_count
    if not BUFFER_PATH.exists():
        _line_count = 0
        return 0
    with _lock:
        if _line_count is not None:
            return _line_count
        try:
            with open(BUFFER_PATH, "r", encoding="utf-8", errors="replace") as f:
                _line_count = sum(1 for _ in f)
        except OSError:
            _line_count = 0
    return _line_count or 0


def clear() -> None:
    global _stats_cache, _line_count
    with _lock:
        if BUFFER_PATH.exists():
            BUFFER_PATH.unlink()
        _line_count = 0
    _stats_cache = None


def tail(n: int = 50) -> list:
    return load_recent(n)


def _is_high_volatility_record(rec: Dict[str, Any]) -> bool:
    try:
        spike = float(rec.get("spike_ratio", 0) or 0)
    except (TypeError, ValueError):
        spike = 0.0
    regime = str(rec.get("regime", "")).lower()
    if spike >= float(os.getenv("PPO_REWARD_HIGH_VOL_SPIKE", "1.45")):
        return True
    if regime in ("volatile", "high_vol", "spike", "trending_volatile"):
        return True
    return False


def sample_balanced_records(
    records: List[Dict[str, Any]],
    *,
    max_records: Optional[int] = None,
    high_vol_fraction: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Mix recent tail with high-volatility examples so calm sessions do not erase
    spike-regime memory from PPO reward training.
    """
    if not records:
        return []
    cap = max_records or int(os.getenv("PPO_REWARD_BUFFER_LOOKBACK", "600"))
    frac = high_vol_fraction
    if frac is None:
        try:
            frac = float(os.getenv("PPO_REWARD_HIGH_VOL_FRACTION", "0.30"))
        except (TypeError, ValueError):
            frac = 0.30
    frac = max(0.0, min(0.6, frac))

    high = [r for r in records if _is_high_volatility_record(r)]
    calm = [r for r in records if not _is_high_volatility_record(r)]
    n_high = min(len(high), max(1, int(cap * frac))) if high else 0
    n_calm = cap - n_high
    picked: List[Dict[str, Any]] = []
    seen = set()

    def _key(r: Dict[str, Any]) -> tuple:
        return (
            r.get("entry_id"),
            r.get("timestamp"),
            r.get("ticker"),
            r.get("source"),
        )

    for r in reversed(high[-max(n_high * 3, n_high):]):
        k = _key(r)
        if k in seen:
            continue
        seen.add(k)
        picked.append(r)
        if len([x for x in picked if _is_high_volatility_record(x)]) >= n_high:
            break

    for r in reversed(calm):
        k = _key(r)
        if k in seen:
            continue
        seen.add(k)
        picked.append(r)
        if len(picked) >= cap:
            break

    if len(picked) < cap:
        for r in reversed(records):
            k = _key(r)
            if k in seen:
                continue
            seen.add(k)
            picked.append(r)
            if len(picked) >= cap:
                break

    picked.reverse()
    return picked[:cap]


def stats() -> Dict[str, Any]:
    global _stats_cache, _stats_cache_ts
    ttl = float(os.getenv("EXPERIENCE_BUFFER_STATS_TTL_SEC", "120"))
    now = utc_now().timestamp()
    if _stats_cache and (now - _stats_cache_ts) < ttl:
        return dict(_stats_cache)
    sample_n = int(os.getenv("EXPERIENCE_BUFFER_STATS_SAMPLE", "2000"))
    recs = load_recent(sample_n)
    if not recs:
        out = {"total": count(), "sampled": 0, "win_rate": 0.0, "avg_reward": 0.0}
        _stats_cache = out
        _stats_cache_ts = now
        return out
    sources = {}
    wins = 0
    total_reward = 0.0
    missed_profit_hunts = 0
    profit_hunt_events = 0
    for r in recs:
        src = r.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        if r.get("win"):
            wins += 1
        total_reward += float(r.get("reward", 0.0))
        if r.get("event") == "missed_profit_hunt" or r.get("action") == "MISSED_PROFIT_HUNT":
            missed_profit_hunts += 1
        if r.get("source") == "profit_hunt" or str(r.get("event", "")).startswith(("spike", "hunt", "profit")):
            profit_hunt_events += 1
    # Avoid full-file scan on hot path — estimate total from cache or sample size
    total_lines = count() if _line_count is not None else len(recs)
    out = {
        "total": total_lines,
        "sampled": len(recs),
        "sources": sources,
        "win_rate": wins / len(recs) if recs else 0.0,
        "avg_reward": total_reward / len(recs) if recs else 0.0,
        "missed_profit_hunts": missed_profit_hunts,
        "profit_hunt_events": profit_hunt_events,
    }
    _stats_cache = out
    _stats_cache_ts = now
    return out
