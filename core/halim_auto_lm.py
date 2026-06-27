#!/usr/bin/env python3
"""
core/halim_auto_lm.py — Auto export → SFT → MLX LoRA retrain when gold grows.

Runs off-hours (or when forced), never blocks trading. Restarts serve optionally
so the new adapter loads without manual steps.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

STATE_PATH = Path("models/halim_lm_evolve_state.json")
JOURNAL_PATH = Path("models/halim_lm_evolve.jsonl")

_lock = threading.Lock()
_running = False


def auto_lm_enabled() -> bool:
    return os.getenv("HALIM_AUTO_LM_RETRAIN", "true").lower() in ("1", "true", "yes")


def _load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.is_file():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _journal(row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _off_hours_ok(cfg: BotConfig) -> bool:
    if os.getenv("HALIM_AUTO_LM_OFF_HOURS_ONLY", "true").lower() not in ("1", "true", "yes"):
        return True
    try:
        from core.market_hours import can_trade_now
        can_trade, _ = can_trade_now(cfg)
        return not can_trade
    except Exception:
        return True


def _cooldown_ok(state: Dict[str, Any]) -> bool:
    last = state.get("last_train_started_at")
    if not last:
        return True
    cooldown = float(os.getenv("HALIM_AUTO_LM_COOLDOWN_SEC", "21600"))  # 6h
    try:
        started = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return elapsed >= cooldown
    except Exception:
        return True


def should_auto_retrain(
    export_result: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
    *,
    force: bool = False,
) -> Tuple[bool, str]:
    """Return (yes, reason) before spawning a train job."""
    from typing import Tuple

    if not auto_lm_enabled() and not force:
        return False, "disabled"
    cfg = cfg or BotConfig()
    if not force and not _off_hours_ok(cfg):
        return False, "market_open"

    state = _load_state()
    if not force and not _cooldown_ok(state):
        return False, "cooldown"

    total = int(export_result.get("total_gold") or 0)
    min_total = int(os.getenv("HALIM_AUTO_LM_MIN_TOTAL_PAIRS", "400"))
    if total < min_total and not force:
        return False, f"total_gold_{total}_lt_{min_total}"

    last_total = int(state.get("last_train_gold_total") or 0)
    new_pairs = int(export_result.get("added") or 0)
    delta = total - last_total
    min_new = int(os.getenv("HALIM_AUTO_LM_MIN_NEW_PAIRS", "150"))
    if not force and delta < min_new and new_pairs < min_new:
        return False, f"delta_{delta}_lt_{min_new}"

    global _running
    if _running:
        return False, "already_running"

    return True, "ok"


# Fix forward ref for Tuple in should_auto_retrain - I used Tuple before import. Let me fix the file.