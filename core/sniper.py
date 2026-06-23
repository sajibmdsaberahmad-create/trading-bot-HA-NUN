#!/usr/bin/env python3
"""
core/sniper.py — HA-NUN "Sniper-Lock" Architecture

Implements a two-phase high-frequency trading system:
  Phase 1: Wide-Net Scout (Low-Frequency) — Scans broad market, ranks candidates
  Phase 2: Strike Squad (Ultra-Low-Latency) — Microsecond execution on locked targets

This design bypasses IBKR API throttling by limiting monitoring to max 5 tickers,
while maintaining dynamic flexibility via background scanning.

Key Features:
  - Thread-safe target roster with async locks
  - Zero API hammering (5-ticker limit prevents throttling)
  - Preserves context locality for GPU (21M student model stays focused)
  - Automatic hot-swap when locked targets enter calm phases
  - Millisecond-level execution heartbeat
"""

import asyncio
import logging
from typing import List, Set, Optional, Dict, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path

from core.notify import log

# ═════════════════════════════════════════════════════════════════════════════
# TARGET LOCK SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class LockedTarget:
    """Represents a locked target with metadata."""
    ticker: str
    score: float  # AI confidence score [0, 1]
    locked_at: datetime = field(default_factory=datetime.now)
    last_heartbeat: datetime = field(default_factory=datetime.now)
    volatility: float = 0.0
    momentum: float = 0.0
    spread_basis_points: float = 0.0
    
    def age_seconds(self) -> float:
        """Time (in seconds) since this target was locked."""
        return (datetime.now() - self.locked_at).total_seconds()
    
    def heartbeat_age_ms(self) -> float:
        """Time (in milliseconds) since last heartbeat."""
        return (datetime.now() - self.last_heartbeat).total_seconds() * 1000
    
    def is_stale(self, timeout_seconds: int = 3600) -> bool:
        """Check if target is stale and should be cycled out."""
        return self.age_seconds() > timeout_seconds


class SniperTargetLock:
    """
    Thread-safe async target roster manager.
    
    Maintains the current "locked sights" (max 5 tickers) and coordinates
    updates between the wide-net scout and strike squad.
    """
    
    def __init__(self, max_targets: int = 5, stale_timeout: int = 3600):
        self.max_targets = max_targets
        self.stale_timeout = stale_timeout
        self.locked_targets: Dict[str, LockedTarget] = {}
        self.lock = asyncio.Lock()
        self.lock_event = asyncio.Event()  # Notifies heartbeat of changes
        
        # Metrics
        self.total_lock_updates = 0
        self.total_cycles = 0
        self.lock_history: List[Tuple[datetime, List[str]]] = []
        
    async def update_targets(self, new_scouted_candidates: List[Tuple[str, float]]) -> bool:
        """
        Update the locked roster with freshly scouted candidates.
        
        Args:
            new_scouted_candidates: List of (ticker, score) tuples ranked by AI
            
        Returns:
            True if roster changed, False if no change needed
        """
        async with self.lock:
            # Filter: only keep candidates with valid scores
            valid_candidates = [
                (ticker, score) for ticker, score in new_scouted_candidates
                if 0 <= score <= 1.0
            ]
            
            # Identify stale targets to remove
            stale_tickers = {
                ticker for ticker, target in self.locked_targets.items()
                if target.is_stale(self.stale_timeout)
            }
            
            # Remove stale targets
            for ticker in stale_tickers:
                del self.locked_targets[ticker]
                log.info(f"🔄 Stale target cycled out: {ticker}")
            
            # Build the new target set (keep high-performers, add fresh ones)
            old_tickers = set(self.locked_targets.keys())
            new_tickers_set = set()
            
            # First pass: retain existing targets if they score high
            for ticker, target in list(self.locked_targets.items()):
                # Check if this ticker is in new candidates and scores well
                matching = [s for t, s in valid_candidates if t == ticker]
                if matching and matching[0] >= 0.5:  # Threshold to keep
                    new_tickers_set.add(ticker)
                    self.locked_targets[ticker].score = matching[0]
            
            # Second pass: fill remaining slots with top new candidates
            for ticker, score in valid_candidates:
                if len(new_tickers_set) >= self.max_targets:
                    break
                if ticker not in new_tickers_set:
                    new_tickers_set.add(ticker)
                    self.locked_targets[ticker] = LockedTarget(
                        ticker=ticker,
                        score=score,
                        locked_at=datetime.now()
                    )
            
            # Detect change
            changed = new_tickers_set != old_tickers
            
            if changed:
                self.total_lock_updates += 1
                self.total_cycles += 1
                old_list = sorted(old_tickers)
                new_list = sorted(new_tickers_set)
                
                log.info(
                    f"🎯 SNIPER RE-ALIGNED [Cycle #{self.total_cycles}]\n"
                    f"   Was: {old_list}\n"
                    f"   Now: {new_list}"
                )
                
                # Record in history for analysis
                self.lock_history.append((datetime.now(), new_list))
                
                # Notify heartbeat loop of change
                self.lock_event.set()
            
            return changed
    
    async def get_targets(self) -> List[LockedTarget]:
        """
        Get current locked targets (heartbeat calls this frequently).
        """
        async with self.lock:
            return list(self.locked_targets.values())
    
    async def get_ticker_list(self) -> List[str]:
        """Get simple list of locked tickers."""
        async with self.lock:
            return list(self.locked_targets.keys())
    
    async def update_heartbeat(self, ticker: str, metrics: Dict[str, float]):
        """
        Update heartbeat metrics for a specific target.
        Called by the strike squad after each execution pulse.
        
        Args:
            ticker: Target ticker
            metrics: Dict with 'volatility', 'momentum', 'spread_bps', etc.
        """
        async with self.lock:
            if ticker in self.locked_targets:
                target = self.locked_targets[ticker]
                target.last_heartbeat = datetime.now()
                target.volatility = metrics.get('volatility', target.volatility)
                target.momentum = metrics.get('momentum', target.momentum)
                target.spread_basis_points = metrics.get('spread_bps', target.spread_basis_points)
    
    async def is_target_locked(self, ticker: str) -> bool:
        """Check if a specific ticker is currently locked."""
        async with self.lock:
            return ticker in self.locked_targets
    
    def get_stats(self) -> Dict:
        """Get sniper statistics."""
        return {
            "total_updates": self.total_lock_updates,
            "total_cycles": self.total_cycles,
            "current_targets": list(self.locked_targets.keys()),
            "num_locked": len(self.locked_targets),
            "max_targets": self.max_targets,
            "history_size": len(self.lock_history),
        }


# ═════════════════════════════════════════════════════════════════════════════
# GLOBAL SINGLETON
# ═════════════════════════════════════════════════════════════════════════════

_sniper_instance: Optional[SniperTargetLock] = None


def get_sniper() -> SniperTargetLock:
    """Get or initialize the global sniper lock system."""
    global _sniper_instance
    if _sniper_instance is None:
        _sniper_instance = SniperTargetLock(max_targets=5, stale_timeout=3600)
    return _sniper_instance


def initialize_sniper(max_targets: int = 5, stale_timeout: int = 3600) -> SniperTargetLock:
    """Initialize sniper with custom parameters."""
    global _sniper_instance
    _sniper_instance = SniperTargetLock(max_targets=max_targets, stale_timeout=stale_timeout)
    log.info(f"🎯 Sniper Initialized | Max Targets: {max_targets} | Stale Timeout: {stale_timeout}s")
    return _sniper_instance


# ═════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═════════════════════════════════════════════════════════════════════════════

async def save_sniper_state(filepath: str):
    """Save sniper lock history and stats to disk (for analysis)."""
    sniper = get_sniper()
    stats = sniper.get_stats()
    stats["timestamp"] = datetime.now().isoformat()
    stats["lock_history"] = [
        {"time": t.isoformat(), "targets": tickers}
        for t, tickers in sniper.lock_history
    ]
    
    import json
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(stats, f, indent=2)
    log.debug(f"Sniper state saved to {filepath}")


async def reset_sniper():
    """Reset sniper lock system (for testing/restart)."""
    global _sniper_instance
    sniper = get_sniper()
    sniper.locked_targets.clear()
    sniper.total_updates = 0
    sniper.lock_event.set()
    log.info("🎯 Sniper reset to clean slate")
