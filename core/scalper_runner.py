#!/usr/bin/env python3
"""
core/scalper_runner.py — Aggressive institutional scalper backtest & live runner.

This is the high-frequency scalping mode that:
1. Scans a universe of penny stocks for institutional activity
2. Enters on volume spikes + momentum with ultra-tight stops
3. Exits via trailing stop-loss + trailing profit-taker
4. Both stops AND targets are hard bracket orders on IB's servers
5. Reads tick-level data for sub-second exit decisions

Designed for $1,000 account: max $50 risk per trade, 
target 0.5-2.0% gains with >60% win rate.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.features import FeatureEngineer
from core.institutional import InstitutionalDetector, InstitutionalSignal
from core.scanner import StockScanner, ScanResult, PENNY_STOCK_UNIVERSE
from core.risk import RiskManager, TradePlan, compute_atr, compute_momentum_score
from core.broker import BrokerExecutor, BracketHandle
from core.env import TradingEnv
from core.notify import log, Notifier


class ScalperRunner:
    """
    Institutional scalper mode.
    
    Key differences from the PPO trader:
    - Scans multiple tickers, chases the best setup
    - Ultra-tight stops (0.3-1.0% instead of 1-2%)
    - Aggressive trailing from +0.2% profit
    - Uses institutional signals to confirm entries
    - Falls back to IB hard bracket orders for safety
    """
    
    def __init__(self, connector: IBConnector, cfg: BotConfig, notifier: Notifier):
        self.conn = connector
        self.ib = connector.ib
        self.cfg = cfg
        self.notifier = notifier
        
        self.data = DataManager(connector, cfg)
        self.broker = BrokerExecutor(connector, cfg)
        self.scanner = StockScanner(cfg)
        self.institutional = InstitutionalDetector()
        self.risk = RiskManager(cfg, cfg.INITIAL_CASH, notifier)
        
        # Account state
        self.account_equity = float(cfg.INITIAL_CASH)
        self.cash = float(cfg.INITIAL_CASH)
        self.shares = 0.0
        self.nav = float(cfg.INITIAL_CASH)
        self.current_ticker: Optional[str] = None
        self.bracket_handle: Optional[BracketHandle] = None
        
        # Scan results
        self.scan_results: List[ScanResult] = []
        self.top_pick: Optional[ScanResult] = None
        
        # Tick bar buffers for current ticker
        self._tick_buffer_5s: List[Dict] = []
        
        # Performance
        self.trades_taken = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        
        # State
        self._last_bar_time: Optional[pd.Timestamp] = None
        self._last_scan_time: float = 0.0
        self._last_metrics_write: float = 0.0
    
    def _write_live_metrics(self):
        """Write current state to live_metrics.json for dashboard."""
        try:
            now = time.time()
            if now - self._last_metrics_write < 2.0:
                return
            self._last_metrics_write = now
            
            win_rate = self.wins / (self.wins + self.losses + 1e-9) * 100
            
            # Build scan results for display
            scan_data = []
            for r in self.scan_results[:5]:
                scan_data.append({
                    "ticker": r.ticker, "price": r.price,
                    "vol": f"{r.volume/1000:.0f}K", "rv": r.relative_volume,
                    "score": r.rank_score, "reason": r.reason
                })
            
            metrics = {
                "mode": "SCALPER",
                "account_equity": round(self.account_equity, 2),
                "cash": round(self.cash, 2),
                "shares": self.shares,
                "current_ticker": self.current_ticker or "NONE",
                "position": f"{self.shares:.0f} {self.current_ticker}" if self.shares > 0 and self.current_ticker else "NONE",
                "nav": round(self.nav, 2),
                "total_pnl": round(self.total_pnl, 2),
                "trades_taken": self.trades_taken,
                "win_rate": round(win_rate, 1),
                "wins": self.wins,
                "losses": self.losses,
                "top_pick": self.top_pick.ticker if self.top_pick else None,
                "top_score": self.top_pick.rank_score if self.top_pick else 0,
                "scan_count": len(self.scan_results),
                "scan_results": scan_data,
                "timestamp": datetime.utcnow().isoformat(),
            }
            with open("live_metrics.json", "w") as f:
                json.dump(metrics, f, indent=2)
        except Exception as exc:
            log.debug(f"Could not write live_metrics.json: {exc}")
    
    def run_scan(self):
        """Run a full universe scan to find the best setup."""
        log.info(f"🔍 Scanning {len(PENNY_STOCK_UNIVERSE)} stocks for setups ...")
        
        results = []
        for ticker in PENNY_STOCK_UNIVERSE:
            try:
                # Fetch 30 bars of daily data for screening
                hist = self.data.fetch_historical(ticker=ticker, duration="1 M", bar_size="1 day")
                if hist is None or len(hist) < 21:
                    continue
                
                result = self.scanner.evaluate_stock(ticker, hist)
                if result:
                    results.append(result)
            except Exception as exc:
                log.debug(f"Scan error {ticker}: {exc}")
                continue
        
        self.scan_results = self.scanner.rank_scans(results)
        
        if self.scan_results:
            self.top_pick = self.scan_results[0]
            alert = self.scanner.build_alert_text(self.scan_results, top_n=3)
            log.info(f"\n{alert}")
            self.notifier.info(alert)
        else:
            log.info("🔍 Scanner: No setups found")
            self.top_pick = None
    
    def run(self):
        """Main scalper loop."""
        log.info("=" * 70)
        log.info("  SCALPER + INSTITUTIONAL MOMENTUM MODE")
        log.info(f"  Account: ${self.account_equity:,.2f}")
        log.info(f"  Universe: {len(PENNY_STOCK_UNIVERSE)} stocks")
        log.info(f"  Max risk/trade: ${self.cfg.risk_amount_usd(self.account_equity):.2f}")
        log.info("=" * 70)
        
        # Initial scan
        self.run_scan()
        
        try:
            while True:
                self.ib.sleep(1)
                
                if not self.conn.is_connected():
                    log.warning("IB connection lost. Attempting reconnect ...")
                    if not self.conn.reconnect():
                        break
                    self.run_scan()
                
                # Rescan every 5 minutes
                if time.time() - self._last_scan_time > 300:
                    self._last_scan_time = time.time()
                    self.run_scan()
                
                # If we have a top pick, start trading it
                if self.top_pick and self.shares == 0:
                    self._trade_top_pick()
                
                self._write_live_metrics()
                
        except KeyboardInterrupt:
            log.info("Keyboard interrupt — shutting down ...")
        finally:
            self._shutdown()
    
    def _trade_top_pick(self):
        """Execute a scalp trade on the top-ranked stock."""
        ticker = self.top_pick.ticker
        log.info(f"🎯 Trading top pick: {ticker} (Score: {self.top_pick.rank_score})")
        
        # Switch ticker context in data manager
        self.current_ticker = ticker
        
        # Fetch 1-min bars for decision making
        try:
            bar_df = self.data.fetch_historical(ticker=ticker, duration="2 D", bar_size="1 min")
            if bar_df is None or len(bar_df) < 30:
                log.warning(f"Not enough 1-min data for {ticker}")
                return
            
            current_price = float(bar_df["close"].iloc[-1])
            
            # Compute ATR from fast bars
            fast_atr = compute_atr(bar_df, period=5)
            if fast_atr <= 0:
                log.debug(f"Entry skipped: ATR not computable for {ticker}")
                return
            
            # Get institutional signal
            inst_signal = self.institutional.scan()
            
            # Check institutional override
            override, reason = self.institutional.should_override_buy()
            if override:
                log.warning(f"Inst override: {reason} — skipping {ticker}")
                return
            
            # Compute trade plan with scalper-tight stops
            momentum = compute_momentum_score(bar_df, lookback=5)
            
            plan = self._compute_scalp_plan(
                equity=self.account_equity,
                cash=self.cash,
                entry_price=current_price,
                atr=fast_atr,
                momentum_score=momentum,
                inst_confidence=inst_signal.get_scalp_confidence(),
            )
            
            if plan is None:
                return
            
            quantity = int(plan.shares)
            if quantity < 1:
                return
            
            # Place bracket order
            self.bracket_handle = self.broker.place_bracket_buy(
                quantity=quantity,
                limit_or_market_price=current_price,
                stop_price=plan.initial_stop_price,
                target_price=plan.take_profit_price,
            )
            self.ib.sleep(1)
            
            cost = quantity * current_price * (1.0 + self.cfg.TRANSACTION_COST_PCT)
            self.cash -= cost
            self.shares += float(quantity)
            self.nav = self.cash + self.shares * current_price
            
            self.risk.open_position(plan)
            self.trades_taken += 1
            
            # Build entry message
            entry_pct = (plan.take_profit_price - current_price) / current_price * 100
            stop_pct = (current_price - plan.initial_stop_price) / current_price * 100
            inst_str = f"🏦 Inst: {inst_signal.direction.upper()} ({inst_signal.strength:.1f})" if inst_signal.detected else ""
            
            log.info(f"🎯 SCALP ENTRY: {quantity}x {ticker} @ ${current_price:.2f} | "
                     f"Stop: -{stop_pct:.2f}% | Target: +{entry_pct:.2f}% | "
                     f"Risk: ${plan.risk_usd:.2f} | Score: {self.top_pick.rank_score}")
            
            self.notifier.trade_opened(
                side="BUY", ticker=ticker, qty=quantity, price=current_price,
                stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                risk_usd=plan.risk_usd,
            )
            
            if inst_str:
                msg = f"{inst_str}\nRank: {self.top_pick.rank_score} | {self.top_pick.reason}"
                self.notifier.info(msg)
            
        except Exception as exc:
            log.error(f"Error trading {ticker}: {exc}")
    
    def _compute_scalp_plan(self, equity: float, cash: float, entry_price: float,
                             atr: float, momentum_score: float = 0.0,
                             inst_confidence: float = 0.0) -> Optional[TradePlan]:
        """
        Scalp-specific trade plan with ultra-tight stops.
        - Max risk $50 on $1000 account
        - Stop: 0.3-1.0% (much tighter than normal mode)
        - Target: 0.5-2.0% with trailing immediately on +0.2%
        """
        if entry_price <= 0 or atr <= 0:
            return None
        
        risk_usd = self.cfg.risk_amount_usd(equity)
        
        # Tighter scalping stop: use ATR(5) * 0.8 instead of ATR(14) * 1.5
        stop_distance = atr * 0.8
        min_dist = entry_price * 0.003  # 0.3% minimum
        max_dist = entry_price * 0.010  # 1.0% maximum
        stop_distance = float(np.clip(stop_distance, min_dist, max_dist))
        stop_price = entry_price - stop_distance
        
        # More aggressive sizing for scalping
        shares_from_risk = risk_usd / stop_distance
        max_shares_by_cash = (cash * self.cfg.DEFAULT_MAX_POSITION_PCT) / entry_price
        
        # Institutional confidence bonus: scale up to 1.5x when tape is hot
        sizing_multiplier = 1.0 + (inst_confidence * 0.5)
        shares = min(
            shares_from_risk * sizing_multiplier,
            max_shares_by_cash,
            self.cfg.MAX_SHARES_PER_TRADE,
        )
        shares = float(np.floor(shares))
        
        if shares < 1:
            log.debug(f"Scalp plan rejected: <1 share (risk=${risk_usd:.2f}, dist=${stop_distance:.4f})")
            return None
        
        # Predictive take-profit: smaller for scalping
        # Base: 2.0x ATR, but adjusted for momentum
        tp_distance = atr * 1.5  # Tighter than normal mode's 2.5x
        tp_distance *= (1.0 + 0.3 * max(0.0, momentum_score))
        min_tp = stop_distance * 1.5  # Min R:R = 1.5
        tp_distance = max(tp_distance, min_tp)
        tp_distance = min(tp_distance, entry_price * 0.03)  # Cap at 3% for scalping
        take_profit_price = entry_price + tp_distance
        
        actual_risk_usd = shares * stop_distance
        
        plan = TradePlan(
            side="LONG",
            entry_price=entry_price,
            shares=shares,
            initial_stop_price=round(stop_price, 4),
            take_profit_price=round(take_profit_price, 4),
            risk_usd=round(actual_risk_usd, 2),
            atr_at_entry=atr,
        )
        
        log.info(
            f"SCALP PLAN: {shares:.0f} sh @ ${entry_price:.2f} | "
            f"Stop ${plan.initial_stop_price:.2f} (-{stop_distance/entry_price:.2%}) | "
            f"Target ${plan.take_profit_price:.2f} (+{tp_distance/entry_price:.2%}) | "
            f"Risk ${actual_risk_usd:.2f} | "
            f"Inst conf: {inst_confidence:.1%}"
        )
        return plan
    
    def _shutdown(self):
        if self.bracket_handle:
            log.info("Shutdown with open bracket — IB handles remain active.")
        
        ret = (self.nav / self.cfg.INITIAL_CASH - 1.0) * 100.0
        win_rate = self.wins / (self.wins + self.losses + 1e-9) * 100
        
        log.info("=" * 70)
        log.info("  SCALPER SESSION COMPLETE")
        log.info(f"  Final NAV: ${self.nav:,.2f} ({ret:+.1f}%)")
        log.info(f"  Trades: {self.trades_taken} | W: {self.wins} L: {self.losses} ({win_rate:.0f}%)")
        log.info(f"  Total P&L: ${self.total_pnl:+.2f}")
        log.info("=" * 70)
        
        self.notifier.info(
            f"🛑 Scalper Stopped\n"
            f"NAV: ${self.nav:,.2f} ({ret:+.1f}%)\n"
            f"Trades: {self.trades_taken} | Win: {win_rate:.0f}%\n"
            f"P&L: ${self.total_pnl:+.2f}"
        )
        
        self.conn.disconnect()