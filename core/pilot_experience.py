#!/usr/bin/env python3
"""
core/pilot_experience.py — Pilot Experience & Veteran Progression System.

The AI acts as a pilot gaining flight experience. Each trade is a "flight" that
contributes to total experience points (XP). As XP accumulates, the AI levels up
through veteran ranks, unlocking more sophisticated strategies and confidence.

VETERAN LEVELS:
- Cadet (0-99 XP): Learning basics, conservative trading
- Rookie (100-499 XP): Basic skills, limited strategy access
- Aviator (500-1,999 XP): Competent pilot, full strategy access
- Ace (2,000-9,999 XP): Elite trader, can adjust advanced parameters
- Veteran (10,000+ XP): Master trader, can modify core strategies

XP SOURCES:
- Trade completed: +10 XP
- Winning trade: +20 XP (total +30)
- Losing trade (small loss): +5 XP (learning from mistakes)
- Losing trade (large loss): +15 XP (hard lessons)
- Perfect win streak (5+): +50 XP bonus
- Market regime correctly identified: +1 XP per bar
- Pattern match successful: +25 XP
- Pattern match avoided loss: +30 XP
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

import numpy as np

from core.config import BotConfig
from core.git_sync import push_learning_checkpoint_async

logger = logging.getLogger("PILOT")

MODELS_DIR = Path("models")
EXPERIENCE_PATH = MODELS_DIR / "pilot_experience.json"
FLIGHT_LOG_PATH = MODELS_DIR / "flight_log.jsonl"


@dataclass
class FlightRecord:
    """A single flight (trade) record."""
    ticker: str
    entry_time: str
    exit_time: Optional[str] = None
    action: str = "HOLD"
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    confidence: float = 0.0
    regime: str = ""
    vix_level: float = 0.0
    patterns_matched: List[str] = field(default_factory=list)
    xp_gained: int = 0
    notes: str = ""


@dataclass
class PilotState:
    """Pilot's accumulated experience state."""
    total_xp: int = 0
    level: str = "Cadet"
    flights_completed: int = 0
    flights_flown: int = 0
    takeoffs: int = 0
    landings: int = 0
    hours_flown: float = 0.0
    sectors_flown: Dict[str, int] = field(default_factory=dict)
    aircraft_type: str = "PPO-Transformer-LSTM"
    license_class: str = "Basic"
    certifications: Dict[str, bool] = field(default_factory=dict)
    skill_points: Dict[str, int] = field(default_factory=lambda: {
        "entry_timing": 0,
        "exit_timing": 0,
        "risk_management": 0,
        "regime_recognition": 0,
        "pattern_recognition": 0,
        "confidence_judgment": 0,
    })


VETERAN_LEVELS = [
    ("Cadet", 0, 99, "Learning basics, conservative trading"),
    ("Rookie", 100, 499, "Basic skills, limited strategy access"),
    ("Aviator", 500, 1999, "Competent pilot, full strategy access"),
    ("Ace", 2000, 9999, "Elite trader, can adjust advanced parameters"),
    ("Veteran", 10000, float('inf'), "Master trader, can modify core strategies"),
]


class PilotExperienceSystem:
    """
    Pilot experience tracker that assigns XP, levels, and unlocks capabilities.
    """
    
    XP_PER_TRADE = 10
    XP_PER_WIN = 20
    XP_PER_SMALL_LOSS = 5
    XP_PER_LARGE_LOSS = 15
    XP_PER_REGIME_CORRECT = 1
    XP_PER_PATTERN_WIN = 25
    XP_PER_PATTERN_AVOIDANCE = 30
    XP_PERFECT_STREAK_BONUS = 50
    SMALL_LOSS_THRESHOLD = 30.0
    LARGE_LOSS_THRESHOLD = 75.0
    
    REGIME_MULTIPLIERS = {
        "TRENDING_UP": 1.2,
        "TRENDING_DOWN": 1.1,
        "HIGH_VOLATILITY": 1.3,
        "LOW_VOLATILITY": 0.9,
        "RANGING": 1.0,
        "BREAKOUT": 1.5,
    }

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.state = self._load_state()
        self._current_flight: Optional[FlightRecord] = None
        logger.info(f"Pilot Experience System initialized | Level: {self.state.level} | XP: {self.state.total_xp}")
    
    def _load_state(self) -> PilotState:
        if EXPERIENCE_PATH.exists():
            try:
                with open(EXPERIENCE_PATH, "r") as f:
                    data = json.load(f)
                return PilotState(**data)
            except Exception:
                pass
        return PilotState()
    
    def _save_state(self):
        EXPERIENCE_PATH.parent.mkdir(exist_ok=True)
        with open(EXPERIENCE_PATH, "w") as f:
            json.dump(asdict(self.state), f, indent=2)
    
    def _save_flight_log(self, flight: FlightRecord):
        EXPERIENCE_PATH.parent.mkdir(exist_ok=True)
        with open(FLIGHT_LOG_PATH, "a") as f:
            f.write(json.dumps(asdict(flight)) + "\n")
    
    def _calculate_level(self, total_xp: int) -> str:
        for level, min_xp, max_xp, _ in VETERAN_LEVELS:
            if min_xp <= total_xp <= max_xp:
                return level
        return "Veteran"
    
    def _determine_skill_focus(self, pnl_usd: float, pnl_pct: float, regime_result) -> List[str]:
        """Determine which skills to improve based on trade outcome."""
        focus = []
        if pnl_usd > 0:
            focus.append("entry_timing")
        else:
            focus.append("exit_timing")
            if pnl_usd < -self.LARGE_LOSS_THRESHOLD:
                focus.append("risk_management")
        if regime_result and hasattr(regime_result, 'regime'):
            regime = getattr(getattr(regime_result, 'regime', None), 'value', str(regime_result.regime))
            if regime in ("TRENDING_UP", "TRENDING_DOWN", "BREAKOUT"):
                focus.append("regime_recognition")
        return focus

    def start_flight(self, ticker: str, entry_price: float, regime_result=None, confidence: float = 0.5, patterns: List[str] = None, vix_level: float = 0.0) -> str:
        """Begin tracking a new flight."""
        self.state.takeoffs += 1
        self.state.flights_flown += 1

        regime_val = ""
        if regime_result and hasattr(regime_result, 'regime'):
            regime_val = getattr(getattr(regime_result, 'regime', None), 'value', str(regime_result.regime))

        self._current_flight = FlightRecord(
            ticker=ticker,
            entry_time=datetime.utcnow().isoformat(),
            entry_price=entry_price,
            regime=regime_val,
            vix_level=vix_level,
            confidence=confidence,
            patterns_matched=patterns or [],
        )
        logger.info(f"✈️ FLIGHT START: {ticker} @ ${entry_price:.2f} | Regime: {regime_val or 'COMPUTING'} | VIX: {vix_level:.1f}")
        return f"Flight started for {ticker}"
    
    def complete_flight(self, exit_price: float, pnl_usd: float, pnl_pct: float, notes: str = "") -> Dict[str, Any]:
        """Complete a flight and award XP."""
        if not self._current_flight:
            return {"xp_gained": 0, "new_level": self.state.level}
        
        self._current_flight.exit_time = datetime.utcnow().isoformat()
        self._current_flight.exit_price = exit_price
        self._current_flight.pnl_usd = pnl_usd
        self._current_flight.pnl_pct = pnl_pct
        self._current_flight.notes = notes
        
        xp = self.XP_PER_TRADE
        
        if pnl_usd > 0:
            xp += self.XP_PER_WIN
            self._current_flight.action = "WIN"
        elif pnl_usd < -self.LARGE_LOSS_THRESHOLD:
            xp += self.XP_PER_LARGE_LOSS
            self._current_flight.action = "CRASH_LANDING"
        elif pnl_usd < -self.SMALL_LOSS_THRESHOLD:
            xp += self.XP_PER_SMALL_LOSS
            self._current_flight.action = "SOFT_LANDING"
        else:
            self._current_flight.action = "NEUTRAL"
        
        regime_mult = self.REGIME_MULTIPLIERS.get(self._current_flight.regime, 1.0)
        xp = int(xp * regime_mult)
        
        for pattern in self._current_flight.patterns_matched:
            if pnl_usd > 0:
                xp += self.XP_PER_PATTERN_WIN
            else:
                xp += self.XP_PER_PATTERN_AVOIDANCE
        
        self._current_flight.xp_gained = xp
        self._save_flight_log(self._current_flight)
        
        self.state.total_xp += xp
        self.state.flights_completed += 1
        self.state.landings += 1
        self.state.hours_flown += 0.1
        
        sector = self._current_flight.ticker[:3].upper()
        self.state.sectors_flown[sector] = self.state.sectors_flown.get(sector, 0) + 1
        
        old_level = self.state.level
        self.state.level = self._calculate_level(self.state.total_xp)
        
        skill_focus = self._determine_skill_focus(pnl_usd, pnl_pct, self._current_flight.regime)
        for skill in skill_focus:
            self.state.skill_points[skill] = min(100, self.state.skill_points.get(skill, 0) + 1)
        
        self._save_state()
        
        result = {
            "xp_gained": xp,
            "total_xp": self.state.total_xp,
            "new_level": self.state.level,
            "level_up": old_level != self.state.level,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
        }
        
        if result["level_up"]:
            logger.info(f"🎖 LEVEL UP: {old_level} → {self.state.level} ({self.state.total_xp} XP)")
        else:
            logger.info(f"✈️ FLIGHT COMPLETE: {self._current_flight.ticker} | XP: +{xp} | Total: {self.state.total_xp}")
        
        self._current_flight = None
        return result
    
    def get_confidence_threshold(self) -> float:
        """Confidence threshold by veteran level — veterans act faster."""
        level = self.state.level
        thresholds = {
            "Cadet": 0.58,
            "Rookie": 0.54,
            "Aviator": 0.50,
            "Ace": 0.46,
            "Veteran": 0.42,
        }
        return thresholds.get(level, 0.52)
    
    def get_max_position_size(self) -> float:
        """Get position size multiplier based on level."""
        level = self.state.level
        multipliers = {
            "Cadet": 0.5,
            "Rookie": 0.75,
            "Aviator": 1.0,
            "Ace": 1.5,
            "Veteran": 2.0,
        }
        return multipliers.get(level, 1.0)
    
    def can_modify_strategy(self) -> bool:
        """Check if pilot can modify core strategies."""
        return self.state.level in ("Ace", "Veteran")
    
    def get_skill_modifiers(self) -> Dict[str, float]:
        """Get multipliers for different skills."""
        modifiers = {}
        for skill, points in self.state.skill_points.items():
            modifiers[skill] = 1.0 + (points / 100.0) * 0.5
        return modifiers
    
    def get_veteran_status(self) -> Dict[str, Any]:
        """Return full veteran status for reporting."""
        win_rate = self._calculate_win_rate()
        return {
            "level": self.state.level,
            "total_xp": self.state.total_xp,
            "flights_flown": self.state.flights_flown,
            "flights_completed": self.state.flights_completed,
            "hours_flown": round(self.state.hours_flown, 1),
            "takeoffs": self.state.takeoffs,
            "landings": self.state.landings,
            "win_rate": round(win_rate, 3),
            "skill_points": self.state.skill_points.copy(),
            "confidence_threshold": self.get_confidence_threshold(),
            "max_position_multiplier": self.get_max_position_size(),
            "can_modify_strategy": self.can_modify_strategy(),
            "sectors_flown": list(self.state.sectors_flown.keys())[:5],
        }
    
    def _calculate_win_rate(self) -> float:
        if not FLIGHT_LOG_PATH.exists():
            return 0.5
        try:
            wins = 0
            total = 0
            with open(FLIGHT_LOG_PATH, "r") as f:
                for line in f:
                    if line.strip():
                        record = json.loads(line)
                        if record.get("pnl_usd", 0) > 0:
                            wins += 1
                        total += 1
            return wins / max(total, 1)
        except Exception:
            return 0.5
    
    def record_regime_recognition(self, correct: bool):
        """Record regime identification for XP."""
        if correct:
            self.state.total_xp += self.XP_PER_REGIME_CORRECT
            self.state.skill_points["regime_recognition"] = min(100, self.state.skill_points["regime_recognition"] + 1)
            self._save_state()
    
    def record_pattern_match(self, pattern_type: str, success: bool, pnl_usd: float = 0):
        """Record pattern match for XP."""
        if success:
            self.state.total_xp += self.XP_PER_PATTERN_WIN
            self.state.skill_points["pattern_recognition"] = min(100, self.state.skill_points["pattern_recognition"] + 2)
        else:
            self.state.total_xp += self.XP_PER_PATTERN_AVOIDANCE
            self.state.skill_points["pattern_recognition"] = min(100, self.state.skill_points["pattern_recognition"] + 1)
        self._save_state()


def pilot_experience_to_git(pilot: PilotExperienceSystem):
    """Push pilot experience to GitHub."""
    status = pilot.get_veteran_status()
    push_learning_checkpoint_async(
        f"pilot {status['level']} {status['total_xp']}XP WR={status.get('win_rate', 0):.0%}"
    )