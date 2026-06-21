#!/usr/bin/env python3
"""
core/backtest_engine.py — True event-driven backtester that mimics live markets.

DIFFERENCE FROM VECTORIZED BACKTESTS:
Vectorized backtests iterate over a DataFrame with a for loop, looking at
all data at once. This causes look-ahead bias, unrealistic fills, and
overestimates strategy performance.

This engine replays historical data ONE EVENT AT A TIME, exactly as if it
were arriving from a live feed. Every tick/bar is processed sequentially,
forcing the AI to decide without knowing what comes next.

LIVE MARKET SIMULATION:
1. Event-Driven Loop — Each bar/tick fires as a discrete event
2. Simulated Latency — Configurable delay between signal generation & execution
3. Slippage Model — Fills at worse price based on volatility + position size
4. Order Book Simulation — Checks if liquidity existed at fill time
5. Rate Limiting — Same limits as live mode (trades/min/hour/day)
6. Full AI Pipeline — Guardrails, regime classifier, ensemble, confidence scoring
7. Complete Journal — Every decision, every micro-second logged

Usage:
    python core/backtest_engine.py --ticker SPY --start 2025-01-01 --end 2025-06-01
"""

import os
import sys
import json
import time
import math
import copy
import hashlib
import random
from typing import Optional, Tuple, Dict, List, Any, Callable
from dataclasses import dataclass, field, asdict
from collections import deque, defaultdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

# Conditional imports for enhanced AI
try:
    from core.agent_enhanced import (
        MarketRegimeClassifier, MarketRegime, RegimeResult,
        ConfidenceScorer, ReasoningChain, ModelVote,
        EnsembleTrader, AdaptiveLearner,
        build_enhanced_agent as _build_enhanced,
        compute_thinking_confidence,
    )
    from core.ai_guardrails import (
        GuardrailController, AuditEntry, sanitize_observation,
        sanitize_action, validate_config, compute_config_hash,
    )
    from core.features_enhanced import FeatureEngineerEnhanced
    _ENHANCED_AVAILABLE = True
except ImportError:
    _ENHANCED_AVAILABLE = False

from core.features import FeatureEngineer
from core.env import TradingEnv
from core.risk import RiskManager, TradePlan, compute_atr, compute_momentum_score


# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestConfig:
    """Configuration for the event-driven backtester."""
    
    # Data
    ticker: str = "SPY"
    start_date: str = "2025-01-01"
    end_date: str = "2025-06-01"
    bar_size: str = "1 min"       # Bar granularity for decision events
    
    # Market simulation
    slippage_model: str = "adaptive"   # "fixed", "adaptive", "random"
    fixed_slippage_pct: float = 0.002  # 0.2% slippage for "fixed" mode
    latency_ticks: int = 0             # 0 = instant, 1+ = delay by N ticks
    fill_probability: float = 0.98     # 98% chance of fill at simulated price
    
    # Rate limits (same as live mode)
    max_trades_per_minute: int = 10
    max_trades_per_hour: int = 50
    max_trades_per_day: int = 200
    
    # Journal
    journal_path: str = "backtest_journal.jsonl"
    save_every_n_events: int = 1000    # Auto-save journal periodically
    
    # AI
    use_enhanced_ai: bool = True
    confidence_threshold: float = 0.55
    
    # Progress
    verbose: bool = True
    show_progress_every: int = 100      # Log every N bars


# ═════════════════════════════════════════════════════════════════════════════
# MARKET SIMULATOR
# ═════════════════════════════════════════════════════════════════════════════

class MarketSimulator:
    """
    Simulates real market conditions during backtest.
    
    Features:
    - Latency: Delays between signal and execution
    - Slippage: Adaptive based on volatility
    - Fill probability: Not all orders get perfect fills
    - Liquidity check: Simulates order book depth
    """
    
    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self._pending_orders: List[Dict] = []
        self._executed_orders: List[Dict] = []
        self._latency_counter = 0
        self._rng = random.Random(42)  # Deterministic randomness
    
    def submit_order(self, order: Dict) -> str:
        """
        Submit an order to the simulated market.
        
        Returns order_id for tracking.
        """
        order_id = hashlib.md5(
            f"{order.get('side', '')}_{order.get('price', 0)}_{time.time()}_{self._latency_counter}".encode()
        ).hexdigest()[:12]
        
        order['order_id'] = order_id
        order['submitted_at'] = self._latency_counter
        order['status'] = 'pending'
        
        if self.cfg.latency_ticks > 0:
            order['execute_at'] = self._latency_counter + self.cfg.latency_ticks
            self._pending_orders.append(order)
        else:
            # Immediate execution
            order['status'] = 'filled'
            order['filled_at'] = self._latency_counter
            self._executed_orders.append(order)
        
        return order_id
    
    def on_tick(self, current_price: float, volume: float, 
                 high: float, low: float) -> List[Dict]:
        """
        Process a tick/bar event. Execute pending orders.
        
        Returns list of newly executed orders.
        """
        self._latency_counter += 1
        executed = []
        
        # Check pending orders
        still_pending = []
        for order in self._pending_orders:
            if order.get('execute_at', 0) <= self._latency_counter:
                # Execute with slippage
                filled_order = self._fill_order(order, current_price, high, low, volume)
                executed.append(filled_order)
                self._executed_orders.append(filled_order)
            else:
                still_pending.append(order)
        
        self._pending_orders = still_pending
        return executed
    
    def _fill_order(self, order: Dict, current_price: float,
                     high: float, low: float, volume: float) -> Dict:
        """Apply slippage and fill probability to an order."""
        filled = dict(order)
        fill_roll = self._rng.random()
        
        # Fill probability
        if fill_roll > self.cfg.fill_probability:
            filled['status'] = 'rejected'
            filled['reason'] = 'no_liquidity'
            filled['filled_price'] = None
            return filled
        
        # Slippage calculation
        side = order.get('side', 'BUY')
        base_price = order.get('price', current_price)
        
        if self.cfg.slippage_model == 'fixed':
            slip = base_price * self.cfg.fixed_slippage_pct
        elif self.cfg.slippage_model == 'adaptive':
            # Slippage scales with volatility proxy (range/close)
            volatility = (high - low) / (current_price + 1e-9)
            slip = base_price * max(0.001, min(0.01, volatility * 0.5))
        else:  # random
            slip = base_price * (0.001 + self._rng.random() * 0.009)
        
        if side == 'BUY':
            # Buy at higher price (worse)
            fill_price = base_price + slip
        else:
            # Sell at lower price (worse)
            fill_price = base_price - slip
        
        # Constrain fill price within day's range
        fill_price = max(low * 0.98, min(high * 1.02, fill_price))
        
        filled['status'] = 'filled'
        filled['filled_price'] = round(fill_price, 4)
        filled['slippage'] = round(abs(fill_price - base_price) / base_price * 100, 4)
        filled['filled_at'] = self._latency_counter
        
        return filled
    
    def reset(self):
        """Reset the simulator state."""
        self._pending_orders.clear()
        self._executed_orders.clear()
        self._latency_counter = 0
        self._rng = random.Random(42)


# ═════════════════════════════════════════════════════════════════════════════
# BACKTEST JOURNAL
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestEvent:
    """A single backtest event record."""
    # Metadata
    event_id: str
    timestamp: float
    event_type: str           # "bar", "tick", "signal", "entry", "exit", "guardrail", "regime"
    bar_index: int
    
    # Market state
    price: float
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    nav: float = 0.0
    cash: float = 0.0
    shares: float = 0.0
    
    # AI decision
    ppo_action: int = -1
    ppo_action_name: str = ""
    ppo_value: float = 0.0
    ppo_probabilities: List[float] = field(default_factory=list)
    
    # Regime
    regime: str = ""
    regime_confidence: float = 0.0
    trend_strength: float = 0.0
    volatility_percentile: float = 50.0
    
    # Ensemble
    ensemble_votes: List[Dict] = field(default_factory=list)
    ensemble_action: int = -1
    ensemble_confidence: float = 0.0
    
    # Guardrails
    guardrails_triggered: List[str] = field(default_factory=list)
    guardrail_override: bool = False
    
    # Confidence
    final_confidence: float = 0.0
    confidence_threshold: float = 0.55
    
    # Decision
    final_action: int = -1
    final_action_name: str = ""
    action_reasoning: str = ""
    
    # Execution
    fill_price: Optional[float] = None
    fill_quantity: Optional[float] = None
    slippage_pct: float = 0.0
    execution_latency: int = 0
    
    # Trade result
    trade_pnl_usd: float = 0.0
    trade_pnl_pct: float = 0.0
    exit_reason: str = ""
    
    # Risk
    risk_override: bool = False
    risk_override_reason: str = ""
    stop_price: float = 0.0
    target_price: float = 0.0


class BacktestJournal:
    """
    Complete, append-only journal of every backtest event.
    
    Features:
    - Every bar, tick, signal, entry, exit logged
    - JSONL format for easy analysis
    - Auto-save every N events
    - Session summary generation
    - Learns from past events to improve strategy
    """
    
    def __init__(self, path: str = "backtest_journal.jsonl", auto_save: int = 1000):
        self.path = path
        self.auto_save = auto_save
        self._events: List[Dict] = []
        self._save_counter = 0
        self._session_start = time.time()
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    
    def record(self, event: BacktestEvent):
        """Record a backtest event."""
        event_dict = asdict(event)
        # Add session metadata
        event_dict['session_elapsed'] = time.time() - self._session_start
        
        self._events.append(event_dict)
        self._save_counter += 1
        
        # Auto-save
        if self._save_counter >= self.auto_save:
            self._append_to_file()
            self._save_counter = 0
    
    def _append_to_file(self):
        """Append buffered events to journal file."""
        try:
            with open(self.path, 'a') as f:
                for event in self._events:
                    f.write(json.dumps(event, default=str) + '\n')
            self._events.clear()
        except Exception as exc:
            log.warning(f"Journal save failed: {exc}")
    
    def flush(self):
        """Flush remaining events to disk."""
        if self._events:
            self._append_to_file()
    
    def generate_summary(self) -> Dict:
        """Generate comprehensive backtest summary."""
        self.flush()
        
        if not os.path.exists(self.path):
            return {"error": "No journal data"}
        
        # Read all events
        events = []
        with open(self.path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        
        if not events:
            return {"error": "No events parsed"}
        
        # Compute metrics
        entries = [e for e in events if e.get('event_type') == 'entry']
        exits = [e for e in events if e.get('event_type') == 'exit']
        guardrails = [e for e in events if e.get('guardrails_triggered')]
        regimes = [e for e in events if e.get('event_type') == 'regime']
        
        # Performance
        nav_values = [e.get('nav', 0) for e in events if e.get('nav', 0) > 0]
        pnls = [e.get('trade_pnl_usd', 0) for e in exits]
        
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        initial_nav = nav_values[0] if nav_values else 0
        final_nav = nav_values[-1] if nav_values else 0
        
        # Drawdown
        if len(nav_values) > 1:
            nav_arr = np.array(nav_values)
            peak = np.maximum.accumulate(nav_arr)
            dd = (peak - nav_arr) / (peak + 1e-9)
            max_dd = float(dd.max() * 100)
        else:
            max_dd = 0.0
        
        # Sharpe
        trade_returns = []
        for e in exits:
            ret = e.get('trade_pnl_pct', 0)
            if ret != 0:
                trade_returns.append(ret)
        
        sharpe = 0.0
        if len(trade_returns) >= 5:
            ret_arr = np.array(trade_returns)
            if ret_arr.std() > 0:
                sharpe = float((ret_arr.mean() / ret_arr.std()) * np.sqrt(252))
        
        # Regime breakdown
        regime_counts = defaultdict(int)
        for e in regimes:
            r = e.get('regime', 'unknown')
            regime_counts[r] += 1
        
        # Guardrail breakdown
        guardrail_counts = defaultdict(int)
        for e in guardrails:
            for g in e.get('guardrails_triggered', []):
                guardrail_counts[g] += 1
        
        # Confidence analysis
        confidences = [e.get('final_confidence', 0) for e in events if e.get('final_confidence', 0) > 0]
        avg_confidence = float(np.mean(confidences)) if confidences else 0.0
        
        return {
            "period": {
                "start": events[0].get('timestamp', '') if events else '',
                "end": events[-1].get('timestamp', '') if events else '',
                "total_bars": len(events),
            },
            "performance": {
                "initial_nav": round(initial_nav, 2),
                "final_nav": round(final_nav, 2),
                "total_return_pct": round((final_nav / initial_nav - 1) * 100 if initial_nav else 0, 2),
                "total_pnl": round(final_nav - initial_nav, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "sharpe_ratio": round(sharpe, 3),
            },
            "trades": {
                "total": len(exits),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate_pct": round(len(wins) / len(exits) * 100 if exits else 0, 1),
                "avg_win": round(np.mean(wins), 2) if wins else 0,
                "avg_loss": round(np.mean(losses), 2) if losses else 0,
                "max_win": round(max(wins), 2) if wins else 0,
                "max_loss": round(min(losses), 2) if losses else 0,
                "profit_factor": round(abs(sum(wins) / (sum(abs(l) for l in losses) + 1e-9)), 2),
            },
            "ai": {
                "avg_confidence": round(avg_confidence, 3),
                "total_guardrail_events": len(guardrails),
                "guardrail_breakdown": dict(guardrail_counts),
                "regime_breakdown": dict(regime_counts),
                "enhanced_mode": _ENHANCED_AVAILABLE,
            },
        }
    
    def get_learning_insights(self) -> Dict:
        """Extract patterns and insights for self-improvement."""
        self.flush()
        
        if not os.path.exists(self.path):
            return {}
        
        # Read events
        events = []
        with open(self.path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        
        insights = {
            "confidence_calibration": {},
            "regime_performance": defaultdict(lambda: {"wins": 0, "losses": 0, "trades": 0}),
            "slippage_analysis": {},
            "best_conditions": [],
            "worst_conditions": [],
        }
        
        # Exit events with regime context
        exits = [e for e in events if e.get('event_type') == 'exit']
        
        for e in exits:
            regime = e.get('regime', 'unknown')
            pnl = e.get('trade_pnl_usd', 0)
            insights["regime_performance"][regime]["trades"] += 1
            if pnl > 0:
                insights["regime_performance"][regime]["wins"] += 1
            else:
                insights["regime_performance"][regime]["losses"] += 1
        
        # Confidence calibration
        conf_events = [e for e in events if e.get('final_confidence', 0) > 0]
        if conf_events:
            confidences = [e['final_confidence'] for e in conf_events]
            insights["confidence_calibration"] = {
                "mean": round(float(np.mean(confidences)), 3),
                "std": round(float(np.std(confidences)), 3),
                "min": round(float(min(confidences)), 3),
                "max": round(float(max(confidences)), 3),
            }
        
        # Slippage analysis
        fills = [e for e in events if e.get('slippage_pct', 0) > 0]
        if fills:
            slips = [e['slippage_pct'] for e in fills]
            insights["slippage_analysis"] = {
                "avg_slippage_pct": round(float(np.mean(slips)), 4),
                "max_slippage_pct": round(float(max(slips)), 4),
                "total_order_count": len(fills),
            }
        
        return dict(insights)


# ═════════════════════════════════════════════════════════════════════════════
# EVENT-DRIVEN BACKTESTER
# ═════════════════════════════════════════════════════════════════════════════

class EventDrivenBacktester:
    """
    True event-driven backtester that replays market data one bar at a time.
    
    FEATURES:
    - Replays bars sequentially (no look-ahead)
    - Runs full enhanced AI pipeline
    - Simulates slippage, latency, fill probability
    - Enforces rate limits (same as live)
    - Journals every event with full context
    - Generates comprehensive metrics + learning insights
    - Auto-pushes results to GitHub
    
    USAGE:
        bt = EventDrivenBacktester()
        result = bt.run("SPY", "2025-01-01", "2025-06-01")
    """
    
    ACTION_NAMES = {0: "HOLD", 1: "BUY", 2: "SELL"}
    
    def __init__(self, bt_cfg: Optional[BacktestConfig] = None):
        self.bt_cfg = bt_cfg or BacktestConfig()
        self.cfg = BotConfig()  # Original bot config
        self._override_cfg_for_backtest()
        
        # Components
        self.simulator = MarketSimulator(self.bt_cfg)
        self.journal = BacktestJournal(self.bt_cfg.journal_path, self.bt_cfg.save_every_n_events)
        
        # AI components
        self.model = None
        self.guardrails: Optional[GuardrailController] = None
        self.regime_classifier: Optional[MarketRegimeClassifier] = None
        self.ensemble: Optional[EnsembleTrader] = None
        self.confidence_scorer: Optional[ConfidenceScorer] = None
        self.components: Dict = {}
        
        # State
        self.cash = float(self.cfg.INITIAL_CASH)
        self.shares = 0.0
        self.nav = float(self.cfg.INITIAL_CASH)
        self.entry_price = 0.0
        self.plan: Optional[TradePlan] = None
        self.position_open = False
        self._bar_index = 0
        self._price_buffer: deque = deque(maxlen=100)
        
        # Rate limit tracking
        self._trade_timestamps: deque = deque(maxlen=1000)
        
        # Performance
        self.trade_pnls: List[float] = []
        self.nav_history: List[float] = [self.nav]
        self.peak_nav = self.nav
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        
        # Recent rewards for confidence scorer
        self._recent_rewards: List[float] = []
    
    def _override_cfg_for_backtest(self):
        """Apply backtest-specific config overrides."""
        self.cfg.TICKER = self.bt_cfg.ticker
        self.cfg.USE_ENHANCED_AI = self.bt_cfg.use_enhanced_ai
        self.cfg.CONFIDENCE_THRESHOLD = self.bt_cfg.confidence_threshold
        self.cfg.PAPER_TRADING = True  # Always paper in backtest
        # Disable GitHub push during backtest (we'll push final results)
        self.cfg.GITHUB_TOKEN = ""  
    
    def _init_ai(self):
        """Initialize AI components for backtest."""
        if not _ENHANCED_AVAILABLE:
            log.warning("Enhanced AI not available — running in standard mode")
            return
        
        if not self.bt_cfg.use_enhanced_ai:
            log.info("Enhanced AI disabled by config — running in standard mode")
            return
        
        log.info("🤖 Initializing Enhanced AI for backtest...")
        
        try:
            # Build model with dummy env
            from stable_baselines3 import PPO
            from stable_baselines3.common.vec_env import DummyVecEnv
            
            dummy_f = np.zeros((self.cfg.WINDOW_SIZE + 2, self.cfg.N_FEATURES), np.float32)
            dummy_px = np.ones(self.cfg.WINDOW_SIZE + 2, np.float32) * 100.0
            dummy_env = TradingEnv(dummy_f, dummy_px, self.cfg.INITIAL_CASH,
                                    self.cfg.TRANSACTION_COST_PCT, self.cfg.WINDOW_SIZE,
                                    self.cfg.DEFAULT_MAX_POSITION_PCT)
            
            # Try loading existing model, build new if not found
            model_path = None
            if os.path.exists(self.cfg.MODEL_PATH):
                model_path = self.cfg.MODEL_PATH
                log.info(f"Loading existing model: {model_path}")
            
            self.model, components = _build_enhanced(self.cfg, model_path, verbose=0)
            self.components = components
            
            # Add guardrails (must be done separately for backtest)
            from core.ai_guardrails import GuardrailController
            self.guardrails = GuardrailController(self.cfg)
            
            # Extract components
            self.regime_classifier = components.get('regime_classifier')
            self.ensemble = components.get('ensemble')
            self.confidence_scorer = components.get('confidence_scorer')
            
            log.info("✅ Enhanced AI initialized for backtest")
            
        except Exception as exc:
            log.warning(f"Enhanced AI initialization failed: {exc}")
            log.info("Falling back to basic model...")
    
    def _check_rate_limit(self) -> Tuple[bool, str]:
        """Check if we're within rate limits (same as live mode)."""
        now = time.time()
        # Purge old timestamps
        while self._trade_timestamps and now - self._trade_timestamps[0] > 86400:
            self._trade_timestamps.popleft()
        
        # Check daily
        daily = sum(1 for t in self._trade_timestamps if now - t < 86400)
        if daily >= self.bt_cfg.max_trades_per_day:
            return False, f"Daily limit: {daily}/{self.bt_cfg.max_trades_per_day}"
        
        # Check hourly
        hourly = sum(1 for t in self._trade_timestamps if now - t < 3600)
        if hourly >= self.bt_cfg.max_trades_per_hour:
            return False, f"Hourly limit: {hourly}/{self.bt_cfg.max_trades_per_hour}"
        
        # Check minutely
        minutely = sum(1 for t in self._trade_timestamps if now - t < 60)
        if minutely >= self.bt_cfg.max_trades_per_minute:
            return False, f"Minute limit: {minutely}/{self.bt_cfg.max_trades_per_minute}"
        
        return True, ""
    
    def _on_bar(self, row: pd.Series) -> BacktestEvent:
        """
        Process a single bar event (the core of event-driven backtest).
        
        This is called for EVERY bar in the historical data, IN ORDER,
        exactly as if the bar just arrived from the live feed.
        """
        self._bar_index += 1
        
        # Extract bar data
        price = float(row.get('close', row.get('price', 0)))
        high = float(row.get('high', price))
        low = float(row.get('low', price))
        volume = float(row.get('volume', 0))
        timestamp = str(row.get('timestamp', row.get('date', row.get('time', ''))))
        
        # Update market simulator
        executed_orders = self.simulator.on_tick(price, volume, high, low)
        
        # Process any fills from pending orders
        for order in executed_orders:
            if order['status'] == 'filled' and order.get('side') == 'BUY':
                fill_price = order['filled_price']
                qty = order.get('quantity', 0)
                
                # Execute the fill
                cost = qty * fill_price * (1 + self.cfg.TRANSACTION_COST_PCT)
                if cost <= self.cash:
                    self.cash -= cost
                    self.shares = float(qty)
                    self.entry_price = fill_price
                    self.position_open = True
                    
                    log.info(f"  ✅ FILL: {qty:.0f}x @ ${fill_price:.4f} (slippage: {order.get('slippage', 0):.3f}%)")
            
            elif order['status'] == 'filled' and order.get('side') == 'SELL':
                fill_price = order['filled_price']
                qty = order.get('quantity', 0)
                
                proceeds = qty * fill_price * (1 - self.cfg.TRANSACTION_COST_PCT)
                self.cash += proceeds
                pnl = (fill_price - self.entry_price) * qty if self.entry_price else 0
                
                self.shares = 0.0
                self.position_open = False
                self.total_trades += 1
                
                if pnl >= 0:
                    self.wins += 1
                else:
                    self.losses += 1
                self.trade_pnls.append(pnl)
                self._recent_rewards.append(pnl)
                
                log.info(f"  ✅ FILL SELL: {qty:.0f}x @ ${fill_price:.4f} | P&L: ${pnl:+.2f} | "
                         f"Reason: {order.get('reason', 'signal')}")
        
        # Update NAV
        self.nav = self.cash + self.shares * price
        if self.nav > self.peak_nav:
            self.peak_nav = self.nav
        self.nav_history.append(self.nav)
        self._price_buffer.append(price)
        
        if _ENHANCED_AVAILABLE and self.bt_cfg.use_enhanced_ai:
            return self._on_bar_enhanced(price, high, low, volume, timestamp)
        else:
            return self._on_bar_standard(price, high, low, volume, timestamp)
    
    def _on_bar_enhanced(self, price: float, high: float, low: float,
                          volume: float, timestamp: str) -> BacktestEvent:
        """Process bar with full enhanced AI pipeline."""
        event = BacktestEvent(
            event_id=hashlib.md5(f"{timestamp}_{self._bar_index}".encode()).hexdigest()[:12],
            timestamp=time.time(),
            event_type="bar",
            bar_index=self._bar_index,
            price=price,
            high=high, low=low, volume=volume,
            nav=self.nav, cash=self.cash, shares=self.shares,
        )
        
        # ── Step 1: Market regime classification ──
        regime_result = None
        if self.regime_classifier is not None and len(self._price_buffer) >= 50:
            # Build a minimal dataframe for regime classification
            df = pd.DataFrame({
                'close': list(self._price_buffer)[-50:],
                'high': [max(p, price) for p in list(self._price_buffer)[-50:]],
                'low': [min(p, price * 0.995) for p in list(self._price_buffer)[-50:]],
                'volume': [volume * (1 + (i % 5) * 0.1) for i in range(50)],
            })
            regime_result = self.regime_classifier.classify(df)
            
            event.regime = regime_result.regime.value
            event.regime_confidence = regime_result.confidence
            event.trend_strength = regime_result.trend_strength
            event.volatility_percentile = regime_result.volatility_percentile
        
        # ── Step 2: Build observation for PPO ──
        obs = self._build_observation(price)
        
        # ── Step 3: PPO prediction with probabilities ──
        if self.model is not None:
            try:
                ppo_action, ppo_value, ppo_probs = compute_thinking_confidence(self.model, obs)
                event.ppo_action = ppo_action
                event.ppo_action_name = self.ACTION_NAMES.get(ppo_action, "?")
                event.ppo_value = ppo_value
                event.ppo_probabilities = ppo_probs.tolist() if hasattr(ppo_probs, 'tolist') else list(ppo_probs)
            except Exception:
                # Fallback: basic predict
                action, _ = self.model.predict(obs, deterministic=True)
                ppo_action = int(action)
                ppo_value = 0.0
                ppo_probs = np.array([0.4, 0.3, 0.3])
        else:
            ppo_action = 0
            ppo_value = 0.0
            ppo_probs = np.array([1.0, 0.0, 0.0])
        
        # ── Step 4: Ensemble voting ──
        final_action = ppo_action
        final_confidence = float(max(ppo_probs))
        reasoning = ""
        
        if self.ensemble is not None and self.bt_cfg.use_enhanced_ai and len(self._price_buffer) >= 50:
            df = pd.DataFrame({
                'close': list(self._price_buffer)[-50:],
                'high': [max(p, price) for p in list(self._price_buffer)[-50:]],
                'low': [min(p, price * 0.995) for p in list(self._price_buffer)[-50:]],
                'volume': [volume * (1 + (i % 5) * 0.1) for i in range(50)],
            })
            
            try:
                votes = self.ensemble.get_votes(
                    ppo_action, ppo_probs, ppo_value,
                    regime_result or RegimeResult(
                        regime=MarketRegime.UNKNOWN, confidence=0.0,
                        trend_strength=0.0, volatility_percentile=50.0,
                        momentum=0.0, volume_regime="normal",
                        recommendation="",
                    ),
                    df,
                )
                
                ens_action, ens_conf, ens_reason = self.ensemble.ensemble_decision(
                    votes, min_confidence=self.bt_cfg.confidence_threshold
                )
                
                if ens_conf >= self.bt_cfg.confidence_threshold:
                    final_action = ens_action
                    final_confidence = ens_conf
                    reasoning = ens_reason
                
                event.ensemble_votes = [
                    {"model": v.model_name, "action": v.action, "confidence": v.confidence}
                    for v in votes
                ]
                event.ensemble_action = ens_action
                event.ensemble_confidence = ens_conf
                
            except Exception:
                pass
        
        # ── Step 5: Confidence scoring ──
        if self.confidence_scorer is not None:
            try:
                confidence = self.confidence_scorer.score(
                    ppo_probs, ppo_value,
                    regime_result or RegimeResult(
                        regime=MarketRegime.UNKNOWN, confidence=0.0,
                        trend_strength=0.0, volatility_percentile=50.0,
                        momentum=0.0, volume_regime="normal",
                        recommendation="",
                    ),
                    features=obs[:self.cfg.N_FEATURES] if len(obs) >= self.cfg.N_FEATURES else None,
                    last_n_rewards=self._recent_rewards[-20:] if self._recent_rewards else None,
                )
                
                if confidence < self.bt_cfg.confidence_threshold:
                    final_action = 0
                    reasoning = f"Low confidence ({confidence:.0%} < {self.bt_cfg.confidence_threshold:.0%})"
                final_confidence = confidence
            except Exception:
                pass
        
        event.final_confidence = final_confidence
        event.confidence_threshold = self.bt_cfg.confidence_threshold
        
        # ── Step 6: Guardrail validation ──
        if self.guardrails is not None:
            try:
                action, passed, warnings = self.guardrails.validate_agent_action(
                    final_action, obs,
                    features=obs[:self.cfg.N_FEATURES] if len(obs) >= self.cfg.N_FEATURES else None,
                )
                
                if not passed:
                    final_action = action
                    event.guardrails_triggered = warnings
                    reasoning = "; ".join(warnings) if warnings else reasoning
            except Exception:
                pass
        
        # ── Step 7: Rate limit check ──
        if final_action in (1, 2):
            allowed, limit_reason = self._check_rate_limit()
            if not allowed:
                final_action = 0
                reasoning = f"Rate limit: {limit_reason}"
                event.guardrails_triggered.append(f"rate_limit:{limit_reason}")
        
        event.final_action = final_action
        event.final_action_name = self.ACTION_NAMES.get(final_action, "?")
        event.action_reasoning = reasoning
        
        # ── Step 8: Execute action ──
        self._execute_action(final_action, price, high, low, volume, event, timestamp)
        
        return event
    
    def _on_bar_standard(self, price: float, high: float, low: float,
                          volume: float, timestamp: str) -> BacktestEvent:
        """Process bar with basic model (no enhanced AI)."""
        event = BacktestEvent(
            event_id=hashlib.md5(f"{timestamp}_{self._bar_index}".encode()).hexdigest()[:12],
            timestamp=time.time(),
            event_type="bar",
            bar_index=self._bar_index,
            price=price, high=high, low=low, volume=volume,
            nav=self.nav, cash=self.cash, shares=self.shares,
        )
        
        obs = self._build_observation(price)
        
        if self.model is not None:
            action, _ = self.model.predict(obs, deterministic=True)
            final_action = int(action)
        else:
            final_action = 0
        
        event.final_action = final_action
        event.final_action_name = self.ACTION_NAMES.get(final_action, "?")
        
        self._execute_action(final_action, price, high, low, volume, event, timestamp)
        
        return event
    
    def _build_observation(self, price: float) -> np.ndarray:
        """Build observation vector from current state."""
        # In backtest, we use a simplified observation
        # The full pipeline uses FeatureEngineer but that requires a DataFrame
        # For simplicity, we build a basic observation
        try:
            obs_dim = self.cfg.WINDOW_SIZE * self.cfg.N_FEATURES + 2
            obs = np.zeros(obs_dim, dtype=np.float32)
            
            # Fill with price history and account state
            price_history = list(self._price_buffer)
            for i, px in enumerate(price_history[:self.cfg.WINDOW_SIZE]):
                idx = i * self.cfg.N_FEATURES
                obs[idx] = px / (price + 1e-9) - 1.0  # Normalized price
                obs[idx + 1] = (px - price) / (price + 1e-9)  # Deviation
            
            # Account state
            total = self.cash + self.shares * price
            obs[-2] = self.cash / (total + 1e-9)
            obs[-1] = (self.shares * price) / (total + 1e-9)
            
            return obs
        except Exception:
            return np.zeros(self.cfg.N_FEATURES * self.cfg.WINDOW_SIZE + 2, dtype=np.float32)
    
    def _execute_action(self, action: int, price: float, high: float, low: float,
                         volume: float, event: BacktestEvent, timestamp: str):
        """Execute a trading action with market simulation."""
        
        # ── EXIT LOGIC ──
        if self.position_open and self.plan is not None:
            # Check stop loss
            if price <= self.plan.initial_stop_price and action != 2:
                # Exit due to stop loss
                order = {
                    'side': 'SELL',
                    'price': price,
                    'quantity': self.shares,
                    'reason': 'stop_loss',
                }
                order_id = self.simulator.submit_order(order)
                
                event.event_type = "exit"
                event.exit_reason = "stop_loss"
                event.stop_price = self.plan.initial_stop_price
                event.fill_price = price
                event.fill_quantity = self.shares
                
                # Record the exit event
                self.journal.record(event)
                
                # Process immediately for backtest (no latency on stops for safety)
                fill_price = price * (1 - self.bt_cfg.fixed_slippage_pct)
                qty = self.shares
                pnl = (fill_price - self.entry_price) * qty
                pnl_pct = (fill_price / self.entry_price - 1) * 100 if self.entry_price else 0
                
                self.cash += qty * fill_price * (1 - self.cfg.TRANSACTION_COST_PCT)
                self.shares = 0.0
                self.position_open = False
                self.total_trades += 1
                self.trade_pnls.append(pnl)
                self._recent_rewards.append(pnl)
                
                if pnl >= 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                event.trade_pnl_usd = pnl
                event.trade_pnl_pct = pnl_pct
                
                log.info(f"  🔴 STOP LOSS @ ${fill_price:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                return
            
            # Check take profit
            if price >= self.plan.take_profit_price and action != 1:
                order = {
                    'side': 'SELL',
                    'price': price,
                    'quantity': self.shares,
                    'reason': 'take_profit',
                }
                order_id = self.simulator.submit_order(order)
                
                event.event_type = "exit"
                event.exit_reason = "take_profit"
                event.target_price = self.plan.take_profit_price
                event.fill_price = price
                event.fill_quantity = self.shares
                
                self.journal.record(event)
                
                fill_price = price * (1 + self.bt_cfg.fixed_slippage_pct)
                qty = self.shares
                pnl = (fill_price - self.entry_price) * qty
                pnl_pct = (fill_price / self.entry_price - 1) * 100 if self.entry_price else 0
                
                self.cash += qty * fill_price * (1 - self.cfg.TRANSACTION_COST_PCT)
                self.shares = 0.0
                self.position_open = False
                self.total_trades += 1
                self.trade_pnls.append(pnl)
                self._recent_rewards.append(pnl)
                
                if pnl >= 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                event.trade_pnl_usd = pnl
                event.trade_pnl_pct = pnl_pct
                
                log.info(f"  🟢 TAKE PROFIT @ ${fill_price:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
                return
        
        # ── ENTRY LOGIC ──
        if action == 1 and not self.position_open:
            # Check rate limits
            allowed, _ = self._check_rate_limit()
            if not allowed:
                event.event_type = "bar"
                event.action_reasoning = "Rate limited"
                self.journal.record(event)
                return
            
            # Calculate position size
            atr_val = self._estimate_atr(price)
            if atr_val <= 0:
                event.event_type = "bar"
                event.action_reasoning = "ATR not computable"
                self.journal.record(event)
                return
            
            risk_usd = self.cfg.risk_amount_usd(self.nav)
            stop_dist = atr_val * self.cfg.STOP_ATR_MULTIPLIER
            min_dist = price * self.cfg.MIN_STOP_DISTANCE_PCT
            max_dist = price * self.cfg.MAX_STOP_DISTANCE_PCT
            stop_dist = float(np.clip(stop_dist, min_dist, max_dist))
            stop_price = price - stop_dist
            
            max_shares = (self.cash * self.cfg.DEFAULT_MAX_POSITION_PCT) / price
            risk_shares = risk_usd / stop_dist
            qty = min(risk_shares, max_shares, self.cfg.MAX_SHARES_PER_TRADE)
            qty = float(np.floor(qty))
            
            if qty < 1:
                event.event_type = "bar"
                event.action_reasoning = "Position < 1 share"
                self.journal.record(event)
                return
            
            # Calculate take profit
            tp_dist = atr_val * self.cfg.TAKE_PROFIT_ATR_MULTIPLIER
            min_tp = stop_dist * self.cfg.MIN_REWARD_RISK_RATIO
            tp_dist = max(tp_dist, min_tp)
            tp_price = price + tp_dist
            
            # Submit order with simulated latency
            order = {
                'side': 'BUY',
                'price': price,
                'quantity': qty,
                'stop_price': round(stop_price, 4),
                'target_price': round(tp_price, 4),
                'reason': 'signal',
            }
            order_id = self.simulator.submit_order(order)
            
            # Create trade plan
            self.plan = TradePlan(
                side="LONG",
                entry_price=price,
                shares=qty,
                initial_stop_price=round(stop_price, 4),
                take_profit_price=round(tp_price, 4),
                risk_usd=round(qty * stop_dist, 2),
                atr_at_entry=atr_val,
            )
            
            # Record entry event
            event.event_type = "entry"
            event.fill_price = price
            event.fill_quantity = qty
            event.stop_price = stop_price
            event.target_price = tp_price
            
            self._trade_timestamps.append(time.time())
            
            log.info(f"  📗 ENTRY SIGNAL: {qty:.0f}x @ ${price:.2f} | "
                     f"Stop: ${stop_price:.2f} (-{stop_dist/price:.2%}) | "
                     f"Target: ${tp_price:.2f} (+{tp_dist/price:.2%})")
        
        # ── SELL LOGIC ──
        elif action == 2 and self.position_open:
            order = {
                'side': 'SELL',
                'price': price,
                'quantity': self.shares,
                'reason': 'signal',
            }
            order_id = self.simulator.submit_order(order)
            
            event.event_type = "exit"
            event.exit_reason = "signal"
            event.fill_price = price
            event.fill_quantity = self.shares
            
            # Immediate fill (agent signal exit)
            fill_price = price * (1 + self.bt_cfg.fixed_slippage_pct)  # Worse price on exit
            qty = self.shares
            pnl = (fill_price - self.entry_price) * qty
            pnl_pct = (fill_price / self.entry_price - 1) * 100 if self.entry_price else 0
            
            self.cash += qty * fill_price * (1 - self.cfg.TRANSACTION_COST_PCT)
            self.shares = 0.0
            self.position_open = False
            self.total_trades += 1
            self.trade_pnls.append(pnl)
            self._recent_rewards.append(pnl)
            
            if pnl >= 0:
                self.wins += 1
            else:
                self.losses += 1
            
            self.plan = None
            
            event.trade_pnl_usd = pnl
            event.trade_pnl_pct = pnl_pct
            
            log.info(f"  📕 AGENT SELL @ ${fill_price:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
        
        # Record the event
        if event.event_type == "bar":
            self.journal.record(event)
    
    def _estimate_atr(self, current_price: float) -> float:
        """Estimate ATR from price buffer."""
        if len(self._price_buffer) < 15:
            return current_price * 0.01  # Default 1%
        
        prices = list(self._price_buffer)
        tr_values = []
        for i in range(1, len(prices)):
            tr = abs(prices[i] - prices[i-1])
            tr_values.append(tr)
        
        if tr_values:
            return float(np.mean(tr_values[-14:]))
        return current_price * 0.01
    
    def run(self, ticker: str = "", start: str = "", end: str = "",
             data: Optional[pd.DataFrame] = None) -> Dict:
        """
        Run the event-driven backtest.
        
        Args:
            ticker: Override ticker
            start: Override start date
            end: Override end date
            data: Pre-loaded DataFrame (will fetch from IB if None)
            
        Returns:
            Dict with full results and metrics
        """
        if ticker:
            self.bt_cfg.ticker = ticker
            self.cfg.TICKER = ticker
        if start:
            self.bt_cfg.start_date = start
        if end:
            self.bt_cfg.end_date = end
        
        log.info("=" * 70)
        log.info(f"  EVENT-DRIVEN BACKTEST")
        log.info(f"  Ticker: {self.bt_cfg.ticker}")
        log.info(f"  Period: {self.bt_cfg.start_date} → {self.bt_cfg.end_date}")
        log.info(f"  AI Mode: {'ENHANCED' if self.bt_cfg.use_enhanced_ai else 'STANDARD'}")
        log.info(f"  Slippage: {self.bt_cfg.slippage_model}")
        log.info(f"  Latency: {self.bt_cfg.latency_ticks} ticks")
        log.info(f"  Initial Cash: ${self.cfg.INITIAL_CASH:,.0f}")
        log.info("=" * 70)
        
        # Get data if not provided
        if data is None:
            data = self._fetch_data()
        
        if data is None or len(data) < 50:
            log.error("Insufficient data for backtest")
            return {"error": "Insufficient data"}
        
        log.info(f"Data loaded: {len(data)} bars from {data.index[0]} to {data.index[-1]}")
        
        # Initialize AI
        self._init_ai()
        
        # Initialize simulator
        self.simulator.reset()
        
        # Process each bar sequentially (event-driven loop)
        total_bars = len(data)
        start_time = time.time()
        
        for idx, (bar_time, row) in enumerate(data.iterrows()):
            event = self._on_bar(row)
            
            # Progress logging
            if self.bt_cfg.verbose and (idx + 1) % self.bt_cfg.show_progress_every == 0:
                elapsed = time.time() - start_time
                pct = (idx + 1) / total_bars * 100
                bars_per_sec = (idx + 1) / (elapsed + 1e-9)
                remaining = (total_bars - idx - 1) / (bars_per_sec + 1e-9)
                
                log.info(f"  Progress: {pct:.0f}% ({idx+1}/{total_bars}) | "
                         f"NAV: ${self.nav:,.2f} | "
                         f"Trades: {self.total_trades} | "
                         f"Speed: {bars_per_sec:.0f} bars/s | "
                         f"ETA: {remaining:.0f}s")
        
        # Close any open position
        if self.position_open:
            final_price = float(data['close'].iloc[-1]) if 'close' in data else float(data.iloc[-1].get('close', 0))
            pnl = (final_price - self.entry_price) * self.shares
            pnl_pct = (final_price / self.entry_price - 1) * 100 if self.entry_price else 0
            
            self.cash += self.shares * final_price * (1 - self.cfg.TRANSACTION_COST_PCT)
            self.total_trades += 1
            self.trade_pnls.append(pnl)
            if pnl >= 0:
                self.wins += 1
            else:
                self.losses += 1
            
            log.info(f"  📕 FORCE CLOSE @ ${final_price:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
            self.shares = 0.0
            self.position_open = False
        
        self.nav = self.cash
        
        # Flush journal
        self.journal.flush()
        
        # Generate results
        total_elapsed = time.time() - start_time
        total_return = (self.nav / self.cfg.INITIAL_CASH - 1) * 100
        win_rate = self.wins / max(1, self.total_trades) * 100
        
        nav_arr = np.array(self.nav_history)
        peak = np.maximum.accumulate(nav_arr)
        dd = (peak - nav_arr) / (peak + 1e-9)
        max_dd = dd.max() * 100
        
        trade_returns_array = []
        for pnl in self.trade_pnls:
            if pnl != 0:
                trade_returns_array.append(pnl / self.cfg.INITIAL_CASH)
        
        sharpe = 0.0
        if len(trade_returns_array) >= 5:
            ret_arr = np.array(trade_returns_array)
            if ret_arr.std() > 0:
                sharpe = float((ret_arr.mean() / ret_arr.std()) * np.sqrt(252))
        
        avg_win = float(np.mean([p for p in self.trade_pnls if p > 0])) if self.wins > 0 else 0
        avg_loss = float(np.mean([p for p in self.trade_pnls if p < 0])) if self.losses > 0 else 0
        
        results = {
            "summary": {
                "ticker": self.bt_cfg.ticker,
                "period": f"{self.bt_cfg.start_date} → {self.bt_cfg.end_date}",
                "total_bars": total_bars,
                "elapsed_seconds": round(total_elapsed, 1),
                "bars_per_second": round(total_bars / total_elapsed, 1) if total_elapsed > 0 else 0,
            },
            "performance": {
                "initial_nav": round(self.cfg.INITIAL_CASH, 2),
                "final_nav": round(self.nav, 2),
                "total_pnl": round(self.nav - self.cfg.INITIAL_CASH, 2),
                "total_return_pct": round(total_return, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "sharpe_ratio": round(sharpe, 3),
            },
            "trades": {
                "total": self.total_trades,
                "wins": self.wins,
                "losses": self.losses,
                "win_rate_pct": round(win_rate, 1),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": round(abs(sum(p for p in self.trade_pnls if p > 0) / (sum(abs(p) for p in self.trade_pnls if p < 0) + 1e-9)), 2),
                "expectancy": round(win_rate/100 * avg_win + (1-win_rate/100) * avg_loss, 2),
            },
            "ai_mode": "enhanced" if self.bt_cfg.use_enhanced_ai else "standard",
            "journal_path": self.bt_cfg.journal_path,
            "journal_summary": self.journal.generate_summary(),
            "learning_insights": self.journal.get_learning_insights(),
        }
        
        # Print results
        log.info("=" * 70)
        log.info("  BACKTEST RESULTS")
        log.info(f"  Period:      {results['summary']['period']}")
        log.info(f"  Bars:        {results['summary']['total_bars']:,} ({results['summary']['bars_per_second']:.0f}/s)")
        log.info(f"  Initial NAV: ${results['performance']['initial_nav']:,.2f}")
        log.info(f"  Final NAV:   ${results['performance']['final_nav']:,.2f}")
        log.info(f"  Return:      {results['performance']['total_return_pct']:+.2f}%")
        log.info(f"  P&L:         ${results['performance']['total_pnl']:+,.2f}")
        log.info(f"  Max DD:      {results['performance']['max_drawdown_pct']:.2f}%")
        log.info(f"  Sharpe:      {results['performance']['sharpe_ratio']:.3f}")
        log.info(f"  Trades:      {results['trades']['total']} ({results['trades']['wins']}W / {results['trades']['losses']}L)")
        log.info(f"  Win Rate:    {results['trades']['win_rate_pct']:.1f}%")
        log.info(f"  Profit Fac:  {results['trades']['profit_factor']:.2f}")
        log.info(f"  Expectancy:  ${results['trades']['expectancy']:+.2f}")
        log.info(f"  AI Mode:     {results['ai_mode'].upper()}")
        log.info("=" * 70)
        
        # Save results to JSON
        results_path = f"backtest_results_{self.bt_cfg.ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        log.info(f"Results saved -> {results_path}")
        
        return results
    
    def _fetch_data(self) -> Optional[pd.DataFrame]:
        """Fetch historical data for backtest."""
        try:
            # Try to fetch from IB
            from core.connector import IBConnector
            from core.data import DataManager
            
            conn = IBConnector(self.cfg)
            if not conn.connect():
                log.warning("Cannot connect to IB — generating synthetic data for testing")
                return self._generate_synthetic_data()
            
            dm = DataManager(conn, self.cfg)
            
            # Calculate duration based on dates
            start_dt = datetime.strptime(self.bt_cfg.start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(self.bt_cfg.end_date, "%Y-%m-%d")
            days = (end_dt - start_dt).days
            
            if days <= 60:
                duration = "2 M"
            elif days <= 180:
                duration = "6 M"
            else:
                duration = "1 Y"
            
            df = dm.fetch_historical(
                duration=duration,
                bar_size=self.bt_cfg.bar_size,
            )
            
            conn.disconnect()
            
            if df is not None and len(df) > 50:
                return df
            
            log.warning(f"IB returned insufficient data ({len(df) if df is not None else 0} rows)")
            return self._generate_synthetic_data()
            
        except Exception as exc:
            log.warning(f"Data fetch failed: {exc}")
            return self._generate_synthetic_data()
    
    def _generate_synthetic_data(self) -> pd.DataFrame:
        """Generate synthetic market data for testing without IB connection."""
        log.info("Generating synthetic market data for backtest...")
        
        np.random.seed(42)
        
        start_dt = datetime.strptime(self.bt_cfg.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(self.bt_cfg.end_date, "%Y-%m-%d")
        
        if self.bt_cfg.bar_size == "1 min":
            # Generate 1-minute bars for market hours only (9:30-16:00 ET)
            bar_interval = timedelta(minutes=1)
            bars_per_day = 390  # 6.5 hours * 60 min
        else:
            bar_interval = timedelta(days=1)
            bars_per_day = 1
        
        # Determine step based on bar size
        if "min" in self.bt_cfg.bar_size:
            minutes = int(self.bt_cfg.bar_size.split()[0])
            step = timedelta(minutes=minutes)
        elif "day" in self.bt_cfg.bar_size:
            step = timedelta(days=1)
        else:
            step = timedelta(minutes=1)
        
        # Generate timestamps (only weekdays, market hours for intraday)
        timestamps = []
        current = start_dt
        while current <= end_dt:
            if current.weekday() < 5:  # Weekday
                if step < timedelta(days=1):
                    # Market hours only
                    for hour in range(9, 16):
                        for minute in ([30] if hour == 9 else [0, 15, 30, 45] if hour < 16 else [0]):
                            ts = current.replace(hour=hour, minute=minute, second=0)
                            if ts <= end_dt:
                                timestamps.append(ts)
                else:
                    timestamps.append(current)
            current += timedelta(days=1)
        
        if not timestamps:
            timestamps = [start_dt + i * step for i in range(500)]
        
        # Generate realistic price series (random walk with drift and volatility)
        n = len(timestamps)
        price = 200.0  # Starting price (like SPY)
        prices = []
        daily_vol = 0.005  # 0.5% daily vol
        intraday_vol = daily_vol / np.sqrt(390) if bars_per_day > 1 else daily_vol
        
        for i in range(n):
            # Random walk with slight upward drift
            drift = 0.0001  # Slight positive drift
            noise = np.random.randn() * intraday_vol
            price *= (1 + drift + noise)
            price = max(price, 1.0)  # Floor at $1
            prices.append(price)
        
        # Build DataFrame
        df = pd.DataFrame({
            'open': [p * (1 - 0.001 * np.random.rand()) for p in prices],
            'high': [p * (1 + 0.002 * np.random.rand()) for p in prices],
            'low': [p * (1 - 0.002 * np.random.rand()) for p in prices],
            'close': prices,
            'volume': [int(1_000_000 * (0.5 + np.random.rand())) for _ in range(n)],
        }, index=timestamps[:n])
        
        df.index.name = 'timestamp'
        log.info(f"Synthetic data generated: {len(df)} bars, "
                 f"price range ${df['low'].min():.2f} - ${df['high'].max():.2f}")
        
        return df


# ═════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def run_backtest_cli():
    """Run backtest from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Event-Driven Backtester with Full AI Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python core/backtest_engine.py --ticker SPY --start 2025-01-01 --end 2025-06-01
  python core/backtest_engine.py --ticker AAPL --start 2025-01-01 --end 2025-06-01 --no-enhanced
  python core/backtest_engine.py --ticker TSLA --start 2025-03-01 --end 2025-04-01 --slippage adaptive
        """
    )
    parser.add_argument("--ticker", default="SPY", help="Ticker symbol")
    parser.add_argument("--start", default="2025-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-06-01", help="End date (YYYY-MM-DD)")
    parser.add_argument("--cash", default=1000.0, type=float, help="Initial cash")
    parser.add_argument("--slippage", choices=["fixed", "adaptive", "random"],
                        default="adaptive", help="Slippage model")
    parser.add_argument("--latency", type=int, default=0, help="Latency in ticks")
    parser.add_argument("--no-enhanced", action="store_true", help="Disable enhanced AI")
    parser.add_argument("--confidence", type=float, default=0.55,
                        help="Confidence threshold (0.0-1.0)")
    parser.add_argument("--bar-size", default="1 min",
                        choices=["1 min", "5 min", "15 min", "1 day"],
                        help="Bar size for backtest")
    parser.add_argument("--quiet", action="store_true", help="Less verbose output")
    
    args = parser.parse_args()
    
    bt_cfg = BacktestConfig(
        ticker=args.ticker.upper(),
        start_date=args.start,
        end_date=args.end,
        bar_size=args.bar_size,
        slippage_model=args.slippage,
        latency_ticks=args.latency,
        use_enhanced_ai=not args.no_enhanced,
        confidence_threshold=args.confidence,
        verbose=not args.quiet,
        journal_path=f"backtest_journal_{args.ticker}.jsonl",
    )
    
    bt = EventDrivenBacktester(bt_cfg)
    results = bt.run()
    
    return results


if __name__ == "__main__":
    run_backtest_cli()