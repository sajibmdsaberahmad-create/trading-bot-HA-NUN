#!/usr/bin/env python3
"""
core/pattern_memory_bank.py — Pattern Memory Bank for the AI Pilot.

Stores successful and failed trading patterns as templates the AI can reference.
Patterns are indexed by market conditions, sector, time-of-day, and outcome.
Like a veteran pilot remembering "this setup looks familiar" during flight.
"""

import json
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict

import numpy as np

from core.config import BotConfig
from core.git_sync import push_learning_checkpoint_async

logger = logging.getLogger("PATTERNS")

MODELS_DIR = Path("models")
PATTERNS_PATH = MODELS_DIR / "pattern_memory_bank.json"
PATTERN_SNAPS_PATH = MODELS_DIR / "pattern_snapshots.jsonl"


@dataclass
class PatternTemplate:
    """A stored pattern template for future reference."""
    pattern_id: str
    ticker: str
    timestamp: str
    regime: str
    pattern_type: str
    features: Dict[str, float]
    outcome: str
    pnl_usd: float
    pnl_pct: float
    confidence: float
    entry_condition: str
    exit_condition: str
    similarity_hash: str = ""
    times_seen: int = 1
    times_successful: int = 0
    avg_return: float = 0.0
    tags: List[str] = field(default_factory=list)


class PatternMemoryBank:
    """
    Stores and retrieves trading patterns like a pilot's flight memory.
    Patterns can be matched against current market conditions.
    """
    
    PATTERN_TYPES = [
        "morning_momentum",
        "lunch_dip_recovery",
        "power_hour_spike",
        "breakout_pullback",
        "high_volatility_squeeze",
        "institutional_accumulation",
        "distribution_exit",
        "mean_reversion_trap",
    ]
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.patterns: Dict[str, PatternTemplate] = {}
        self._by_sector: Dict[str, List[str]] = defaultdict(list)
        self._by_regime: Dict[str, List[str]] = defaultdict(list)
        self._by_pattern_type: Dict[str, List[str]] = defaultdict(list)
        self._load_patterns()
    
    def _load_patterns(self):
        if PATTERNS_PATH.exists():
            try:
                with open(PATTERNS_PATH, "r") as f:
                    data = json.load(f)
                for pid, pdata in data.items():
                    pattern = PatternTemplate(**pdata)
                    self.patterns[pid] = pattern
                    self._by_sector[pdata["ticker"][:3]].append(pid)
                    self._by_regime[pdata["regime"]].append(pid)
                    self._by_pattern_type[pdata["pattern_type"]].append(pid)
            except Exception as exc:
                logger.warning(f"Could not load patterns: {exc}")
    
    def _save_patterns(self):
        PATTERNS_PATH.parent.mkdir(exist_ok=True)
        with open(PATTERNS_PATH, "w") as f:
            json.dump({pid: asdict(p) for pid, p in self.patterns.items()}, f, indent=2)
    
    def _save_pattern_snapshot(self, pattern: PatternTemplate):
        """Save pattern to incremental log for GitHub tracking."""
        PATTERNS_PATH.parent.mkdir(exist_ok=True)
        with open(PATTERN_SNAPS_PATH, "a") as f:
            f.write(json.dumps(asdict(pattern)) + "\n")
    
    def _compute_similarity_hash(self, features: Dict[str, float]) -> str:
        """Create hash for pattern matching."""
        key_items = sorted([(k, round(v, 4)) for k, v in features.items() if not np.isnan(v)])
        key_str = json.dumps(key_items)
        return hashlib.md5(key_str.encode()).hexdigest()[:12]
    
    def store_pattern(self, ticker: str, regime: str, pattern_type: str,
                    features: Dict[str, float], outcome: str, pnl_usd: float,
                    pnl_pct: float, confidence: float, entry_condition: str,
                    exit_condition: str, tags: List[str] = None) -> str:
        """
        Store a new pattern or update existing one.
        Returns pattern_id.
        """
        sim_hash = self._compute_similarity_hash(features)
        
        existing_id = None
        for pid, p in self.patterns.items():
            if p.similarity_hash == sim_hash and p.pattern_type == pattern_type:
                existing_id = pid
                break
        
        if existing_id:
            pattern = self.patterns[existing_id]
            pattern.times_seen += 1
            if outcome == "win":
                pattern.times_successful += 1
            pattern.avg_return = (pattern.avg_return * (pattern.times_seen - 1) + pnl_pct) / pattern.times_seen
            pattern.tags = list(set(pattern.tags + (tags or [])))
            pattern.timestamp = datetime.utcnow().isoformat()
            logger.info(f"Pattern updated: {existing_id} | seen={pattern.times_seen} | win_rate={pattern.times_successful / pattern.times_seen:.0%}")
        else:
            pattern_id = f"pt_{pattern_type[:3]}_{len(self.patterns) + 1:04d}"
            pattern = PatternTemplate(
                pattern_id=pattern_id,
                ticker=ticker,
                timestamp=datetime.utcnow().isoformat(),
                regime=regime,
                pattern_type=pattern_type,
                features=features,
                outcome=outcome,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                confidence=confidence,
                entry_condition=entry_condition,
                exit_condition=exit_condition,
                similarity_hash=sim_hash,
                times_successful=1 if outcome == "win" else 0,
                avg_return=pnl_pct,
                tags=tags or [],
            )
            self.patterns[pattern_id] = pattern
            self._by_sector[ticker[:3]].append(pattern_id)
            self._by_regime[regime].append(pattern_id)
            self._by_pattern_type[pattern_type].append(pattern_id)
            logger.info(f"Pattern stored: {pattern_id} | type={pattern_type} | outcome={outcome}")
        
        self._save_patterns()
        self._save_pattern_snapshot(pattern)
        
        return pattern_id
    
    def find_similar_patterns(self, features: Dict[str, float], regime: str,
                             top_n: int = 5) -> List[PatternTemplate]:
        """
        Find patterns similar to current market conditions.
        Returns top N patterns sorted by success rate.
        """
        candidates = []
        
        for pid in self._by_regime.get(regime, []):
            pattern = self.patterns[pid]
            similarity = self._compute_feature_similarity(features, pattern.features)
            if similarity > 0.7:
                win_rate = pattern.times_successful / max(pattern.times_seen, 1)
                candidates.append((pattern, similarity, win_rate))
        
        for pid in self._by_pattern_type.values():
            for _pid in pid:
                if _pid not in [p[0].pattern_id for p in candidates]:
                    pattern = self.patterns[_pid]
                    similarity = self._compute_feature_similarity(features, pattern.features)
                    if similarity > 0.5:
                        win_rate = pattern.times_successful / max(pattern.times_seen, 1)
                        candidates.append((pattern, similarity, win_rate))
        
        candidates.sort(key=lambda x: x[2], reverse=True)
        return [p[0] for p in candidates[:top_n]]
    
    def _compute_feature_similarity(self, f1: Dict[str, float], f2: Dict[str, float]) -> float:
        """Compute similarity between two feature sets."""
        common_keys = set(f1.keys()) & set(f2.keys())
        if not common_keys:
            return 0.0
        
        diffs = []
        for k in common_keys:
            v1, v2 = f1.get(k, 0), f2.get(k, 0)
            if v1 != v2:
                diffs.append(abs(v1 - v2) / max(abs(v1), abs(v2), 1e-6))
        
        if not diffs:
            return 1.0
        return 1.0 - min(1.0, np.mean(diffs))
    
    def get_pattern_recommendation(self, features: Dict[str, float], regime: str,
                                  current_confidence: float) -> Tuple[bool, float, str]:
        """
        Get recommendation based on similar patterns.
        Returns (should_trade, confidence_modifier, reason).
        """
        similar = self.find_similar_patterns(features, regime, top_n=10)
        
        if not similar:
            return True, 0.0, "No prior patterns — first flight"
        
        win_rates = [p.times_successful / max(p.times_seen, 1) for p in similar]
        avg_win_rate = np.mean(win_rates)
        
        if avg_win_rate > 0.7:
            return True, 0.1, f"Strong pattern match: {avg_win_rate:.0%} win rate"
        elif avg_win_rate > 0.5:
            return True, 0.0, f"Pattern match with positive expectation"
        elif avg_win_rate < 0.3:
            return False, -0.2, f"Pattern warning: {avg_win_rate:.0%} win rate on similar setups"
        else:
            return True, -0.1, f"Pattern caution: {avg_win_rate:.0%} win rate on similar setups"
    
    def get_familiar_setup_warning(self, ticker: str) -> Optional[str]:
        """Check if we've seen this ticker/setup before and warn."""
        sector = ticker[:3]
        familiar = [self.patterns[pid] for pid in self._by_sector.get(sector, [])]
        
        if len(familiar) >= 10:
            win_rate = sum(1 for p in familiar if p.outcome == "win") / len(familiar)
            if win_rate > 0.6:
                return f"SECTOR FAMILIAR: {sector} | {len(familiar)} past trades | {win_rate:.0%} win rate"
            elif win_rate < 0.3:
                return f"CAUTION: {sector} | {len(familiar)} past trades | {win_rate:.0%} risky sector"
        return None
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Return pattern memory statistics."""
        total = len(self.patterns)
        wins = sum(1 for p in self.patterns.values() if p.outcome == "win")
        by_type = {pt: len([p for p in self.patterns.values() if p.pattern_type == pt]) for pt in self.PATTERN_TYPES}
        
        return {
            "total_patterns": total,
            "win_rate": round(wins / max(total, 1), 3),
            "by_pattern_type": by_type,
            "by_regime": {r: len(pids) for r, pids in self._by_regime.items()},
            "by_sector": {s: len(pids) for s, pids in self._by_sector.items()},
        }


def pattern_memory_to_git(patterns: PatternMemoryBank):
    """Push pattern memory to GitHub."""
    stats = patterns.get_memory_stats()
    push_learning_checkpoint_async(
        f"patterns {stats['total_patterns']} templates WR={stats['win_rate']:.0%}"
    )