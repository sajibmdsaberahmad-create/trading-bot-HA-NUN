#!/usr/bin/env python3
"""
core/cognitive_autopilot.py — THE FINAL ORCHESTRATOR.

This module connects the Cognitive Core to EVERY component in the system
and gives the AI FULL AUTONOMY to make any decision, anywhere, anytime —
as long as it stays within the hard guardrails.

This is the "awake" layer. Once this is running, the AI is fully self-driving.
"""

import os
import sys
import json
import time
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
from core.notify import log, Notifier
from core.cognitive_core import CognitiveCore
from core.cognitive_guardrails import CognitiveGuardrails
from core.device_optimizer import DeviceOptimizer
from core.self_evaluator import SelfEvaluator

logger = logging.getLogger("COGNITIVE_AUTOPILOT")


class CognitiveAutopilot:
    """
    Full-autonomy wrapper around the Cognitive Core.
    
    This is the top-level interface that every other module calls when
    it needs an AI decision. It guarantees:
    
    1. All AI decisions go through guardrails (never bypassed)
    2. Decisions are cached and deduplicated where possible
    3. Heavy thinking is offloaded to background threads
    4. The system can run fully autonomously 24/7
    5. Graceful degradation if AI subsystem fails
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.enabled = getattr(cfg, 'COGNITIVE_MODE_ENABLED', True)
        if not self.enabled:
            logger.info("Cognitive autopilot DISABLED")
            self.core = None
            self.guardrails = None
            self.device = None
            self.evaluator = None
            return

        self.core = CognitiveCore(cfg)
        self.guardrails = getattr(self.core, 'cognitive_guardrails', None)
        self.device = getattr(self.core, 'device', None)
        self.evaluator = getattr(self.core, 'evaluator', None)

        self._decision_cache = {}
        self._cache_ttl = 2.0
        self._background_tasks = deque(maxlen=100)
        self._running = False
        self._main_thread = None
        self._shutdown_event = threading.Event()

        logger.info("🤖 Cognitive Autopilot ONLINE — AI is fully autonomous")
        logger.info("   Human cognition: analyze + gut feel + experience on every decision")
        logger.info("   Computational tools: PPO, scanner, volume, regime — all synthesized")

    def start(self):
        """Start the background autopilot loop."""
        if self._running or not self.enabled:
            return
        self._running = True
        self._main_thread = threading.Thread(target=self._autopilot_loop, daemon=True)
        self._main_thread.start()

        if self.evaluator:
            self.evaluator.start()
        if self.device:
            self.device.start_monitoring()

        logger.info("Autopilot loop started")

    def stop(self):
        """Graceful shutdown."""
        self._shutdown_event.set()
        self._running = False
        if self.core:
            try:
                self.core._persist_state(push_git=True)
            except Exception:
                pass
        if self.evaluator:
            self.evaluator.stop()
        if self.device:
            self.device.stop_monitoring()
        logger.info("Autopilot stopped")

    def _autopilot_loop(self):
        """Background loop for continuous AI operations."""
        while self._running and not self._shutdown_event.is_set():
            try:
                self._background_cycle()
                self._shutdown_event.wait(timeout=10)
            except Exception as exc:
                logger.debug(f"Autopilot cycle: {exc}")

    def _background_cycle(self):
        """One cycle of background AI work."""
        if not self.core:
            return

        # 1. Periodic self-evaluation
        now = time.time()
        if not hasattr(self, '_last_eval') or now - getattr(self, '_last_eval', 0) > 3600:
            try:
                result = self.core.evaluator.run_evaluation() if self.evaluator else None
                if result and result.overall_score < 4.0:
                    self._trigger_deep_analysis()
                self._last_eval = now
            except Exception:
                pass

        # 2. Predictive scan
        if not hasattr(self, '_last_predict') or now - getattr(self, '_last_predict', 0) > 300:
            try:
                self.core.predict_regime_transition("unknown")
                self._last_predict = now
            except Exception:
                pass

    def _trigger_deep_analysis(self):
        """When performance is poor, trigger deep analysis."""
        try:
            reflection = self.core.reflect()
            if reflection:
                logger.info(f"Deep analysis: {reflection[:200]}")
        except Exception as exc:
            logger.debug(f"Deep analysis failed: {exc}")

    # ── Decision Interface ─────────────────────────────────────────────────

    def should_trade(self, context: Dict) -> Tuple[bool, str, Dict]:
        """Main entry point: should we trade right now?"""
        if not self.enabled or not self.core:
            return True, "AI disabled, allowing trade", {}

        cache_key = f"trade_{hash(str(sorted(context.items())))}"
        if cache_key in self._decision_cache:
            cached_time, cached_result = self._decision_cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                return cached_result

        result = self.core.decide_to_trade(context)
        self._decision_cache[cache_key] = (time.time(), result)
        if len(self._decision_cache) > 1000:
            self._decision_cache.clear()
        return result

    def choose_strategy(self, context: Dict) -> str:
        """AI chooses the best strategy for current conditions."""
        if not self.enabled or not self.core:
            return "scalper"
        return self.core.decide_strategy(context)

    def get_scan_interval(self, context: Dict) -> int:
        """AI decides how fast to scan."""
        if not self.enabled or not self.core:
            return 30
        return self.core.decide_scan_frequency(context)

    def should_train(self) -> Tuple[bool, Dict]:
        """AI decides whether to train now."""
        if not self.enabled or not self.core:
            return False, {}
        plan = self.core.decide_training_schedule()
        return plan.get("should_train", False), plan

    def propose_improvement(self, param: str, value: Any, reason: str) -> Tuple[bool, str]:
        """AI proposes a change."""
        if not self.enabled or not self.core:
            return False, "AI disabled"
        return self.core.propose_param_adjustment(param, value, reason)

    def generate_notification(self, event_type: str, context: Dict) -> str:
        """Generate a contextually intelligent notification (used by AI composer)."""
        if not self.enabled or not self.core or not self.core.ollama:
            return ""

        max_chars = int(getattr(self.cfg, "AI_TELEGRAM_MAX_CHARS", 450))
        mood = getattr(self.core.state, "mood", "awake")
        prompt = (
            "You are HANOON trading pilot AI. Write a Telegram briefing — analytical, organized, human.\n"
            f"Event: {event_type} | Mood: {mood}\n"
            f"Data: {json.dumps(context, default=str)[:700]}\n"
            "Include exact numbers (price, P&L, stop, R:R). First-person voice. "
            f"Max {max_chars} chars. Plain text, 3-4 lines, no JSON."
        )
        return self.core.think(prompt)

    def observe_market_event(self, event: Dict):
        """Let the AI learn from everything that happens in the market."""
        if not self.core:
            return
        self.core.learn_pattern(event)

    def observe_trade(self, trade: Dict):
        """Let the AI evaluate every trade outcome."""
        if not self.core:
            return
        self.core.observe_trade_outcome(trade)

    def deep_reflection(self) -> str:
        """Trigger a comprehensive self-reflection."""
        if not self.core:
            return ""
        return self.core.reflect()

    def get_full_status(self) -> Dict:
        """Get complete AI system status."""
        if not self.core:
            return {"enabled": False}
        return self.core.get_status()

    def emergency_stop(self, reason: str = "manual"):
        """Emergency: halt all trading."""
        if self.guardrails:
            self.guardrails.full_lockdown(reason)
        logger.error(f"EMERGENCY STOP: {reason}")

    def resume(self):
        """Resume after emergency stop."""
        if self.guardrails:
            self.guardrails.unlock()
        logger.info("Cognitive Autopilot resumed")
