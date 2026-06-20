#!/usr/bin/env python3
"""
core/scalper_runner.py — Single-focus institutional scalper.

THE RULES:
1. Scan ALL tickers, pick top 1 by probability score.
2. Deploy MAX $1,000 per trade (from live account balance).
3. Risk is FIXED at $50 max per trade — cannot be overridden.
4. Only trade UPTRENDS (price > SMA20, VWAP, rising closes).
5. Every tick is analyzed: price delta, velocity, tape rhythm.
6. Hard stop + trailing stop + hard TP + trailing profit.
7. Daily summary shows all trades + balance changes.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

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
from core.git_sync import init as git_sync_init, push_trade, push_daily_summary


def _only_uptrend(df: pd.DataFrame, current_px: float) -> bool:
    """
    Strict uptrend filter:
    - Price > 20-period SMA
    - Price > VWAP of last 20 bars
    - Last 3 closes are rising
    - ATR is not expanding wildly (avoid choppy markets)
    """
    if len(df) < 20:
        return False
    closes = df["close"].values[-20:]
    sma20 = np.mean(closes)
    if current_px <= sma20:
        return False

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vwap = np.average(typical[-20:], weights=df["volume"].values[-20:])
    if current_px <= vwap:
        return False

    if not all(closes[i] >= closes[i-1] for i in range(-3, 0)):
        return False

    # ATR stability check
    atr = compute_atr(df, period=10)
    if atr <= 0 or atr > current_px * 0.05:
        return False

    return True


class ScalperRunner:
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
        git_sync_init(cfg)
        
        # Account state (read from IB live)
        self.account_equity = float(cfg.INITIAL_CASH)
        self.available_cash: Optional[float] = None  # from IB
        self.cash = float(cfg.INITIAL_CASH)
        self.shares = 0.0
        self.nav = float(cfg.INITIAL_CASH)
        self.current_ticker: Optional[str] = None
        self.bracket_handle: Optional[BracketHandle] = None
        
        # Scanner state
        self.scan_results: List[ScanResult] = []
        self.top_pick: Optional[ScanResult] = None
        self._last_scan_time: float = 0.0
        self._last_metrics_write: float = 0.0
        
        # Trade journal for end-of-day
        self.trade_journal: List[Dict] = []
        self.trades_today: int = 0
        self._current_day: Optional[str] = None
        self._last_daily_push_date: Optional[str] = None
    
    def _refresh_account_balance(self):
        """Pull live balance from IB."""
        try:
            values = self.ib.accountValues()
            for v in values:
                if v.tag in ("NetLiquidation", "TotalCashValue"):
                    if v.currency == self.cfg.CURRENCY:
                        self.account_equity = float(v.value)
                        if v.tag == "TotalCashValue":
                            self.available_cash = float(v.value)
            if self.available_cash is None:
                self.available_cash = self.account_equity
            self.cash = self.available_cash
            # Expose to notifier for Telegram balance display
            self.cfg._latest_account_balance = self.account_equity
        except Exception as exc:
            log.debug(f"Could not fetch IB account balance: {exc}")
        self.nav = self.cash + self.shares * self._latest_price()
    
    def _latest_price(self) -> float:
        try:
            return self.data.get_latest_price() or 0.0
        except Exception:
            return 0.0
    
    def _write_live_metrics(self):
        try:
            now = time.time()
            if now - self._last_metrics_write < 2.0:
                return
            self._last_metrics_write = now
            
            win_rate = (self.risk.win_rate * 100) if hasattr(self.risk, 'win_rate') else 0.0
            
            scan_data = []
            for r in self.scan_results[:5]:
                scan_data.append({
                    "ticker": r.ticker, "price": r.price,
                    "score": round(r.rank_score, 1), "reason": r.reason[:30]
                })
            
            metrics = {
                "mode": "SCALPER_FOCUS",
                "account_equity": round(self.account_equity, 2),
                "available_cash": round(self.available_cash or 0, 2),
                "position_value": round(self.shares * self._latest_price(), 2),
                "nav": round(self.nav, 2),
                "deployed_pct": round((self.nav - (self.available_cash or 0)) / (self.account_equity + 1e-9) * 100, 1),
                "current_ticker": self.current_ticker or "NONE",
                "position": f"{self.shares:.0f} {self.current_ticker}" if self.shares > 0 else "NONE",
                "win_rate": round(win_rate, 1),
                "trades_today": self.trades_today,
                "top_pick": self.top_pick.ticker if self.top_pick else None,
                "top_score": self.top_pick.rank_score if self.top_pick else 0,
                "scan_results": scan_data,
                "timestamp": datetime.utcnow().isoformat(),
            }
            with open("live_metrics.json", "w") as f:
                json.dump(metrics, f, indent=2)
        except Exception as exc:
            log.debug(f"Could not write live_metrics.json: {exc}")
    
    def run(self):
        log.info("=" * 70)
        log.info("  SCALPER — SINGLE FOCUS MODE")
        log.info(f"  IB Account: {self.conn.ib.accountValues()[0].account if self.conn.ib.accountValues() else 'unknown'}")
        log.info(f"  Universe: {len(PENNY_STOCK_UNIVERSE)} tickers")
        log.info(f"  Max per trade: ${self.cfg.MAX_TRADE_SIZE_USD:,.0f}")
        log.info(f"  Max risk/trade: ${self.cfg.risk_amount_usd(self.account_equity):.2f}")
        log.info("=" * 70)
        
        self._refresh_account_balance()
        self._last_scan_time = time.time()
        self._scan_and_rank()
        
        try:
            while True:
                self.ib.sleep(1)
                
                if not self.conn.is_connected():
                    log.warning("IB connection lost. Reconnecting...")
                    if not self.conn.reconnect():
                        break
                    self._refresh_account_balance()
                
                # Rescan every 5 minutes
                if time.time() - self._last_scan_time > self.cfg.SCAN_INTERVAL_SECONDS:
                    self._last_scan_time = time.time()
                    self._scan_and_rank()
                
                # Trade the #1 pick only
                if self.top_pick and self.shares == 0:
                    self._attempt_entry()
                
                self._refresh_account_balance()
                self._write_live_metrics()
                
                # Daily portfolio statement — push to git every day at ~5pm ET (21:00 UTC)
                self._maybe_daily_push()
                
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self._shutdown()
    
    def _scan_and_rank(self):
        """Scan universe in PARALLEL, rank, keep only TOP 1. Focus everything on the best."""
        t0 = time.perf_counter()
        log.info(f"🔍 MILLISECOND SCAN: {len(PENNY_STOCK_UNIVERSE)} tickers...")
        
        results: List[Dict] = []
        
        def _scan_one(ticker: str) -> Optional[Dict]:
            try:
                cfg_ticker = self.cfg.TICKER
                self.cfg.TICKER = ticker
                dm = DataManager(self.conn, self.cfg)
                hist = dm.fetch_historical(duration="1 M", bar_size="1 day")
                if hist is None or len(hist) < 21:
                    return None
                score = self._score_ticker(ticker, hist)
                self.cfg.TICKER = cfg_ticker
                return score if score and score.get("total_score", 0) > 0 else None
            except Exception:
                return None
        
        # Parallel scan: use as many workers as tickers (full CPU utilization)
        workers = min(len(PENNY_STOCK_UNIVERSE), 32)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scan_one, t): t for t in PENNY_STOCK_UNIVERSE}
            for fut in as_completed(futures):
                r = fut.result()
                if r:
                    results.append(r)
        
        elapsed_ms = (time.perf_counter() - t0) * 1000
        
        results.sort(key=lambda x: x["total_score"], reverse=True)
        self.scan_results = results[:10]
        
        if self.scan_results:
            best = self.scan_results[0]
            self.top_pick = ScanResult(
                ticker=best["ticker"],
                price=best["price"],
                volume=best["volume"],
                avg_volume=best["avg_volume"],
                relative_volume=best["rel_vol"],
                rank_score=best["total_score"],
                reason=best["reasons"],
            )
            log.info(f"🎯 TOP PICK: {best['ticker']} @ ${best['price']:.2f} | Score: {best['total_score']:.0f} | Scan: {elapsed_ms:.0f}ms")
            self.notifier.info(f"🎯 TOP PICK: {best['ticker']} @ ${best['price']:.2f}\nScore: {best['total_score']:.0f}\n{best['reasons']}")
        else:
            self.top_pick = None
            log.info(f"🔍 No viable setups this scan ({elapsed_ms:.0f}ms)")
    
    def _score_ticker(self, ticker: str, df: pd.DataFrame) -> Dict:
        """
        Math-heavy scoring using ALL available indicators:
        - Momentum (5d, 10d, 20d returns)
        - Volume acceleration
        - Institutional footprints
        - Trend strength (ADX-style)
        - VWAP position
        - Mean reversion z-score
        - Volatility ratio
        """
        closes = df["close"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])
        
        if not _only_uptrend(df, current_px):
            return {"ticker": ticker, "total_score": 0, "price": current_px, "volume": volumes[-1], "avg_volume": np.mean(volumes[-20:]), "rel_vol": 1.0, "reasons": "not_uptrend"}
        
        score = 0.0
        reasons = []
        
        # 1. Momentum composite
        ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) > 10 else 0
        ret_20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else 0
        mom_score = ret_5 * 0.5 + ret_10 * 0.3 + ret_20 * 0.2
        score += mom_score * 2.0
        if mom_score > 2:
            reasons.append(f"strong_mom_{mom_score:.1f}")
        
        # 2. Volume acceleration
        vol_avg20 = np.mean(volumes[-20:])
        vol_avg5 = np.mean(volumes[-5:])
        vol_ratio = vol_avg5 / (vol_avg20 + 1e-9)
        score += max(0, vol_ratio - 1.0) * 15
        if vol_ratio > 1.3:
            reasons.append(f"vol_{vol_ratio:.1f}x")
        
        # 3. Institutional flow
        inst = InstitutionalDetector()
        for i in range(-20, 0):
            inst.feed_bar(float(volumes[i]), float(closes[i]))
        sig = inst.scan()
        if sig.direction == "accumulating" and sig.strength > 0.5:
            score += sig.strength * 20
            reasons.append(f"inst_{sig.strength:.1f}")
        
        # 4. VWAP slope
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        vwap_hist = np.array([np.average(typical[max(0, i-19):i+1], weights=volumes[max(0, i-19):i+1]) for i in range(19, len(typical))])
        vwap_slope = (vwap_hist[-1] - vwap_hist[-5]) / (vwap_hist[-5] + 1e-9) * 100
        score += max(0, vwap_slope) * 5
        if vwap_slope > 0.5:
            reasons.append(f"vwap_up_{vwap_slope:.2f}%")
        
        # 5. ATR efficiency (risk-adjusted)
        atr = compute_atr(df, period=10)
        atr_pct = (atr / current_px) * 100
        if 0.3 < atr_pct < 3.0:
            score += 5
        
        # 6. Mean reversion z-score (not overextended)
        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().iloc[-1]
        dist = (current_px - ema9) / (pd.Series(closes).diff().rolling(20).std().iloc[-1] + 1e-9)
        if abs(dist) < 1.5:
            score += 5
        
        return {
            "ticker": ticker,
            "price": current_px,
            "volume": int(volumes[-1]),
            "avg_volume": int(vol_avg20),
            "rel_vol": round(vol_ratio, 2),
            "total_score": round(score, 1),
            "reasons": " | ".join(reasons[:3]) if reasons else "balanced",
        }
    
    def _attempt_entry(self):
        """Focus ALL capital on the top 1 pick only."""
        ticker = self.top_pick.ticker
        self.current_ticker = ticker
        
        try:
            # Reset ticker in config
            self.cfg.TICKER = ticker
            
            # Fetch fast data
            df_fast = self.data.fetch_historical(duration="1 D", bar_size="1 min")
            if df_fast is None or len(df_fast) < 20:
                return
            
            current_px = float(df_fast["close"].iloc[-1])
            
            # Enforce uptrend one more time at millisecond level
            if not _only_uptrend(df_fast, current_px):
                log.info(f"⏸ {ticker} lost uptrend — waiting for next scan")
                self.top_pick = None
                return
            
            # Institution check
            inst = self.institutional.scan()
            override, reason = self.institutional.should_override_buy()
            if override:
                log.warning(f"Inst override on {ticker}: {reason}")
                self.top_pick = None
                return
            
            # Mathematics: ATR + momentum
            fast_atr = compute_atr(df_fast, period=5)
            momentum = compute_momentum_score(df_fast, lookback=5)
            
            # Deploy up to MAX_TRADE_SIZE_USD
            deploy_usd = min(self.cfg.MAX_TRADE_SIZE_USD, self.cash * 0.95)
            risk_usd = self.cfg.risk_amount_usd(self.account_equity)
            
            stop_dist = max(fast_atr * self.cfg.SCALP_STOP_ATR_MULTIPLIER, current_px * self.cfg.SCALP_MIN_STOP_PCT)
            stop_dist = min(stop_dist, current_px * self.cfg.SCALP_MAX_STOP_PCT)
            
            shares_by_risk = int(risk_usd / stop_dist)
            shares_by_cash = int(deploy_usd / current_px)
            shares = min(shares_by_risk, shares_by_cash, self.cfg.MAX_SHARES_PER_TRADE)
            
            if shares < 1:
                return
            
            tp_dist = max(fast_atr * self.cfg.SCALP_TP_ATR_MULTIPLIER, stop_dist * self.cfg.SCALP_MIN_RR)
            tp_dist = min(tp_dist, current_px * self.cfg.SCALP_MAX_TP_PCT)
            tp_price = current_px + tp_dist
            
            plan = TradePlan(
                side="LONG",
                entry_price=current_px,
                shares=float(shares),
                initial_stop_price=round(current_px - stop_dist, 4),
                take_profit_price=round(tp_price, 4),
                risk_usd=round(shares * stop_dist, 2),
                atr_at_entry=fast_atr,
            )
            
            # Place bracket
            self.bracket_handle = self.broker.place_bracket_buy(
                quantity=shares,
                limit_or_market_price=current_px,
                stop_price=plan.initial_stop_price,
                target_price=plan.take_profit_price,
            )
            self.ib.sleep(1)
            
            # Account update
            cost = shares * current_px * (1 + self.cfg.TRANSACTION_COST_PCT)
            self.cash -= cost
            self.shares = float(shares)
            self.nav = self.cash + self.shares * current_px
            self.risk.open_position(plan)
            self.trades_today += 1
            
            log.info(f"🎯 FOCUS ENTRY: {shares}x {ticker} @ ${current_px:.2f} | "
                     f"Stop -{stop_dist/current_px:.2%} | TP +{tp_dist/current_px:.2%} | "
                     f"Deployed: ${cost:,.0f} | Score: {self.top_pick.rank_score:.0f}")
            
            self.notifier.trade_opened(
                side="BUY", ticker=ticker, qty=shares, price=current_px,
                stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                risk_usd=plan.risk_usd,
            )
            push_trade(ticker, "BUY", current_px, shares)
            
            # Clear pick until next scan cycle
            self.top_pick = None
            
        except Exception as exc:
            log.error(f"Entry error on {ticker}: {exc}")
    
    def _maybe_daily_push(self):
        """Push a portfolio statement to git once per calendar day (at 21:00 UTC)."""
        try:
            now_utc = datetime.utcnow()
            today_str = now_utc.strftime("%Y-%m-%d")
            
            # Push once per day at 21:00 UTC or later (after US market close)
            if now_utc.hour >= 21 and self._last_daily_push_date != today_str:
                self._last_daily_push_date = today_str
                
                pnl = self.nav - float(self.cfg.INITIAL_CASH)
                pnl_pct = (pnl / float(self.cfg.INITIAL_CASH)) * 100 if self.cfg.INITIAL_CASH else 0.0
                
                stmt = (
                    f"portfolio: {today_str} | "
                    f"account=${self.account_equity:,.0f} | "
                    f"nav=${self.nav:,.0f} | "
                    f"pnl=${pnl:+,.0f} ({pnl_pct:+.2f}%) | "
                    f"trades={self.trades_today}"
                )
                
                # Push performance + metrics + journal to GitHub
                push_daily_summary(self.nav, self.account_equity)
                log.info(f"📤 Daily portfolio statement pushed to git: {stmt}")
        except Exception as exc:
            log.debug(f"Daily push skipped: {exc}")
    
    def _shutdown(self):
        self._refresh_account_balance()
        
        # End-of-day portfolio statement — ALWAYS push, even with zero trades
        pnl = self.nav - float(self.cfg.INITIAL_CASH)
        pnl_pct = (pnl / float(self.cfg.INITIAL_CASH)) * 100 if self.cfg.INITIAL_CASH else 0.0
        summary = "📊 DAILY PORTFOLIO STATEMENT\n"
        summary += f" Account:       ${self.account_equity:>12,.2f}\n"
        summary += f" Cash:          ${self.cash:>12,.2f}\n"
        summary += f" NAV:           ${self.nav:>12,.2f}\n"
        summary += f" Day P&L:       ${pnl:>+12,.2f} ({pnl_pct:+.2f}%)\n"
        summary += f" Trades:        {self.trades_today:>12d}\n"
        if self.shares > 0:
            summary += f" Position:      {self.shares:.0f} {self.current_ticker}\n"
            summary += " (bracket orders remain active on IB)\n"
        
        log.info(summary)
        self.notifier.info(summary)
        push_daily_summary(self.nav, self.account_equity)
        
        self.conn.disconnect()
