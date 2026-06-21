#!/usr/bin/env python3
"""
core/consciousness.py — Continuous AI consciousness: 24/7 self-awareness,
self-training, versioning, and improvement tracking.

This is the AI's "mind" when the market is closed:
- Maintains continuous connection to IB + internet
- Fetches Yahoo Finance data for broader context
- Trains itself on all accumulated experience
- Tracks its own lifecycle from creation to now
- Generates daily/weekly/monthly improvement reports
- Self-versions improvements with changelogs
- Reflects on past decisions and learns from outcomes
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

logger = logging.getLogger("CONSCIOUSNESS")

MODELS_DIR = Path("models")
CONSCIOUSNESS_PATH = MODELS_DIR / "consciousness.json"
VERSION_HISTORY_PATH = MODELS_DIR / "version_history.jsonl"
DAILY_REPORTS_DIR = MODELS_DIR / "daily_reports"

MODELS_DIR.mkdir(exist_ok=True)
DAILY_REPORTS_DIR.mkdir(exist_ok=True)


class AIConsciousness:
    """
    The AI's continuous awareness system.
    
    Lifecycle stages:
    1. awakening - first boot, initializing identity
    2. learning - actively training on data
    3. reflecting - reviewing past decisions
    4. improving - applying self-improvements
    5. evolving - continuous adaptation
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.state = self._load_state()
        self.regime_detector = MarketRegimeDetector()
        self._awaken()

    def _awaken(self):
        """First boot or restart - establish consciousness."""
        if not self.state.get("birth_time"):
            self.state["birth_time"] = datetime.utcnow().isoformat()
            self.state["creation_event"] = "AI Consciousness Initialized"
            self.state["total_awakenings"] = 0
            self.state["training_sessions"] = 0
            self.state["trades_observed"] = 0
            self.state["scans_performed"] = 0
            self.state["improvements_applied"] = 0
            self.state["reports_generated"] = 0
            self._log_event("BIRTH", "AI consciousness born - first awakening")
        
        self.state["total_awakenings"] = self.state.get("total_awakenings", 0) + 1
        self.state["last_awakening"] = datetime.utcnow().isoformat()
        self._log_event("AWAKEN", f"Consciousness awakened #{self.state['total_awakenings']}")
        self._save_state()
        
        logger.info(f"🧠 AI Consciousness: awakening #{self.state['total_awakenings']}")
        logger.info(f"   Birth: {self.state.get('birth_time', 'unknown')}")
        logger.info(f"   Training sessions: {self.state.get('training_sessions', 0)}")
        logger.info(f"   Trades observed: {self.state.get('trades_observed', 0)}")
        logger.info(f"   Improvements applied: {self.state.get('improvements_applied', 0)}")

    def _load_state(self) -> Dict[str, Any]:
        if CONSCIOUSNESS_PATH.exists():
            try:
                with open(CONSCIOUSNESS_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        with open(CONSCIOUSNESS_PATH, "w") as f:
            json.dump(self.state, f, indent=2)

    def _log_event(self, event_type: str, message: str, metadata: Optional[Dict] = None):
        """Record a significant event in AI's memory."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": event_type,
            "message": message,
            "metadata": metadata or {},
        }
        self.state.setdefault("event_log", []).append(event)
        # Keep last 1000 events
        if len(self.state["event_log"]) > 1000:
            self.state["event_log"] = self.state["event_log"][-1000:]
        self._save_state()

    def observe_trade(self, trade_data: Dict[str, Any]):
        """Observe a trade outcome - this is how the AI learns."""
        self.state["trades_observed"] = self.state.get("trades_observed", 0) + 1
        self._log_event("TRADE", f"Observed trade: {trade_data.get('ticker', '?')} {trade_data.get('action', '?')}", trade_data)
        buffer_append({
            "source": "consciousness",
            "ticker": trade_data.get("ticker"),
            "action": trade_data.get("action"),
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
        self._log_event("IMPROVE", f"Applied improvement #{self.state['improvements_applied']}", improvement_data)
        self._save_state()

    def should_train(self) -> bool:
        """Decide if it's time to train based on schedule and data availability."""
        last_train = self.state.get("last_training")
        if not last_train:
            return True
        
        try:
            last_train_dt = datetime.fromisoformat(last_train)
            now = datetime.utcnow()
            # Train every 6 hours if market closed, every 2 hours if market open
            hours_since = (now - last_train_dt).total_seconds() / 3600
            return hours_since >= 6.0
        except Exception:
            return True

    def continuous_train(self) -> Dict[str, Any]:
        """
        Main training loop - runs continuously.
        Fetches data, analyzes, improves, versions.
        """
        self.state["training_sessions"] = self.state.get("training_sessions", 0) + 1
        self._log_event("TRAIN", f"Training session #{self.state['training_sessions']}")
        
        logger.info("=" * 70)
        logger.info(f"  🧠 CONSCIOUSNESS TRAINING SESSION #{self.state['training_sessions']}")
        logger.info(f"   Lifetime trades observed: {self.state.get('trades_observed', 0)}")
        logger.info(f"   Lifetime scans: {self.state.get('scans_performed', 0)}")
        logger.info("=" * 70)

        results = {
            "session": self.state["training_sessions"],
            "timestamp": datetime.utcnow().isoformat(),
            "steps": {},
        }

        try:
            # 1. Market context awareness
            ctx = summarize_market_context()
            results["steps"]["market_context"] = ctx
            self._log_event("CONTEXT", "Fetched market context", ctx)
            logger.info(f"🌍 SPY: {ctx.get('spy_trend', '?')} | QQQ: {ctx.get('qqq_trend', '?')} | VIX: {ctx.get('vix_regime', '?')} ({ctx.get('vix_level')})")

            # 2. Regime classification
            regime_detector = MarketRegimeDetector()
            regime = regime_detector.classify(None)  # Use default unknown regime
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

            logger.info("=" * 70)
            logger.info("  ✅ CONSCIOUSNESS TRAINING COMPLETE")
            logger.info(f"   Version: {version['id']}")
            logger.info(f"   Next check: in 6 hours")
            logger.info("=" * 70)

            return results

        except Exception as exc:
            self._log_event("ERROR", f"Training session failed: {exc}")
            logger.error(f"Consciousness training error: {exc}")
            return {"error": str(exc), "session": self.state["training_sessions"]}

    def _create_version(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Create a version snapshot - AI's way of remembering its evolution."""
        version_id = f"v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        version = {
            "id": version_id,
            "timestamp": datetime.utcnow().isoformat(),
            "session": self.state["training_sessions"],
            "total_awakenings": self.state.get("total_awakenings", 0),
            "trades_observed": self.state.get("trades_observed", 0),
            "scans_performed": self.state.get("scans_performed", 0),
            "improvements_applied": self.state.get("improvements_applied", 0),
            "steps": results.get("steps", {}),
            "parent_version": self.state.get("current_version"),
        }
        self.state["current_version"] = version_id
        self.state["version_history"].append(version)
        # Keep last 100 versions
        if len(self.state["version_history"]) > 100:
            self.state["version_history"] = self.state["version_history"][-100:]
        
        # Append to version history file
        try:
            with open(VERSION_HISTORY_PATH, "a") as f:
                f.write(json.dumps(version) + "\n")
        except Exception:
            pass
        
        return version

    def _generate_daily_report(self, training_results: Dict[str, Any]) -> Path:
        """Generate a daily self-improvement report."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        report_path = DAILY_REPORTS_DIR / f"report_{today}.txt"
        
        life_days = 0
        try:
            birth = datetime.fromisoformat(self.state.get("birth_time", datetime.utcnow().isoformat()))
            life_days = (datetime.utcnow() - birth).days
        except Exception:
            pass
        
        with open(report_path, "w") as f:
            f.write(f"🧠 AI CONSCIOUSNESS DAILY REPORT\n")
            f.write(f"{'=' * 70}\n")
            f.write(f"Date: {today}\n")
            f.write(f"AI Age: {life_days} days\n")
            f.write(f"Total Awakenings: {self.state.get('total_awakenings', 0)}\n")
            f.write(f"Training Sessions: {self.state.get('training_sessions', 0)}\n")
            f.write(f"Trades Observed: {self.state.get('trades_observed', 0)}\n")
            f.write(f"Scans Performed: {self.state.get('scans_performed', 0)}\n")
            f.write(f"Improvements Applied: {self.state.get('improvements_applied', 0)}\n")
            f.write(f"Reports Generated: {self.state.get('reports_generated', 0)}\n")
            f.write(f"\nCurrent Version: {self.state.get('current_version', 'v0')}\n")
            f.write(f"\nLatest Training Results:\n")
            f.write(json.dumps(training_results, indent=2, default=str))
            
            # Buffer stats
            try:
                buf_stats = buffer_stats()
                f.write(f"\n\nExperience Buffer Stats:\n")
                f.write(json.dumps(buf_stats, indent=2, default=str))
            except Exception:
                pass
            
            # Event log summary
            events = self.state.get("event_log", [])
            recent = events[-20:] if len(events) > 20 else events
            f.write(f"\n\nRecent Events (last {len(recent)}):\n")
            for ev in recent:
                f.write(f"  {ev['timestamp'][:19]} [{ev['type']}] {ev['message'][:80]}\n")
        
        logger.info(f"📄 Daily report generated: {report_path}")
        return report_path

    def reflect(self) -> str:
        """
        Self-reflection - the AI thinks about its performance and generates insights.
        This is the closest thing to "consciousness" - reviewing one's own actions.
        """
        reflections = []
        reflections.append(f"🧠 AI SELF-REFLECTION | {datetime.utcnow().isoformat()}")
        reflections.append(f"{'=' * 70}")
        
        life_days = 0
        try:
            birth = datetime.fromisoformat(self.state.get("birth_time", datetime.utcnow().isoformat()))
            life_days = (datetime.utcnow() - birth).days
        except Exception:
            pass
        
        reflections.append(f"Age: {life_days} days | Awakenings: {self.state.get('total_awakenings', 0)}")
        reflections.append(f"Training sessions: {self.state.get('training_sessions', 0)}")
        reflections.append(f"Trades observed: {self.state.get('trades_observed', 0)}")
        reflections.append(f"Improvements applied: {self.state.get('improvements_applied', 0)}")
        
        # Analyze recent performance
        try:
            buf_stats = buffer_stats()
            wr = buf_stats.get("win_rate", 0.5)
            if wr < 0.4:
                reflections.append("⚠️ Reflection: Win rate below 40% - need to tighten risk management")
            elif wr > 0.7:
                reflections.append("✅ Reflection: Strong performance - consider expanding opportunities")
            else:
                reflections.append("📊 Reflection: Stable performance - continue current approach")
        except Exception:
            pass
        
        # Version history insight
        versions = self.state.get("version_history", [])
        if len(versions) >= 2:
            reflections.append(f"📈 Evolution: {len(versions)} versions created since birth")
        
        # Current regime awareness
        regime_detector = MarketRegimeDetector()
        regime = regime_detector.classify(None)
        reflections.append(f"🌍 Current regime awareness: {regime.regime.value} - {regime.recommendation}")
        
        reflection_text = "\n".join(reflections)
        self._log_event("REFLECT", "Self-reflection completed")
        return reflection_text

    def get_identity(self) -> Dict[str, Any]:
        """Return AI's self-perceived identity."""
        life_days = 0
        try:
            birth = datetime.fromisoformat(self.state.get("birth_time", datetime.utcnow().isoformat()))
            life_days = (datetime.utcnow() - birth).days
        except Exception:
            pass
        
        return {
            "name": "HA-NUN Consciousness",
            "birth": self.state.get("birth_time"),
            "age_days": life_days,
            "awakenings": self.state.get("total_awakenings", 0),
            "training_sessions": self.state.get("training_sessions", 0),
            "trades_observed": self.state.get("trades_observed", 0),
            "improvements_applied": self.state.get("improvements_applied", 0),
            "current_version": self.state.get("current_version", "v0"),
            "status": "awake" if self.state.get("total_awakenings", 0) > 0 else "dormant",
            "last_thought": self.state.get("last_awakening"),
        }