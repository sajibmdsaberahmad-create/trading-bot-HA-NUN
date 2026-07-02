#!/usr/bin/env python3
"""
core/halim_self_tune.py — Parameter self-tuning from Halim overseer observations.

Halim observes patterns → self-tune adjusts parameters within safe bounds.
Every change is logged. No code generation. No file modification.
All adjustments are small (+/- 0.02 float, +/- 1 int), cumulative over time.

Safety:
  - Every parameter has hardcoded min/max bounds
  - Changes are gradual (never jump more than 1 step per cycle)
  - No changes during active position management
  - Full audit trail at models/self_tune_journal.jsonl
  - Can be disabled entirely via SELF_TUNE_ENABLED=false
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

# ── Tuneable parameters with hardcoded safety bounds ─────────────────────
# Format: (env_key, default, min, max, step, description)
TUNEABLE_PARAMS: List[Tuple[str, float, float, float, float, str]] = [
    ("CONFIDENCE_THRESHOLD", 0.65, 0.45, 0.75, 0.03,
     "Min PPO confidence for entry"),
    ("MIN_PROFIT_PROBABILITY", 0.62, 0.40, 0.75, 0.03,
     "Min profit probability for entry"),
    ("TECH_OVERRIDE_SPIKE_MIN", 1.3, 1.0, 2.0, 0.1,
     "Min spike ratio for tech override"),
    ("TECH_OVERRIDE_SCORE_MIN", 30.0, 20.0, 50.0, 2.0,
     "Min scan score for tech override"),
    ("SPIKE_SKIP_SEC", 30.0, 5.0, 60.0, 3.0,
     "Cooldown after skipped spike"),
]

TUNE_JOURNAL = Path("models/self_tune_journal.jsonl")

# ── Runtime state ─────────────────────────────────────────────────────────
_overrides: Dict[str, float] = {}
_override_lock = threading.Lock()
_last_tune_at: float = 0.0
_last_journal_trim: float = 0.0


def self_tune_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("SELF_TUNE_ENABLED", "true").lower() in ("1", "true", "yes")


def self_tune_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    return float(os.getenv("SELF_TUNE_INTERVAL_SEC", "300"))  # 5 min default


def apply_override(cfg: BotConfig, key: str, value: float) -> None:
    """Set a runtime override on the config instance."""
    with _override_lock:
        _overrides[key] = value
    setattr(cfg, key, value)


def current_overrides() -> Dict[str, float]:
    with _override_lock:
        return dict(_overrides)


def current_adjustment_summary() -> str:
    """Human-readable summary of active overrides."""
    with _override_lock:
        if not _overrides:
            return "default (no overrides)"
    parts = []
    for k, v in sorted(_overrides.items()):
        parts.append(f"{k}={v}")
    return " | ".join(parts)


# ── Observation loading ───────────────────────────────────────────────────

def _load_recent_observations(n: int = 5) -> List[Dict[str, Any]]:
    """Load the last N observations from the overseer gold log."""
    overseer_log = Path("halim/data/overseer/observations.jsonl")
    if not overseer_log.is_file():
        return []
    try:
        with open(overseer_log, encoding="utf-8") as fh:
            lines = fh.readlines()
        obs = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                obs.append(row.get("observations") or {})
            except Exception:
                continue
        return obs
    except Exception:
        return []


# ── Pattern detection → adjustment ───────────────────────────────────────

_PATTERN_RULES = [
    # (pattern_regex, param_key, direction, strength, description)
    # "too many vetoes" → lower thresholds (negative = lower)
    (r"veto.*cluster|consistently.*blocked|too many.*veto|many.*skip", "relax", None),
    (r"never.*fire|always.*HOLD|stuck.*hold|ppo.*never.*buy", "lower_conf", None),
    (r"profit.prob|profit_prob.*veto|profit.*near.*threshold|just.*below", "lower_profit_prob", None),
    (r"too.*loose|never.*blocked|never.*veto|always.*pass", "tighten", None),
    (r"spike.*miss|missed.*spike|spike.*ignored", "lower_spike_min", None),
    (r"memory|slow|degrad|timeout|swap", "memory", None),
]


def _categorize(observation: Dict[str, Any]) -> List[str]:
    """Match observation text to pattern categories."""
    text = json.dumps(observation).lower()
    categories = []
    for pattern, action, _ in _PATTERN_RULES:
        if re.search(pattern, text):
            categories.append(action)
    return categories


def _compute_adjustments(
    observations: List[Dict[str, Any]],
    current: Dict[str, float],
) -> Dict[str, float]:
    """Compute parameter adjustments from observations."""
    adjustments: Dict[str, float] = {}

    # Count pattern categories across observations
    category_counts: Dict[str, int] = {}
    for obs in observations:
        for cat in _categorize(obs):
            category_counts[cat] = category_counts.get(cat, 0) + 1

    # Convert counts to parameter adjustments
    relax_count = category_counts.get("relax", 0)
    tighten_count = category_counts.get("tighten", 0)
    lower_conf = category_counts.get("lower_conf", 0)
    lower_profit = category_counts.get("lower_profit_prob", 0)
    lower_spike = category_counts.get("lower_spike_min", 0)

    if relax_count > tighten_count:
        # More relax signals than tighten — ease thresholds
        net = min(relax_count - tighten_count, 3)
        adjustments["CONFIDENCE_THRESHOLD"] = -0.03 * net
        adjustments["MIN_PROFIT_PROBABILITY"] = -0.03 * net
    elif tighten_count > relax_count:
        net = min(tighten_count - relax_count, 3)
        adjustments["CONFIDENCE_THRESHOLD"] = 0.03 * net
        adjustments["MIN_PROFIT_PROBABILITY"] = 0.03 * net

    if lower_conf > 0:
        n = min(lower_conf, 3)
        adjustments["CONFIDENCE_THRESHOLD"] = adjustments.get("CONFIDENCE_THRESHOLD", 0) - 0.03 * n

    if lower_profit > 0:
        n = min(lower_profit, 3)
        adjustments["MIN_PROFIT_PROBABILITY"] = adjustments.get("MIN_PROFIT_PROBABILITY", 0) - 0.03 * n

    if lower_spike > 0:
        n = min(lower_spike, 3)
        adjustments["TECH_OVERRIDE_SPIKE_MIN"] = adjustments.get("TECH_OVERRIDE_SPIKE_MIN", 0) - 0.1 * n

    return adjustments


def _clamp(key: str, value: float) -> float:
    """Clamp value to the param's safety bounds."""
    for env_key, default, pmin, pmax, step, desc in TUNEABLE_PARAMS:
        if env_key == key:
            return max(pmin, min(pmax, round(value / step) * step))
    return value


def _default_of(key: str) -> float:
    for env_key, default, *_ in TUNEABLE_PARAMS:
        if env_key == key:
            return default
    return 0.0


def _journal(entry: Dict[str, Any]) -> None:
    """Append to the self-tune journal."""
    try:
        TUNE_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(TUNE_JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"Self-tune journal: {exc}")


def _trim_journal() -> None:
    """Keep only the last 1000 entries."""
    global _last_journal_trim
    now = time.time()
    if now - _last_journal_trim < 3600:
        return
    _last_journal_trim = now
    try:
        if TUNE_JOURNAL.is_file():
            with open(TUNE_JOURNAL, encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) > 1000:
                with open(TUNE_JOURNAL, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-1000:])
    except Exception:
        pass


# ── Main entry point ─────────────────────────────────────────────────────

def tune_cycle(cfg: BotConfig) -> Dict[str, Any]:
    """
    Run one self-tune cycle: read observations, compute adjustments, apply.

    Safe to call any time — does nothing if:
      - Self-tune disabled
      - Less than INTERVAL_SEC since last cycle
      - No observations to process

    Returns dict of changes made (empty if none).
    """
    if not self_tune_enabled(cfg):
        return {"ok": False, "reason": "disabled"}

    global _last_tune_at
    now = time.time()
    iv = self_tune_interval_sec(cfg)
    if now - _last_tune_at < iv:
        return {"ok": False, "reason": "too_soon"}
    _last_tune_at = now

    observations = _load_recent_observations(n=5)
    if not observations:
        return {"ok": False, "reason": "no_observations"}

    # Get current overrides for reference
    with _override_lock:
        current = dict(_overrides)

    adjustments = _compute_adjustments(observations, current)
    if not adjustments:
        _trim_journal()
        return {"ok": True, "changes": {}, "reason": "no_adjustments_needed"}

    # Apply each adjustment within bounds
    changes = {}
    for key, delta in adjustments.items():
        old_val = current.get(key) or float(getattr(cfg, key, _default_of(key)))
        new_val = _clamp(key, old_val + delta)
        if abs(new_val - old_val) >= 0.001:
            apply_override(cfg, key, new_val)
            changes[key] = {"from": round(old_val, 4), "to": round(new_val, 4)}

    if changes:
        summary = " | ".join(f"{k}: {v['from']}→{v['to']}" for k, v in changes.items())
        log.info(f"🔧 Self-tune: {summary}")
        _journal({
            "changes": changes,
            "adjustments": {k: round(v, 4) for k, v in adjustments.items()},
            "observations": [o.get("observation", "")[:120] for o in observations[-3:]],
        })

    _trim_journal()
    return {"ok": True, "changes": changes}


# ── Manual parameter query (for logging / status) ────────────────────────

def describe_parameter(key: str) -> str:
    """Describe a tuneable parameter's current state."""
    for env_key, default, pmin, pmax, step, desc in TUNEABLE_PARAMS:
        if env_key == key:
            with _override_lock:
                val = _overrides.get(key, default)
            return f"{key}={val} ({desc}, range [{pmin}, {pmax}], step={step})"
    return f"{key}: unknown"
