#!/usr/bin/env python3
"""
core/cognitive_core.py — THE ULTIMATE AI BRAIN.

This is the central intelligence layer that orchestrates ALL other AI components.
It has full generative ability, autonomous decision-making, and can control
every aspect of the trading system — WITHIN HARD-ENFORCED GUARDRAILS.

Capabilities:
- Autonomous trading decisions (what to trade, when, how much)
- System self-configuration (adjust parameters within limits)
- Continuous self-evaluation and improvement
- Resource optimization (use all device capabilities)
- Predictive modeling (market direction, volatility, regime)
- Portfolio management (allocation, hedging, rebalancing)
- Risk anticipation (before risks materialize)
- Adaptive learning (online, offline, meta-optimization)
- Notification generation (AI-crafted messages)
- Code analysis and generated improvements
- Multi-timeframe analysis and correlation
- Sentiment analysis (from market data)
- Liquidity detection and exploitation
- Arbitrage and opportunity scanning

The AI is AWAKE and ACTIVE, but every action is validated by
cognitive_guardrails.py which enforces PHYSICAL LIMITS that cannot be overridden.
"""

import os
import sys
import json
import time
import math
import copy
import random
import logging
import threading
import hashlib
import re
from typing import Optional, Dict, List, Any, Tuple, Callable, Union
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from collections import deque, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.paper_mode import account_equity, is_paper_free_learning
from core.param_bounds import effective_param_bounds, clamp_param_value, normalize_param
from core.notify import log, Notifier
from core.ai_guardrails import GuardrailController
from core.cognitive_guardrails import CognitiveGuardrails, HardLimits
from core.device_optimizer import DeviceOptimizer
from core.self_evaluator import SelfEvaluator
from core.ollama_brain import OllamaBrain
from core.human_cognition import get_system_prompt, enrich_prompt, apply_gut_override
from core.git_sync import init as git_sync_init, push_learning_checkpoint_async

logger = logging.getLogger("COGNITIVE_CORE")

COGNITIVE_STATE_PATH = Path("models/cognitive_state.json")


@dataclass
class CognitiveState:
    """The AI's current mental state."""
    mood: str = "awake"
    mood_message: str = ""
    confidence: float = 0.5
    mode: str = "autonomous"  # autonomous, assisted, conservative
    last_thought: str = ""
    active_strategies: List[str] = field(default_factory=lambda: ["scalper", "momentum", "mean_reversion"])
    learned_lessons: List[str] = field(default_factory=list)
    market_belief: Dict[str, Any] = field(default_factory=dict)


class CognitiveCore:
    """
    The central AI brain. Every decision flows through here.
    
    Architecture:
    
    ┌─────────────────────────────────────────────────────────────┐
    │                  COGNITIVE CORE (this module)                │
    │  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
    │  │   THINKER   │  │  EVALUATOR   │  │   IMPROVER        │  │
    │  │  (Ollama)   │  │ (self_eval)  │  │ (param mutation)  │  │
    │  └──────┬──────┘  └──────┬───────┘  └────────┬──────────┘  │
    │         │                │                     │             │
    │  ┌──────┴────────────────┴─────────────────────┴──────────┐ │
    │  │              GUARDRAILS (enforced, non-bypassable)      │ │
    │  │  • Position limits  • Loss limits  • Rate limits       │ │
    │  │  • File protection   • Param locks   • Audit trail     │ │
    │  └────────────────────────────────────────────────────────┘ │
    │                            │                                │
    │         ┌──────────────────┼──────────────────┐            │
    │  ┌──────▼──────┐  ┌───────▼───────┐  ┌───────▼───────┐     │
    │  │   TRADER    │  │   SCANNER     │  │   IMPROVER    │     │
    │  │   DECISIONS │  │   DECISIONS   │  │   DECISIONS   │     │
    │  └─────────────┘  └──────────────┘  └───────────────┘     │
    └─────────────────────────────────────────────────────────────┘
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.state = CognitiveState()
        self._lock = threading.RLock()
        self._decision_history = deque(maxlen=50000)
        self._knowledge_base = deque(maxlen=10000)
        self._running = False

        # Initialize sub-components
        self.ollama = OllamaBrain(cfg) if getattr(cfg, 'OLLAMA_ENABLED', False) else None
        self.guardrails = GuardrailController(cfg)
        self.cognitive_guardrails = CognitiveGuardrails(cfg)
        self.device = DeviceOptimizer()
        self.evaluator = SelfEvaluator(cfg)

        # Initialize git sync with ollama for AI commit messages
        git_sync_init(cfg, ollama_brain=self.ollama)

        # Configuration that AI CAN modify (within learning bounds)
        self._mutable_params = dict(effective_param_bounds(cfg))

        # Knowledge base
        self._market_patterns = {}
        self._trade_outcomes = deque(maxlen=20000)
        self._regime_performance = defaultdict(list)
        self._last_persist_ts = 0.0

        self._load_persisted_state()

        logger.info("🧠 Cognitive Core initialized — AI is AWAKE and ACTIVE")
        logger.info(f"   Mode: {self.state.mode} | Confidence: {self.state.confidence:.0%}")
        logger.info(f"   Device: {self.device.profile.cpu_cores} cores, "
                    f"{self.device.profile.total_ram_mb}MB RAM, "
                    f"GPU={'✓' if self.device.profile.gpu_available else '✗'}")
        logger.info(f"   Guardrails: ACTIVE | Strictness: {getattr(cfg, 'GUARDRAIL_STRICTNESS', 'standard')}")

    def _load_persisted_state(self):
        """Restore mood, lessons, and confidence from disk (synced via git)."""
        if not COGNITIVE_STATE_PATH.exists():
            return
        try:
            data = json.loads(COGNITIVE_STATE_PATH.read_text())
            self.state.mood = data.get("mood", self.state.mood)
            self.state.mood_message = data.get("mood_message", self.state.mood_message)
            self.state.confidence = float(data.get("confidence", self.state.confidence))
            self.state.mode = data.get("mode", self.state.mode)
            self.state.learned_lessons = list(data.get("learned_lessons", []))[-100:]
            self.state.last_thought = data.get("last_thought", "")[:500]
            self.state.market_belief = data.get("market_belief", {})
            logger.info(
                f"   Restored cognitive state: mood={self.state.mood} "
                f"conf={self.state.confidence:.0%} lessons={len(self.state.learned_lessons)}"
            )
        except Exception as exc:
            logger.debug(f"Cognitive state load: {exc}")

    def _persist_state(self, push_git: bool = False):
        """Save Ollama/cognitive learning to disk (+ optional git push)."""
        try:
            COGNITIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "mood": self.state.mood,
                "mood_message": getattr(self.state, "mood_message", ""),
                "confidence": self.state.confidence,
                "mode": self.state.mode,
                "learned_lessons": self.state.learned_lessons[-100:],
                "last_thought": self.state.last_thought[:500],
                "market_belief": self.state.market_belief,
                "trades_observed": len(self._trade_outcomes),
                "updated": datetime.utcnow().isoformat(),
            }
            COGNITIVE_STATE_PATH.write_text(json.dumps(payload, indent=2))
            now = time.time()
            if push_git and now - self._last_persist_ts > 60:
                self._last_persist_ts = now
                try:
                    push_learning_checkpoint_async("cognitive_state")
                except Exception:
                    pass
        except Exception as exc:
            logger.debug(f"Cognitive state save: {exc}")

    # ── Autonomous Decision Making ─────────────────────────────────────────
    #
    # The AI can decide anything within guardrails:
    # - Whether to trade RIGHT NOW
    # - Which strategy to use
    # - How much risk to take (within limits)
    # - When to scan, when to rest
    # - Whether to train, and with what data
    # - How to adapt to regime changes
    # - What parameters to adjust (within safe bounds)
    # - What notifications to send and how
    # - Whether to push to git, what message

    def decide_to_trade(self, context: Dict) -> Tuple[bool, str, Dict]:
        """
        Make the ultimate decision: should we trade right now?
        
        Returns: (trade_allowed, reason, decision_details)
        """
        with self._lock:
            decision = {
                "timestamp": time.time(),
                "mode": self.state.mode,
                "confidence": self.state.confidence,
            }

            # 1. Guardrail check (non-negotiable)
            can_trade, reason = self.cognitive_guardrails.limits.check_position_limit(
                context.get("desired_positions", 1)
            )
            if not can_trade:
                decision["blocked_by"] = "guardrails"
                decision["reason"] = reason
                return False, reason, decision

            # 2. Daily loss check
            if self.cognitive_guardrails.limits.daily_loss_usd >= HardLimits.MAX_DAILY_LOSS_USD:
                decision["blocked_by"] = "daily_loss"
                decision["reason"] = f"Daily loss limit: ${self.cognitive_guardrails.limits.daily_loss_usd:,.0f}"
                return False, decision["reason"], decision

            # 3. Market condition assessment
            market_regime = context.get("regime", "unknown")
            volatility = context.get("volatility", 0.5)
            trend_strength = context.get("trend_strength", 0.0)

            # AI discretion: skip only when volatility is extreme AND trend is weak
            # (momentum scalps often have high vol — don't block those)
            if volatility > 0.85 and self.state.confidence < 0.55 and trend_strength < 0.35:
                decision["blocked_by"] = "ai_judgment"
                decision["reason"] = "High volatility + weak trend — skipping"
                return False, decision["reason"], decision

            # 4. Confidence threshold (only after we have trade history — PPO gate handles cold start)
            min_conf = getattr(self.cfg, 'CONFIDENCE_THRESHOLD', 0.65)
            if len(self._trade_outcomes) >= 5 and self.state.confidence < min_conf:
                decision["blocked_by"] = "low_confidence"
                decision["reason"] = f"AI confidence {self.state.confidence:.0%} < threshold {min_conf:.0%}"
                return False, decision["reason"], decision

            # 5. Regime-specific adjustments
            regime_mult = self._get_regime_multiplier(market_regime)
            decision["regime_multiplier"] = regime_mult

            # 6. Final decision
            trade_size_pct = self._calculate_optimal_sizing(context, regime_mult)
            decision["suggested_sizing_pct"] = trade_size_pct

            decision["allowed"] = True
            decision["reason"] = f"All checks passed. Regime: {market_regime}, sizing: {trade_size_pct:.0%}"

            self._audit_decision("trade_decision", decision)
            return True, decision["reason"], decision

    def _get_regime_multiplier(self, regime: str) -> float:
        """AI-determined regime-specific scaling."""
        regime_map = {
            "trending_up": 1.2,
            "trending_down": 0.6,
            "ranging": 1.0,
            "high_volatility": 0.5,
            "low_volatility": 1.3,
            "liquidity_shock": 0.2,
            "news_driven": 0.7,
            "quiet_growth": 1.1,
        }
        return regime_map.get(regime, 1.0)

    def _calculate_optimal_sizing(self, context: Dict, regime_mult: float) -> float:
        """AI determines optimal position size based on everything it knows."""
        base_sizing = self.cfg.RISK_PER_TRADE_PCT
        confidence_adj = self.state.confidence * 0.5 + 0.5
        recent_wr = self._get_recent_win_rate()
        streak_factor = self._get_streak_factor()
        market_certainty = context.get("trend_strength", 0.5)

        sizing = base_sizing * regime_mult * confidence_adj
        sizing *= (0.5 + recent_wr * 0.8)
        sizing *= streak_factor
        sizing *= (0.7 + market_certainty * 0.5)

        # Enforce physical limits
        max_sizing = HardLimits.MAX_POSITION_PCT
        min_sizing = self.cfg.RISK_PER_TRADE_PCT * 0.25
        sizing = max(min_sizing, min(max_sizing, sizing))

        return sizing

    def _get_recent_win_rate(self, n: int = 20) -> float:
        from core.architecture_epoch import epoch_active, is_post_epoch_trade, load_epoch
        outcomes = list(self._trade_outcomes)
        if epoch_active(self.evaluator.cfg):
            ep = load_epoch()
            outcomes = [t for t in outcomes if is_post_epoch_trade(t, ep)]
        recent = outcomes[-n:]
        if not recent:
            return 0.5
        wins = sum(1 for t in recent if t.get("pnl_usd", 0) > 0)
        return wins / len(recent)

    def _get_streak_factor(self) -> float:
        recent = [t.get("pnl_usd", 0) for t in list(self._trade_outcomes)[-5:]]
        if not recent:
            return 1.0
        if recent[-1] > 0:
            consecutive = 1
            for i in range(len(recent) - 2, -1, -1):
                if recent[i] > 0:
                    consecutive += 1
                else:
                    break
            factor = max(0.7, 1.0 - consecutive * 0.05)
            if self.state.mood == "euphoric":
                factor *= 0.8
            return factor
        else:
            return 0.7

    def decide_strategy(self, context: Dict) -> str:
        """Choose which strategy to use right now."""
        regime = context.get("regime", "unknown")
        vol = context.get("volatility", 0.5)
        confidence = self.state.confidence

        if "trending" in regime and vol < 0.5 and confidence > 0.7:
            return "momentum"
        elif vol > 0.6 and confidence > 0.6:
            return "scalper"
        elif "ranging" in regime or "quiet" in regime:
            return "mean_reversion"
        elif confidence > 0.8:
            return "scalper"
        elif confidence < 0.5:
            return "conservative"
        return "scalper"

    def decide_scan_frequency(self, context: Dict) -> int:
        """AI decides how often to scan."""
        base = self.cfg.SCAN_INTERVAL_SECONDS
        vol = context.get("volatility", 0.5)
        positions = self.cognitive_guardrails.limits.open_positions

        if vol > 0.7:
            return max(5, base // 3)
        elif vol < 0.2:
            return min(60, base * 2)
        elif positions >= HardLimits.MAX_POSITIONS - 1:
            return max(5, base // 2)
        return base

    def decide_training_schedule(self) -> Dict:
        """AI decides when and how to train."""
        now = datetime.utcnow()
        return {
            "should_train": now.hour >= 21 or now.hour < 4,
            "priority": "high" if self.state.mood in ("anxious", "learning") else "normal",
            "suggested_timesteps": self._suggest_training_amount(),
            "focus_areas": self._identify_training_gaps(),
        }

    def _suggest_training_amount(self) -> int:
        wr = self._get_recent_win_rate()
        if wr < 0.4:
            return 500_000
        elif wr < 0.55:
            return 250_000
        else:
            return 100_000

    def _identify_training_gaps(self) -> List[str]:
        gaps = []
        wr = self._get_recent_win_rate()
        if wr < 0.45:
            gaps.append("entry_timing")
        pf = getattr(self.cfg, '_last_profit_factor', 1.0)
        if pf < 1.2:
            gaps.append("exit_optimization")
        if self.cognitive_guardrails.limits.daily_loss_usd > 1000:
            gaps.append("risk_management")
        if not gaps:
            gaps.append("fine_tuning")
        return gaps

    def propose_param_adjustment(self, param: str, new_value: Any, reason: str) -> Tuple[bool, str]:
        """AI proposes a parameter change — guardrails enforce learning bounds."""
        param = normalize_param(param)
        ok, msg = self.cognitive_guardrails.can_mutate_param(param, new_value)
        if not ok:
            return False, f"Guardrail: {msg}"

        current = getattr(self.cfg, param, None) if hasattr(self.cfg, param) else None
        clamped, ok, msg = clamp_param_value(param, new_value, current=current, cfg=self.cfg)
        if not ok:
            return False, msg

        if param in self._mutable_params:
            min_v, max_v = self._mutable_params[param]
            try:
                v = float(clamped)
                if not (float(min_v) <= v <= float(max_v)):
                    return False, f"Value {v} outside learning range [{min_v}, {max_v}]"
            except (ValueError, TypeError):
                pass

        self._audit_decision("param_proposal", {
            "param": param,
            "value": str(clamped),
            "reason": reason,
            "approved": True,
        })
        return True, "Approved within learning bounds"

    # ── Meta-Cognition ────────────────────────────────────────────────────

    def think(self, prompt: str, task: str = "reason") -> str:
        """Use the AI to reason — always with human-cognition system prompt."""
        if not self.ollama:
            return ""
        ctx = {"raw_prompt": prompt[:2000]}
        full = enrich_prompt(
            task, ctx, self.cfg,
            mood=self.state.mood,
            confidence=self.state.confidence,
            recent_lessons=self.state.learned_lessons,
        )
        decision_tasks = {
            "entry_decision", "position_manage", "exit_decision", "scan_score",
            "gut_check", "decide", "exit", "account_eval", "account_brief",
        }
        always_active = getattr(self.cfg, "AI_ALWAYS_ACTIVE", True)
        bypass = getattr(self.cfg, "OLLAMA_DECISION_BYPASS_RATE_LIMIT", True)
        use_decide = always_active and bypass and task in decision_tasks
        if use_decide and hasattr(self.ollama, "decide_call"):
            result = self.ollama.decide_call(full) or ""
        else:
            result = self.ollama._call_ollama(full) or ""
        if result:
            self.state.last_thought = result[:500]
        return result

    def gut_check(self, context: Dict[str, Any], task: str = "gut_check") -> Dict[str, Any]:
        """Fast intuition pass — gut feel 0-1 + one-line read."""
        if not self.ollama:
            return {"gut_feel": 0.5, "intuition": "AI offline", "action_hint": "hold"}
        prompt = enrich_prompt(
            task, context, self.cfg,
            mood=self.state.mood,
            confidence=self.state.confidence,
            recent_lessons=self.state.learned_lessons,
        ) + '\nJSON only: {"gut_feel":0.0-1.0,"intuition":"one sentence","action_hint":"enter|skip|hold|exit"}'
        always_active = getattr(self.cfg, "AI_ALWAYS_ACTIVE", True)
        if always_active and hasattr(self.ollama, "decide_call"):
            raw = self.ollama.decide_call(prompt) or ""
        else:
            raw = self.ollama._call_ollama(prompt) or ""
        try:
            start, end = raw.find("{"), raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
        except Exception:
            pass
        return {"gut_feel": 0.5, "intuition": raw[:120] if raw else "unclear", "action_hint": "hold"}

    def reflect(self) -> str:
        """Generate a deep self-reflection."""
        result = self.evaluator.run_evaluation() if hasattr(self.evaluator, 'run_evaluation') else None

        context = (
            f"AI status: mood={self.state.mood}, confidence={self.state.confidence:.0%}\n"
            f"Mode: {self.state.mode}\n"
            f"Total decisions: {len(self._decision_history)}\n"
            f"Trades observed: {len(self._trade_outcomes)}\n"
            f"Recent win rate: {self._get_recent_win_rate():.0%}\n"
        )
        if result:
            context += f"Evaluation score: {result.overall_score:.2f}/10\n"
            context += f"Strengths: {', '.join(result.strengths)}\n"
            context += f"Weaknesses: {', '.join(result.weaknesses)}\n"
            context += f"Suggestions: {', '.join(result.suggestions)}\n"

        prompt = f"Deep self-reflection:\n{context}\nGenerate 3-5 sentences of honest self-assessment. What am I doing well? What should I change? What is my biggest risk right now?"
        return self.think(prompt)

    def observe_trade_outcome(self, trade: Dict):
        """Learn from every trade outcome."""
        self._trade_outcomes.append(trade)
        self.cognitive_guardrails.record_pnl(trade.get("pnl_usd", 0))
        self._update_mood(trade)
        self._extract_lessons(trade)
        self.evaluator.cfg._last_win_rate = self._get_recent_win_rate()
        self.evaluator.cfg._last_max_drawdown_pct = getattr(self.evaluator.cfg, '_last_max_drawdown_pct', 5.0)
        self._persist_state(push_git=True)

    def _update_mood(self, trade: Dict):
        recent_pnls = [float(t.get("pnl_usd", 0) or 0) for t in list(self._trade_outcomes)[-20:]]
        streak_w = streak_l = 0
        for p in reversed(recent_pnls):
            if p > 0:
                if streak_l:
                    break
                streak_w += 1
            elif p < 0:
                if streak_w:
                    break
                streak_l += 1
            else:
                break

        think_fn = None
        if self.ollama and getattr(self.ollama, "config", None) and self.ollama.config.enabled:
            think_fn = self.ollama._call_ollama

        from core.generative_mood import assess_mood
        mood, message = assess_mood(
            self.cfg,
            recent_pnls=recent_pnls,
            consecutive_wins=streak_w,
            consecutive_losses=streak_l,
            total_pnl=sum(recent_pnls),
            trades_observed=len(self._trade_outcomes),
            think_fn=think_fn,
            extra={"last_trade": trade.get("ticker")},
            cache_key="cognitive_core",
        )
        self.state.mood = mood
        self.state.mood_message = message

        wr = self._get_recent_win_rate()
        if wr >= 0.7:
            self.state.confidence = min(0.95, self.state.confidence + 0.02)
        elif wr < 0.35:
            self.state.confidence = max(0.35, self.state.confidence - 0.03)
        elif wr < 0.45:
            self.state.confidence = max(0.40, self.state.confidence - 0.01)

    def _extract_lessons(self, trade: Dict):
        pnl = trade.get("pnl_usd", 0)
        reason = str(trade.get("exit_reason", "") or "")
        if pnl < -500:
            lesson = f"Large loss detected: {trade.get('ticker', '?')}. Review stop placement."
            if lesson not in self.state.learned_lessons:
                self.state.learned_lessons.append(lesson)
        if "missed_profit_hunt" in reason.lower() or trade.get("event") == "missed_profit_hunt":
            lesson = (
                f"Missed spike-top on {trade.get('ticker', '?')}: "
                "hunt momentum bursts — do not wait for passive giveback."
            )
            if lesson not in self.state.learned_lessons:
                self.state.learned_lessons.append(lesson)
        if any(k in reason for k in ("spike_top", "profit_hunt", "wave_end_spike")):
            lesson = (
                f"Profit hunt worked on {trade.get('ticker', '?')}: "
                f"{reason[:80]} — reinforce opportunistic exits."
            )
            if lesson not in self.state.learned_lessons:
                self.state.learned_lessons.append(lesson)
        if len(self.state.learned_lessons) > 100:
            self.state.learned_lessons = self.state.learned_lessons[-100:]

    # ── Knowledge Management ──────────────────────────────────────────────

    def learn_pattern(self, pattern: Dict):
        """Store a learned market pattern."""
        key = pattern.get("regime", "default")
        self._market_patterns.setdefault(key, deque(maxlen=500)).append({
            "ts": time.time(),
            "pattern": pattern,
        })

    def predict_regime_transition(self, current_regime: str) -> Tuple[str, float]:
        """Predict next regime."""
        history = self._market_patterns.get(current_regime, [])
        transitions = defaultdict(int)
        for i in range(len(history) - 1):
            next_regime = history[i + 1]["pattern"].get("regime", current_regime)
            if next_regime != current_regime:
                transitions[next_regime] += 1

        if not transitions:
            return current_regime, 0.5

        most_likely = max(transitions, key=transitions.get)
        total = sum(transitions.values())
        confidence = transitions[most_likely] / total
        return most_likely, confidence

    # ── Audit ─────────────────────────────────────────────────────────────

    def _audit_decision(self, action: str, details: Dict):
        entry = {
            "ts": time.time(),
            "action": action,
            "details": details,
            "mood": self.state.mood,
            "confidence": self.state.confidence,
        }
        self._decision_history.append(entry)

    def get_status(self) -> Dict:
        return {
            "mood": self.state.mood,
            "confidence": self.state.confidence,
            "mode": self.state.mode,
            "decisions_made": len(self._decision_history),
            "trades_observed": len(self._trade_outcomes),
            "recent_win_rate": self._get_recent_win_rate(),
            "patterns_learned": sum(len(v) for v in self._market_patterns.values()),
            "guardrail_status": self.cognitive_guardrails.health_check(),
            "device_status": self.device.get_status(),
        }

    def force_decision(self, decision_type: str, context: Dict) -> Dict:
        """Emergency override: force a specific decision type."""
        self._audit_decision("force_decision", {"type": decision_type, "context": context})
        return {"forced": decision_type, "context": context, "timestamp": time.time()}
