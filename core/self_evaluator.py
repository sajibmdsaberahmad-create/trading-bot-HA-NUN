#!/usr/bin/env python3
"""
core/self_evaluator.py — Continuous AI self-evaluation and improvement.

The AI can evaluate its own performance, detect weaknesses, and
propose/apply improvements automatically within guardrails.
"""

import os
import sys
import json
import time
import math
import logging
import threading
import hashlib
from typing import Optional, Dict, List, Any, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

logger = logging.getLogger("SELF_EVALUATOR")


@dataclass
class EvaluationResult:
    """Result of a self-evaluation cycle."""
    timestamp: float
    overall_score: float = 0.0
    trade_efficiency: float = 0.0
    risk_management: float = 0.0
    learning_speed: float = 0.0
    adaptation_score: float = 0.0
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


class SelfEvaluator:
    """
    Continuously evaluate AI performance and generate improvement plans.
    
    This module gives the AI meta-cognition: it can observe its own
    behavior, measure effectiveness, and systematically improve.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._evaluation_history = deque(maxlen=1000)
        self._improvement_log = deque(maxlen=5000)
        self._last_evaluation = 0.0
        self._evaluation_interval = 3600.0  # 1 hour
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._evaluation_loop, daemon=True)
        self._thread.start()
        logger.info("Self-evaluator started")

    def stop(self):
        self._running = False

    def _evaluation_loop(self):
        while self._running:
            try:
                now = time.time()
                if now - self._last_evaluation >= self._evaluation_interval:
                    self.run_evaluation()
                    self._last_evaluation = now
                time.sleep(60)
            except Exception as exc:
                logger.debug(f"Evaluation loop: {exc}")

    def run_evaluation(self) -> EvaluationResult:
        """Run a full self-evaluation cycle."""
        with self._lock:
            result = self._evaluate()
            self._evaluation_history.append(result)
            logger.info(f"Self-evaluation: score={result.overall_score:.2f}/10 "
                        f"strengths={len(result.strengths)} weaknesses={len(result.weaknesses)}")
            return result

    def _evaluate(self) -> EvaluationResult:
        result = EvaluationResult(timestamp=time.time())

        # Trade efficiency (win rate, profit factor)
        wr = getattr(self.cfg, '_last_win_rate', 0.5)
        pf = getattr(self.cfg, '_last_profit_factor', 1.0)
        result.trade_efficiency = self._score_metric(wr, 0.3, 0.8) * 10
        result.trade_efficiency += self._score_metric(pf, 0.8, 3.0) * 3
        result.trade_efficiency = min(10.0, result.trade_efficiency)

        if result.trade_efficiency >= 7.0:
            result.strengths.append("Consistent profitability")
        elif result.trade_efficiency <= 3.0:
            result.weaknesses.append("Low win rate / poor profit factor")
            result.suggestions.append("Review entry timing, tighten stop-loss, reduce trade frequency")

        # Risk management (max drawdown, daily loss compliance)
        mdd = getattr(self.cfg, '_last_max_drawdown_pct', 5.0)
        result.risk_management = self._score_inverse(mdd, 1.0, 15.0) * 10
        result.risk_management = min(10.0, max(0.0, result.risk_management))

        if result.risk_management >= 7.0:
            result.strengths.append("Sound risk management")
        elif result.risk_management <= 3.0:
            result.weaknesses.append("Drawdown too large")
            result.suggestions.append("Reduce position size, tighten stops, add circuit breakers")

        # Learning speed (recent improvement trend)
        recent = list(self._evaluation_history)[-10:]
        if len(recent) >= 3:
            scores = [r.overall_score for r in recent]
            trend = scores[-1] - scores[0]
            result.learning_speed = min(10.0, max(0.0, 5.0 + trend * 10))
        else:
            result.learning_speed = 5.0

        if result.learning_speed >= 7.0:
            result.strengths.append("Rapid improvement detected")
        elif result.learning_speed <= 3.0:
            result.weaknesses.append("Plateau or regression in performance")
            result.suggestions.append("Increase exploration rate, adjust learning rate, add new features")

        # Adaptation score (regime changes handled well?)
        regime_switches = getattr(self.cfg, '_regime_switches_today', 0)
        perf_during_switches = getattr(self.cfg, '_perf_during_switches', 1.0)
        result.adaptation_score = self._score_metric(perf_during_switches, 0.3, 1.5) * 10
        if regime_switches > 3:
            if result.adaptation_score >= 6.0:
                result.strengths.append("Handles regime changes well")
            else:
                result.weaknesses.append("Struggles with regime transitions")
                result.suggestions.append("Add regime-specific models, increase regime detection frequency")

        # Overall score
        result.overall_score = (
            result.trade_efficiency * 0.35 +
            result.risk_management * 0.30 +
            result.learning_speed * 0.20 +
            result.adaptation_score * 0.15
        )
        result.metrics = {
            "win_rate": wr,
            "profit_factor": pf,
            "max_drawdown_pct": mdd,
            "regime_switches": regime_switches,
        }
        return result

    def _score_metric(self, value: float, good: float, bad: float) -> float:
        if good == bad:
            return 5.0
        if good < bad:
            if value <= good:
                return 10.0
            elif value >= bad:
                return 0.0
            return 10.0 * (bad - value) / (bad - good)
        else:
            if value >= good:
                return 10.0
            elif value <= bad:
                return 0.0
            return 10.0 * (value - bad) / (good - bad)

    def _score_inverse(self, value: float, good: float, bad: float) -> float:
        return self._score_metric(-value, -good, -bad)

    def get_improvement_plan(self) -> Dict[str, Any]:
        """Generate an actionable improvement plan."""
        if not self._evaluation_history:
            return {"status": "insufficient_data"}

        latest = self._evaluation_history[-1]
        plan = {
            "timestamp": datetime.utcnow().isoformat(),
            "overall_score": round(latest.overall_score, 2),
            "strengths": latest.strengths,
            "weaknesses": latest.weaknesses,
            "suggestions": latest.suggestions,
            "adjustments": {},
        }

        for suggestion in latest.suggestions:
            if "tighten stop" in suggestion.lower():
                plan["adjustments"]["STOP_ATR_MULTIPLIER"] = -0.1
            elif "reduce position" in suggestion.lower():
                plan["adjustments"]["RISK_PER_TRADE_PCT"] = -0.005
            elif "increase exploration" in suggestion.lower():
                plan["adjustments"]["PPO_ENT_COEF"] = 0.005
            elif "learning rate" in suggestion.lower():
                plan["adjustments"]["PPO_LR"] = 1.25
            elif "regime" in suggestion.lower():
                plan["adjustments"]["HMRS_MIN_REGIME_PROB"] = -0.05

        return plan

    def get_history(self, n: int = 50) -> List[Dict]:
        """Get recent evaluation history."""
        return [
            {
                "timestamp": r.timestamp,
                "score": r.overall_score,
                "strengths": len(r.strengths),
                "weaknesses": len(r.weaknesses),
            }
            for r in list(self._evaluation_history)[-n:]
        ]
