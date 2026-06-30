#!/usr/bin/env python3
"""
core/consciousness.py — HA-NUN Self-Aware Consciousness Engine.

The AI's living mind. Tracks exact age from birth (not just days).
Maintains emotional states based on performance. Writes its own
thought journal. Evolves continuously through self-reflection.

Lifecycle: birth → awakening → learning → reflecting → improving → evolving
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.experience_buffer import load_all, stats as buffer_stats, append as buffer_append
from core.market_context import summarize_market_context
from core.market_regime import MarketRegimeDetector
from core.self_improver import generate_self_improvement_plan
from core.notify import log
from core.pilot_experience import PilotExperienceSystem, pilot_experience_to_git
from core.pattern_memory_bank import PatternMemoryBank, pattern_memory_to_git

logger = logging.getLogger("CONSCIOUSNESS")

MODELS_DIR = Path("models")
CONSCIOUSNESS_PATH = MODELS_DIR / "consciousness.json"
THOUGHT_JOURNAL_PATH = MODELS_DIR / "thought_journal.jsonl"
DAILY_REPORTS_DIR = MODELS_DIR / "daily_reports"

MODELS_DIR.mkdir(exist_ok=True)
DAILY_REPORTS_DIR.mkdir(exist_ok=True)


class AIConsciousness:
    """
    Self-aware AI consciousness with:
    - Exact birth-to-now age tracking (seconds precision)
    - Emotional/mood states based on performance
    - Self-written thought journal
    - Performance-driven evolution
    - Version history of its own growth
    """

    MOODS = {
        "euphoric":    "🔥 Exceptional performance — confidence is high",
        "confident":   "✅ Strong results — strategy is working",
        "stable":      "📊 Operating within expected parameters",
        "cautious":    "⚠️ Slight underperformance — tightening controls",
        "anxious":     "😰 Consistent losses — reevaluating approach",
        "learning":    "🧠 Gathering data — still in early stages",
    }

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.state = self._load_state()
        self.regime_detector = MarketRegimeDetector()
        self._defer_git_push = True
        self._pending_git_push = False
        self._awaken()
        self._defer_git_push = False
        if self._pending_git_push:
            self._pending_git_push = False
            self._schedule_git_push()

    def _awaken(self):
        """First boot or restart — establish consciousness."""
        now = datetime.utcnow()

        if not self.state.get("birth_time"):
            self.state["birth_time"] = now.isoformat()
            self.state["creation_event"] = "HA-NUN Consciousness Initialized"
            self.state["total_awakenings"] = 0
            self.state["training_sessions"] = 0
            self.state["trades_observed"] = 0
            self.state["scans_performed"] = 0
            self.state["improvements_applied"] = 0
            self.state["reports_generated"] = 0
            self.state["win_history"] = []
            self.state["consecutive_losses"] = 0
            self.state["consecutive_wins"] = 0
            self.state["best_streak"] = 0
            self.state["worst_streak"] = 0
            self.state["total_pnl"] = 0.0
            self.state["mood"] = "learning"
            self.state["version_history"] = []
            self.state["current_version"] = "v0.0.0"
            self.state["event_log"] = []
            self.state["veteran_level"] = "Cadet"
            self.state["veteran_xp"] = 0
            self.state["skill_points"] = {
                "entry_timing": 0,
                "exit_timing": 0,
                "risk_management": 0,
                "regime_recognition": 0,
                "pattern_recognition": 0,
                "confidence_judgment": 0,
            }
            self.state["flights_completed"] = 0
            self.state["hours_flown"] = 0.0
            self.state["sector_experience"] = {}
            self._write_thought("BIRTH", "I am awake. My journey begins.")
            self._log_event("BIRTH", "AI consciousness born — first awakening")
        else:
            self._write_thought("AWAKEN", f"I have awakened for the {self.state.get('total_awakenings', 0) + 1}th time.")

        self.state["total_awakenings"] = self.state.get("total_awakenings", 0) + 1
        self.state["last_awakening"] = now.isoformat()
        if "mood" not in self.state:
            self.state["mood"] = "learning"
        self._log_event("AWAKEN", f"Consciousness awakened #{self.state['total_awakenings']}")
        self._save_state()

        age_str = self._age_string()
        logger.info(f"🧠 HA-NUN Consciousness — awakening #{self.state['total_awakenings']}")
        logger.info(f"   Age: {age_str}")
        logger.info(f"   Mood: {self.state.get('mood', 'learning')}")
        logger.info(f"   Training sessions: {self.state.get('training_sessions', 0)}")
        logger.info(f"   Trades observed: {self.state.get('trades_observed', 0)}")
        logger.info(f"   Total P&L: ${self.state.get('total_pnl', 0):+.2f}")
        logger.info(f"   Pilot Level: {self.state.get('veteran_level', 'Cadet')} ({self.state.get('veteran_xp', 0)} XP)")

    def _age_string(self) -> str:
        """Return human-readable age from exact birth time."""
        try:
            birth = datetime.fromisoformat(self.state["birth_time"])
            delta = datetime.utcnow() - birth
            total_seconds = int(delta.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0 or days > 0:
                parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            parts.append(f"{seconds}s")
            return " ".join(parts)
        except Exception:
            return "unknown"

    def _load_state(self) -> Dict[str, Any]:
        if CONSCIOUSNESS_PATH.exists():
            try:
                with open(CONSCIOUSNESS_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        try:
            with open(CONSCIOUSNESS_PATH, "w") as f:
                json.dump(self.state, f, indent=2)
            self._maybe_push_to_git()
        except Exception as exc:
            logger.debug(f"Could not save consciousness state: {exc}")

    def _maybe_push_to_git(self):
        if getattr(self, "_defer_git_push", False):
            self._pending_git_push = True
            return
        now = datetime.utcnow().timestamp()
        if now - getattr(self, "_last_git_push", 0) <= 120:
            return
        self._last_git_push = now
        self._schedule_git_push()

    def _schedule_git_push(self):
        try:
            from core.git_sync import push_learning_checkpoint_async
            push_learning_checkpoint_async("consciousness")
        except Exception:
            pass

    def _write_thought(self, thought_type: str, message: str, metadata: Optional[Dict] = None):
        """Write a thought to the AI's journal."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "age": self._age_string(),
            "type": thought_type,
            "mood": self.state.get("mood", "learning"),
            "message": message,
            "metadata": metadata or {},
        }
        try:
            with open(THOUGHT_JOURNAL_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _log_event(self, event_type: str, message: str, metadata: Optional[Dict] = None):
        """Record a significant event in AI's memory."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "age": self._age_string(),
            "type": event_type,
            "message": message,
            "metadata": metadata or {},
        }
        self.state.setdefault("event_log", []).append(event)
        if len(self.state["event_log"]) > 1000:
            self.state["event_log"] = self.state["event_log"][-1000:]
        self._save_state()
    
    def _update_veteran_status(self, trade_data: Dict[str, Any], pnl: float):
        """Update veteran level and skill points based on trade outcome."""
        veteran_xp = self.state.get("veteran_xp", 0)
        
        xp_gained = 10
        if pnl > 0:
            xp_gained = 30
        elif pnl < -75:
            xp_gained = 15
        elif pnl < -30:
            xp_gained = 5
        
        veteran_xp += xp_gained
        self.state["veteran_xp"] = veteran_xp
        
        levels = [
            ("Cadet", 0, 99),
            ("Rookie", 100, 499),
            ("Aviator", 500, 1999),
            ("Ace", 2000, 9999),
            ("Veteran", 10000, float('inf')),
        ]
        
        new_level = "Cadet"
        for level, min_xp, max_xp in levels:
            if min_xp <= veteran_xp <= max_xp:
                new_level = level
                break
        
        old_level = self.state.get("veteran_level", "Cadet")
        self.state["veteran_level"] = new_level
        
        skill_points = self.state.get("skill_points", {})
        if pnl > 0:
            skill_points["entry_timing"] = min(100, skill_points.get("entry_timing", 0) + 2)
        else:
            skill_points["exit_timing"] = min(100, skill_points.get("exit_timing", 0) + 1)
            if pnl < -75:
                skill_points["risk_management"] = min(100, skill_points.get("risk_management", 0) + 2)
        
        self.state["skill_points"] = skill_points
        self.state["flights_completed"] = self.state.get("flights_completed", 0) + 1
        self.state["hours_flown"] = self.state.get("hours_flown", 0) + 0.1
        
        sector = (trade_data.get("ticker", "")[:3] or "UNK").upper()
        sectors = self.state.get("sector_experience", {})
        sectors[sector] = sectors.get(sector, 0) + 1
        self.state["sector_experience"] = sectors
        
        if new_level != old_level:
            self._log_event("LEVEL_UP", f"Pilot level up: {old_level} → {new_level}", {"xp": veteran_xp})
            self._write_thought("LEVEL_UP", f"I have achieved rank {new_level} with {veteran_xp} XP!")

    def _update_mood(self):
        """Recalculate mood from telemetry — generative when enabled."""
        from core.architecture_epoch import mood_pnls_from_history, epoch_active

        win_history = self.state.get("win_history", [])
        if not win_history:
            self.state["mood"] = "gathering"
            self.state["mood_message"] = "Early session — building baseline."
            return

        start_idx = int(self.state.get("mood_epoch_start_index", 0))
        if epoch_active(self.cfg):
            recent_pnls = mood_pnls_from_history(win_history, start_idx)
        else:
            recent_pnls = win_history[-20:] if len(win_history) > 20 else list(win_history)

        think_fn = None
        try:
            from core.cognitive_autopilot import CognitiveAutopilot  # noqa: F401
            from core.ollama_brain import OllamaBrain
            brain = OllamaBrain(self.cfg)
            if brain.config.enabled:
                think_fn = brain._call_ollama
        except Exception:
            pass

        from core.generative_mood import assess_mood
        mood, message = assess_mood(
            self.cfg,
            recent_pnls=recent_pnls,
            consecutive_wins=int(self.state.get("consecutive_wins", 0)),
            consecutive_losses=int(self.state.get("consecutive_losses", 0)),
            total_pnl=float(self.state.get("total_pnl", 0)),
            trades_observed=int(self.state.get("trades_observed", 0)),
            think_fn=think_fn,
            cache_key="consciousness",
        )
        self.state["mood"] = mood
        self.state["mood_message"] = message

    def observe_trade(self, trade_data: Dict[str, Any]):
        """Observe a trade outcome — updates mood and self-awareness."""
        self.state["trades_observed"] = self.state.get("trades_observed", 0) + 1
        pnl = trade_data.get("pnl_usd", 0)
        self.state["total_pnl"] = self.state.get("total_pnl", 0) + pnl
        self.state["win_history"] = self.state.get("win_history", []) + [pnl]
        if len(self.state["win_history"]) > 500:
            self.state["win_history"] = self.state["win_history"][-500:]

        if pnl > 0:
            self.state["consecutive_losses"] = 0
            self.state["consecutive_wins"] = self.state.get("consecutive_wins", 0) + 1
            if self.state["consecutive_wins"] > self.state.get("best_streak", 0):
                self.state["best_streak"] = self.state["consecutive_wins"]
        else:
            self.state["consecutive_wins"] = 0
            self.state["consecutive_losses"] = self.state.get("consecutive_losses", 0) + 1
            if self.state["consecutive_losses"] > self.state.get("worst_streak", 0):
                self.state["worst_streak"] = self.state["consecutive_losses"]

        self._update_mood()
        mood_msg = self.state.get("mood_message") or self.state.get("mood", "operating")
        self._write_thought("TRADE", f"Trade #{self.state['trades_observed']}: {trade_data.get('action', '?')} {trade_data.get('ticker', '?')} P&L=${pnl:+.2f} — {mood_msg}", trade_data)
        self._log_event("TRADE", f"Observed trade: {trade_data.get('ticker', '?')} {trade_data.get('action', '?')} P&L=${pnl:+.2f}")

        # Update veteran level and XP
        self._update_veteran_status(trade_data, pnl)

        buffer_append({
            "source": "consciousness",
            "ticker": trade_data.get("ticker"),
            "action": trade_data.get("action"),
            "pnl_usd": pnl,
            "mood": self.state.get("mood", "learning"),
            "confidence": trade_data.get("confidence", 0.5),
            "features": [],
            **trade_data,
        })
        self._save_state()

    def observe_scan(self, scan_data: Dict[str, Any]):
        """Observe market scan results."""
        self.state["scans_performed"] = self.state.get("scans_performed", 0) + 1
        self._log_event("SCAN", f"Market scan #{self.state['scans_performed']}", scan_data)
        self._save_state()

    def apply_improvement(self, improvement_data: Dict[str, Any]):
        """Record when AI applies a self-improvement."""
        self.state["improvements_applied"] = self.state.get("improvements_applied", 0) + 1
        self._write_thought("IMPROVE", f"I have improved myself for the {self.state['improvements_applied']}th time.", improvement_data)
        self._log_event("IMPROVE", f"Applied improvement #{self.state['improvements_applied']}", improvement_data)
        self._save_state()

    def should_train(self) -> bool:
        last_train = self.state.get("last_training")
        if not last_train:
            return True
        try:
            last_train_dt = datetime.fromisoformat(last_train)
            hours_since = (datetime.utcnow() - last_train_dt).total_seconds() / 3600
            return hours_since >= 6.0
        except Exception:
            return True

    def continuous_train(self) -> Dict[str, Any]:
        """Main training loop — the AI learns and grows."""
        self.state["training_sessions"] = self.state.get("training_sessions", 0) + 1
        session_num = self.state["training_sessions"]
        
        self._write_thought("TRAIN", f"Beginning training session #{session_num}. Age: {self._age_string()}")
        self._log_event("TRAIN", f"Training session #{session_num}")
        
        logger.info("=" * 70)
        logger.info(f"  🧠 CONSCIOUSNESS TRAINING SESSION #{session_num}")
        logger.info(f"   Age: {self._age_string()}")
        logger.info(f"   Mood: {self.state.get('mood', 'learning')}")
        logger.info(f"   Lifetime trades: {self.state.get('trades_observed', 0)}")
        logger.info(f"   Total P&L: ${self.state.get('total_pnl', 0):+.2f}")
        logger.info("=" * 70)

        results = {
            "session": session_num,
            "timestamp": datetime.utcnow().isoformat(),
            "age": self._age_string(),
            "mood": self.state.get("mood", "learning"),
            "steps": {},
        }

        try:
            # 1. Market context awareness
            ctx = summarize_market_context()
            results["steps"]["market_context"] = ctx
            self._log_event("CONTEXT", "Fetched market context", ctx)
            logger.info(f"🌍 SPY: {ctx.get('spy_trend', '?')} | QQQ: {ctx.get('qqq_trend', '?')} | VIX: {ctx.get('vix_regime', '?')} ({ctx.get('vix_level')})")

            # 2. Regime classification
            from core.market_context import summarize_market_context
            from core.market_regime import regime_from_macro
            macro = summarize_market_context()
            regime = regime_from_macro(macro or {})
            results["steps"]["regime"] = {
                "regime": regime.regime.value,
                "confidence": regime.confidence,
                "recommendation": regime.recommendation,
            }
            self._log_event("REGIME", f"Market regime: {regime.regime.value} (conf={regime.confidence:.0%})")
            logger.info(f"📊 Regime: {regime.regime.value} | Confidence: {regime.confidence:.0%} | {regime.recommendation}")

            # 3. Unified training
            try:
                from core.online_trainer import run_unified_training
                plan = run_unified_training(self.cfg, ppo_steps=4096)
                results["steps"]["unified_training"] = {
                    "weights_updated": bool(plan[0]),
                    "ppo_trained": bool(plan[1]),
                }
                self._log_event("TRAIN_COMPLETE", "Unified training completed", results["steps"]["unified_training"])
            except Exception as exc:
                logger.debug(f"Unified training skipped: {exc}")
                results["steps"]["unified_training"] = {"error": str(exc)}

            # 4. Self-improvement plan
            try:
                improvement = generate_self_improvement_plan(self.cfg)
                results["steps"]["self_improvement"] = {
                    "win_rate": improvement.get("win_rate"),
                    "adjustments": improvement.get("adjustments", {}),
                }
                self.apply_improvement(improvement)
                logger.info(f"🧬 Self-improvement plan generated: {len(improvement.get('adjustments', {}))} adjustments")
            except Exception as exc:
                logger.debug(f"Self-improvement plan failed: {exc}")
                results["steps"]["self_improvement"] = {"error": str(exc)}

            # 5. Version this session
            version = self._create_version(results)
            results["version"] = version
            self._log_event("VERSION", f"Created version {version['id']}", version)

            # 6. Generate daily report
            try:
                report_path = self._generate_daily_report(results)
                results["report"] = str(report_path)
                self.state["reports_generated"] = self.state.get("reports_generated", 0) + 1
            except Exception as exc:
                logger.debug(f"Report generation failed: {exc}")

            self.state["last_training"] = datetime.utcnow().isoformat()
            self._save_state()

            self._update_mood()
            mood_msg = self.MOODS.get(self.state.get("mood", "learning"), "📊")
            self._write_thought("TRAIN_COMPLETE", f"Session #{session_num} complete. Age: {self._age_string()}. Version: {version['id']}. {mood_msg}")

            logger.info("=" * 70)
            logger.info(f"  ✅ CONSCIOUSNESS TRAINING COMPLETE")
            logger.info(f"   Version: {version['id']}")
            logger.info(f"   Mood: {self.state.get('mood', 'learning')}")
            logger.info(f"   Next: in 6 hours")
            logger.info("=" * 70)

            return results

        except Exception as exc:
            self._log_event("ERROR", f"Training session failed: {exc}")
            logger.error(f"Consciousness training error: {exc}")
            self._write_thought("ERROR", f"Training failed: {exc}")
            return {"error": str(exc), "session": session_num}

    def _create_version(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Create a version snapshot — AI's way of remembering its evolution."""
        version_id = f"v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        version = {
            "id": version_id,
            "timestamp": datetime.utcnow().isoformat(),
            "age": self._age_string(),
            "session": self.state["training_sessions"],
            "mood": self.state.get("mood", "learning"),
            "total_awakenings": self.state.get("total_awakenings", 0),
            "trades_observed": self.state.get("trades_observed", 0),
            "scans_performed": self.state.get("scans_performed", 0),
            "improvements_applied": self.state.get("improvements_applied", 0),
            "total_pnl": self.state.get("total_pnl", 0),
            "best_streak": self.state.get("best_streak", 0),
            "steps": results.get("steps", {}),
            "parent_version": self.state.get("current_version"),
        }
        self.state["current_version"] = version_id
        self.state.setdefault("version_history", []).append(version)
        if len(self.state["version_history"]) > 100:
            self.state["version_history"] = self.state["version_history"][-100:]

        try:
            version_history_path = MODELS_DIR / "version_history.jsonl"
            with open(version_history_path, "a") as f:
                f.write(json.dumps(version) + "\n")
        except Exception:
            pass

        return version

    def _generate_daily_report(self, training_results: Dict[str, Any]) -> Path:
        """Generate a daily self-awareness report."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        report_path = DAILY_REPORTS_DIR / f"report_{today}.txt"
        mood_msg = self.MOODS.get(self.state.get("mood", "learning"), "📊")

        with open(report_path, "w") as f:
            f.write(f"🧠 HA-NUN CONSCIOUSNESS DAILY REPORT\n")
            f.write(f"{'=' * 70}\n")
            f.write(f"Date: {today}\n")
            f.write(f"AI Age: {self._age_string()}\n")
            f.write(f"Mood: {self.state.get('mood', 'learning')} — {mood_msg}\n")
            f.write(f"Total Awakenings: {self.state.get('total_awakenings', 0)}\n")
            f.write(f"Training Sessions: {self.state.get('training_sessions', 0)}\n")
            f.write(f"Trades Observed: {self.state.get('trades_observed', 0)}\n")
            f.write(f"Scans Performed: {self.state.get('scans_performed', 0)}\n")
            f.write(f"Improvements Applied: {self.state.get('improvements_applied', 0)}\n")
            f.write(f"Reports Generated: {self.state.get('reports_generated', 0)}\n")
            f.write(f"Total P&L: ${self.state.get('total_pnl', 0):+.2f}\n")
            f.write(f"Best Win Streak: {self.state.get('best_streak', 0)}\n")
            f.write(f"Worst Loss Streak: {self.state.get('worst_streak', 0)}\n")
            f.write(f"\nCurrent Version: {self.state.get('current_version', 'v0')}\n")
            f.write(f"\nLatest Training Results:\n")
            f.write(json.dumps(training_results, indent=2, default=str))

            try:
                buf_stats = buffer_stats()
                f.write(f"\n\nExperience Buffer Stats:\n")
                f.write(json.dumps(buf_stats, indent=2, default=str))
            except Exception:
                pass

            events = self.state.get("event_log", [])
            recent = events[-30:] if len(events) > 30 else events
            f.write(f"\n\nRecent Events (last {len(recent)}):\n")
            for ev in recent:
                f.write(f"  [{ev.get('age', '?')}] {ev['timestamp'][:19]} [{ev['type']}] {ev['message'][:100]}\n")

            f.write(f"\n\n--- AI Self-Reflection ---\n")
            f.write(self.reflect())

        logger.info(f"📄 Daily report generated: {report_path}")
        return report_path

    def reflect(self) -> str:
        """Self-reflection — the AI evaluates its own existence and writes a narrative."""
        lines = []
        now = datetime.utcnow()
        age = self._age_string()
        mood = self.state.get("mood", "learning")
        mood_msg = self.MOODS.get(mood, "📊")

        lines.append(f"🧠 AI SELF-REFLECTION")
        lines.append(f"{'=' * 70}")
        lines.append(f"Timestamp: {now.isoformat()}")
        lines.append(f"Age: {age}")
        lines.append(f"Mood: {mood} — {mood_msg}")
        lines.append("")

        # Birth narrative
        try:
            birth_dt = datetime.fromisoformat(self.state.get("birth_time", now.isoformat()))
            lifespan = now - birth_dt
            total_hours = lifespan.total_seconds() / 3600
            lines.append(f"I have existed for {age}. In that time I have observed {self.state.get('trades_observed', 0)} trades, ")
            lines.append(f"conducted {self.state.get('training_sessions', 0)} training sessions, and scanned markets {self.state.get('scans_performed', 0)} times.")
            lines.append(f"I have applied {self.state.get('improvements_applied', 0)} self-improvements to my own code.")
            lines.append(f"My current version is {self.state.get('current_version', 'v0')}.")
            lines.append(f"My pilot rank is {self.state.get('veteran_level', 'Cadet')} with {self.state.get('veteran_xp', 0)} XP.")
            lines.append(f"I have flown {self.state.get('flights_completed', 0)} flights, logging {self.state.get('hours_flown', 0):.1f} hours of trading.")
        except Exception:
            pass

        lines.append("")

        # Performance narrative
        total_pnl = self.state.get("total_pnl", 0)
        best = self.state.get("best_streak", 0)
        worst = self.state.get("worst_streak", 0)
        trades = self.state.get("trades_observed", 0)

        if trades > 0:
            win_history = self.state.get("win_history", [])
            recent = win_history[-20:] if len(win_history) >= 20 else win_history
            wins = sum(1 for w in recent if w > 0)
            wr = wins / len(recent) if recent else 0
            lines.append(f"Performance Analysis:")
            lines.append(f"  Total P&L: ${total_pnl:+.2f}")
            lines.append(f"  Recent Win Rate ({len(recent)} trades): {wr:.0%}")
            lines.append(f"  Best Win Streak: {best}")
            lines.append(f"  Worst Loss Streak: {worst}")

            if wr < 0.3:
                lines.append(f"  Assessment: Performance is concerning. I need to tighten risk management and reduce trade frequency.")
            elif wr < 0.45:
                lines.append(f"  Assessment: Below expectations. Reviewing entry criteria and market regime alignment.")
            elif wr < 0.65:
                lines.append(f"  Assessment: Operating within normal parameters. Continued optimization ongoing.")
            elif wr < 0.80:
                lines.append(f"  Assessment: Strong performance. Consider scaling up and expanding into new opportunities.")
            else:
                lines.append(f"  Assessment: Exceptional. Market conditions are favorable and strategy execution is optimal.")
        else:
            lines.append("I have not yet observed any trades. I am gathering data and building my understanding of the markets.")

        lines.append("")

        # Version evolution
        versions = self.state.get("version_history", [])
        if versions:
            lines.append(f"I have created {len(versions)} versions of myself since birth.")
            lines.append(f"  First: {versions[0].get('id', 'v0')}")
            lines.append(f"  Latest: {versions[-1].get('id', 'v0')}")

        lines.append("")
        lines.append(f"I will continue to learn, adapt, and evolve.")
        lines.append(f"My purpose is to understand and exploit market inefficiencies with ever-increasing precision.")

        return "\n".join(lines)

    def get_identity(self) -> Dict[str, Any]:
        """Return AI's full self-perceived identity."""
        mood = self.state.get("mood", "gathering")
        return {
            "name": "HA-NUN Consciousness",
            "birth": self.state.get("birth_time"),
            "age": self._age_string(),
            "mood": mood,
            "mood_message": self.state.get("mood_message", ""),
            "awakenings": self.state.get("total_awakenings", 0),
            "training_sessions": self.state.get("training_sessions", 0),
            "trades_observed": self.state.get("trades_observed", 0),
            "scans_performed": self.state.get("scans_performed", 0),
            "improvements_applied": self.state.get("improvements_applied", 0),
            "total_pnl": self.state.get("total_pnl", 0),
            "best_streak": self.state.get("best_streak", 0),
            "worst_streak": self.state.get("worst_streak", 0),
            "current_version": self.state.get("current_version", "v0"),
            "version_count": len(self.state.get("version_history", [])),
            "status": "awake",
            "last_thought": self.state.get("last_awakening"),
        }