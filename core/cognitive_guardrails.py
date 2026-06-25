#!/usr/bin/env python3
"""
core/cognitive_guardrails.py — Hard limits + bounded learning for AI self-tuning.

Runtime physical caps (daily loss USD, max positions) are non-negotiable.
Parameter mutation is allowed within core.param_bounds learning ranges.
Secrets and credentials remain locked.
"""

import os
import sys
import json
import time
import hashlib
import threading
import logging
from typing import Optional, Tuple, Dict, List, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log
from core.param_bounds import (
    ABSOLUTE_LOCK_PARAMS,
    clamp_param_value,
    is_locked,
    is_tunable,
    normalize_param,
    validate_mutation,
)
from core.paper_mode import account_equity, is_paper_free_learning

logger = logging.getLogger("COGNITIVE_GUARDRAILS")


@dataclass
class HardLimits:
    """Physical limits that the AI can NEVER modify."""
    MAX_TRADE_SIZE_USD: float = 100_000.0
    MAX_RISK_PER_TRADE_USD: float = 10_000.0
    MAX_DAILY_LOSS_USD: float = 50_000.0
    MAX_POSITIONS: int = 10
    MAX_SYSTEM_CHANGES_PER_DAY: int = 20
    MAX_PARAM_MUTATIONS_PER_DAY: int = 50
    MIN_CASH_RESERVE_PCT: float = 0.01
    MAX_POSITION_PCT: float = 0.95
    MAX_MODEL_INFERENCE_TIME_MS: int = 5000
    MAX_TELEGRAM_LENGTH: int = 4096
    MAX_CONCURRENT_THREADS: int = 16
    MAX_FILE_SIZE_MB: int = 500
    MAX_LOG_LINES_PER_DAY: int = 100000
    FORBIDDEN_FILES: List[str] = field(default_factory=lambda: [
        ".env",
        "core/config.py",
        "core/ai_guardrails.py",
        "core/cognitive_guardrails.py",
        "core/cognitive_core.py",
        "core/self_evaluator.py",
        "core/device_optimizer.py",
        "core/cognitive_autopilot.py",
    ])
    FORBIDDEN_PARAMS: List[str] = field(default_factory=lambda: list(ABSOLUTE_LOCK_PARAMS))


class EnforcedLimits:
    """Runtime-enforced limits with counters."""

    def __init__(self):
        self._today = datetime.utcnow().date()
        self._daily_trades = 0
        self._daily_pnl = 0.0
        self._daily_loss_usd = 0.0
        self._daily_system_changes = 0
        self._daily_param_mutations = 0
        self._open_positions = 0
        self._lock = threading.Lock()
        self._history = deque(maxlen=100_000)
        self._last_check = {}

    def _check_date(self):
        today = datetime.utcnow().date()
        if today != self._today:
            with self._lock:
                self._today = today
                self._daily_trades = 0
                self._daily_pnl = 0.0
                self._daily_loss_usd = 0.0
                self._daily_system_changes = 0
                self._daily_param_mutations = 0
                logger.info("Guardrail daily counters reset")

    def record_trade(self):
        with self._lock:
            self._check_date()
            if self._daily_trades >= HardLimits.MAX_POSITIONS * 3:
                return False, f"Daily trade limit: {self._daily_trades}"
            self._daily_trades += 1
            self._open_positions = min(self._open_positions + 1, HardLimits.MAX_POSITIONS)
        return True, ""

    def record_pnl(self, pnl_usd: float):
        with self._lock:
            self._check_date()
            self._daily_pnl += pnl_usd
            if pnl_usd < 0:
                self._daily_loss_usd += abs(pnl_usd)
                if self._daily_loss_usd >= HardLimits.MAX_DAILY_LOSS_USD:
                    return False, f"Daily loss limit: ${self._daily_loss_usd:,.0f}"
        return True, ""

    def record_system_change(self):
        with self._lock:
            self._check_date()
            if self._daily_system_changes >= HardLimits.MAX_SYSTEM_CHANGES_PER_DAY:
                return False, "Daily system changes exhausted"
            self._daily_system_changes += 1
        return True, ""

    def record_param_mutation(self):
        with self._lock:
            self._check_date()
            if self._daily_param_mutations >= HardLimits.MAX_PARAM_MUTATIONS_PER_DAY:
                return False, "Daily param mutations exhausted"
            self._daily_param_mutations += 1
        return True, ""

    def check_position_limit(self, desired_open: int) -> Tuple[bool, str]:
        with self._lock:
            if self._open_positions + desired_open > HardLimits.MAX_POSITIONS:
                return False, f"Position limit: {self._open_positions}/{HardLimits.MAX_POSITIONS}"
        return True, ""

    def record_position_closed(self):
        with self._lock:
            self._open_positions = max(0, self._open_positions - 1)

    def enforce_file_access(self, filepath: str) -> Tuple[bool, str]:
        """Prevent AI from touching forbidden files."""
        basename = os.path.basename(filepath)
        path_str = str(filepath)
        for forbidden in HardLimits.FORBIDDEN_FILES:
            if forbidden in path_str or basename == forbidden:
                return False, f"Access denied: {forbidden} is protected"
        return True, ""

    def enforce_param_change(self, param: str, value: Any = None, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
        """Prevent AI from modifying locked params; validate learning bounds when value given."""
        p = normalize_param(param)
        if is_locked(p):
            return False, f"Parameter locked: {p} cannot be modified by AI"
        if value is not None:
            if not is_tunable(p, cfg):
                return False, f"Parameter not in learning bounds: {p}"
            ok, msg = validate_mutation(p, value, cfg=cfg)
            if not ok:
                return False, msg
        return True, ""

    @property
    def daily_trades(self) -> int:
        self._check_date()
        return self._daily_trades

    @property
    def daily_pnl(self) -> float:
        self._check_date()
        return self._daily_pnl

    @property
    def daily_loss_usd(self) -> float:
        self._check_date()
        return self._daily_loss_usd

    @property
    def open_positions(self) -> int:
        return self._open_positions

    def status(self) -> Dict:
        self._check_date()
        return {
            "daily_trades": self._daily_trades,
            "daily_pnl": round(self._daily_pnl, 2),
            "daily_loss_usd": round(self._daily_loss_usd, 2),
            "open_positions": self._open_positions,
            "system_changes_today": self._daily_system_changes,
            "param_mutations_today": self._daily_param_mutations,
            "limits": {
                "max_trade_usd": HardLimits.MAX_TRADE_SIZE_USD,
                "max_daily_loss_usd": HardLimits.MAX_DAILY_LOSS_USD,
                "max_positions": HardLimits.MAX_POSITIONS,
            }
        }


class CognitiveGuardrails:
    """
    Master guardrail system for the cognitive AI.
    
    This is the final authority. No decision, code modification, or action
    bypasses this system. It sits between the AI's decisions and execution.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.limits = EnforcedLimits()
        self._active = True
        self._override_requested = False
        self._audit_log = deque(maxlen=50_000)
        self._violation_count = 0
        self._last_health_check = time.time()
        self._health_status = "healthy"

    def _runtime_trade_limits(self) -> Dict[str, float]:
        if is_paper_free_learning(self.cfg):
            eq = max(account_equity(self.cfg), 10_000.0)
            return {
                "max_trade_usd": eq * 0.95,
                "max_risk_usd": eq * 0.25,
                "max_positions": float(getattr(self.cfg, "AI_MAX_CONCURRENT_POSITIONS", 200)),
                "max_daily_loss_usd": eq * 0.50,
                "max_position_pct": 0.98,
            }
        return {
            "max_trade_usd": HardLimits.MAX_TRADE_SIZE_USD,
            "max_risk_usd": HardLimits.MAX_RISK_PER_TRADE_USD,
            "max_positions": float(HardLimits.MAX_POSITIONS),
            "max_daily_loss_usd": HardLimits.MAX_DAILY_LOSS_USD,
            "max_position_pct": HardLimits.MAX_POSITION_PCT,
        }

    def check_trade(self, action: str, ticker: str, qty: float, price: float,
                    risk_usd: float, current_positions: int) -> Tuple[bool, str]:
        """Comprehensive pre-trade validation."""
        if not self._active:
            return False, "Guardrails suspended"

        lim = self._runtime_trade_limits()

        if current_positions >= lim["max_positions"] and action in ("BUY", "LONG"):
            return False, f"Max positions ({int(lim['max_positions'])}) reached"

        if self.limits.daily_loss_usd >= lim["max_daily_loss_usd"]:
            return False, f"Daily loss limit hit: ${self.limits.daily_loss_usd:,.0f}"

        trade_value = abs(qty * price)
        if trade_value > lim["max_trade_usd"]:
            return False, f"Trade size ${trade_value:,.0f} exceeds ${lim['max_trade_usd']:,.0f}"

        if risk_usd > lim["max_risk_usd"]:
            return False, f"Risk ${risk_usd:,.0f} exceeds ${lim['max_risk_usd']:,.0f}"

        if hasattr(self.cfg, '_latest_account_balance'):
            account = self.cfg._latest_account_balance
            if account > 0:
                trade_pct = trade_value / account
                if trade_pct > lim["max_position_pct"]:
                    return False, f"Position {trade_pct:.0%} exceeds {lim['max_position_pct']:.0%}"

        # Track
        ok, msg = self.limits.record_trade()
        if not ok:
            return False, msg

        self._audit(f"trade_approved", f"{action} {qty} {ticker} @ ${price:.2f} risk=${risk_usd:.2f}")
        return True, ""

    def record_pnl(self, pnl_usd: float):
        ok, msg = self.limits.record_pnl(pnl_usd)
        if not ok:
            self._violation_count += 1
            logger.error(f"GUARDRAIL VIOLATION: {msg}")
            self._audit("pnl_violation", msg)
        return ok

    def can_modify_code(self, filepath: str) -> Tuple[bool, str]:
        ok, msg = self.limits.enforce_file_access(filepath)
        if not ok:
            self._violation_count += 1
            return False, msg
        ok, msg = self.limits.record_system_change()
        if not ok:
            return False, msg
        self._audit("code_modify", filepath)
        return True, ""

    def can_mutate_param(self, param: str, value: Any = None) -> Tuple[bool, str]:
        ok, msg = self.limits.enforce_param_change(param, value, cfg=self.cfg)
        if not ok:
            self._violation_count += 1
            return False, msg
        ok, msg = self.limits.record_param_mutation()
        if not ok:
            return False, msg
        self._audit("param_mutate", f"{param}={value}")
        return True, ""

    def health_check(self) -> Dict:
        return {
            "active": self._active,
            "health": self._health_status,
            "violations": self._violation_count,
            "limits": self.limits.status(),
            "audit_entries": len(self._audit_log),
        }

    def _audit(self, action: str, detail: str):
        entry = {
            "ts": time.time(),
            "ts_str": datetime.utcnow().isoformat(),
            "action": action,
            "detail": detail,
        }
        self._audit_log.append(entry)

    def full_lockdown(self, reason: str):
        """Emergency: stop all AI actions."""
        self._active = False
        self._health_status = "LOCKED"
        logger.error(f"COGNITIVE GUARDRAIL LOCKDOWN: {reason}")
        self._audit("LOCKDOWN", reason)

    def unlock(self):
        """Resume normal operation."""
        self._active = True
        self._health_status = "healthy"
        logger.info("Cognitive guardrails unlocked")
