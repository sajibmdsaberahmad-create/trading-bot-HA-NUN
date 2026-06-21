#!/usr/bin/env python3
"""
core/scalper_runner.py — HA-NUN single-focus institutional scalper.

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

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # fallback

MARKET_TZ = ZoneInfo("America/New_York")

import numpy as np
import pandas as pd
import requests

# US market holidays (approximate - will verify via internet on startup)
US_MARKET_HOLIDAYS = {
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}

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
        
        # IB account state (full account from IB)
        self.account_equity = float(cfg.INITIAL_CASH)
        self.available_cash: Optional[float] = None
        self.cash = float(cfg.INITIAL_CASH)
        
        # Bot's own financial state (starts at INITIAL_CASH, changes ONLY via trades)
        self.bot_cash: float = float(cfg.INITIAL_CASH)
        self.shares: float = 0.0
        self.bot_nav: float = float(cfg.INITIAL_CASH)
        self.current_ticker: Optional[str] = None
        self.bracket_handle: Optional[BracketHandle] = None
        
        # IB account tracking (real P&L impact)
        self._ib_starting_balance: Optional[float] = None
        
        # Track previous shares to detect exits
        self._prev_shares: float = 0.0
        self._entry_price: float = 0.0
        
        self.scan_results: List[ScanResult] = []
        self.top_pick: Optional[ScanResult] = None
        self._last_scan_time: float = 0.0
        self._last_metrics_write: float = 0.0
        
        self.trade_journal: List[Dict] = []
        self.trades_today: int = 0
        self._current_day: Optional[str] = None
        self._last_daily_push_date: Optional[str] = None
        self._weights_file = "models/scalper_weights.json"
    
    def _refresh_account_balance(self):
        """Pull live balance from IB. Bot state changes ONLY via trades."""
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
            if self._ib_starting_balance is None:
                self._ib_starting_balance = self.account_equity
            self.cfg._latest_account_balance = self.account_equity
        except Exception as exc:
            log.debug(f"Could not fetch IB account balance: {exc}")
        self.bot_nav = self.bot_cash + self.shares * self._latest_price()
    
    def _latest_price(self) -> float:
        try:
            return self.data.get_latest_price() or 0.0
        except Exception:
            return 0.0
    
    def _detect_exit(self, current_px: float):
        """Detect if position was closed (by bracket or manually) and notify."""
        if self._prev_shares > 0 and self.shares == 0:
            # Position closed
            pnl = (current_px - self._entry_price) * self._prev_shares
            pnl_pct = ((current_px / self._entry_price) - 1) * 100 if self._entry_price else 0
            result = "win" if pnl > 0 else "loss"
            log.info(f"📕 EXIT: {self.current_ticker} @ ${current_px:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | {result.upper()}")
            self.notifier.info(
                f"📕 HA-NUN EXIT\n"
                f"Ticker: {self.current_ticker}\n"
                f"Exit: ${current_px:.2f}\n"
                f"Entry: ${self._entry_price:.2f}\n"
                f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                f"Result: {result.upper()}"
            )
            # Save to trade journal for self-training
            self.trade_journal.append({
                "ticker": self.current_ticker,
                "entry": self._entry_price,
                "exit": current_px,
                "shares": self._prev_shares,
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "result": result,
            })
        self._prev_shares = self.shares
        if self.shares > 0:
            self._entry_price = current_px
    
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
                "mode": "HA-NUN",
                "account_equity": round(self.account_equity, 2),
                "available_cash": round(self.available_cash or 0, 2),
                "position_value": round(self.shares * self._latest_price(), 2),
                "nav": round(self.bot_nav, 2),
                "deployed_pct": round((self.bot_nav - (self.available_cash or 0)) / (self.account_equity + 1e-9) * 100, 1),
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
        log.info("  HA-NUN — SINGLE FOCUS SCALPER")
        acct = self.conn.ib.accountValues()
        log.info(f"  IB Account: {acct[0].account if acct else 'unknown'}")
        log.info(f"  Universe: {len(PENNY_STOCK_UNIVERSE)} tickers")
        log.info(f"  Max per trade: ${self.cfg.MAX_TRADE_SIZE_USD:,.0f}")
        log.info(f"  Max risk/trade: ${self.cfg.risk_amount_usd(self.account_equity):.2f}")
        log.info(f"  Baseline:      ${self.cfg.INITIAL_CASH:,.2f}")
        log.info("=" * 70)
        self.notifier.info("🚀 HA-NUN STARTED\nSingle-focus scalper active.\nScanning for setups...")
        
        self._refresh_account_balance()
        if self._ib_starting_balance:
            log.info(f"  IB Starting Balance: ${self._ib_starting_balance:,.2f}")
        
        # Check market status
        market_open, market_reason = self._is_market_open()
        if market_open:
            log.info(f"  ✅ {market_reason}")
            self.notifier.info(f"✅ HA-NUN MARKET STATUS\n{market_reason}\nStarting scalper...")
        else:
            log.warning(f"  ⚠️ {market_reason}")
            self.notifier.warning(f"⚠️ HA-NUN MARKET STATUS\n{market_reason}\nBot will train offline until market opens.")
        
        self._last_scan_time = time.time()
        self._scan_and_rank()
        
        try:
            while True:
                self.ib.sleep(1)
                current_px = self._latest_price()
                
                if not self.conn.is_connected():
                    log.warning("IB connection lost. Reconnecting...")
                    if not self.conn.reconnect():
                        break
                    self._refresh_account_balance()
                
                # Detect exits (bracket orders hitting stop/target)
                self._detect_exit(current_px)
                
                # Check market status every 60 seconds
                market_open, market_reason = self._is_market_open()
                
                if market_open:
                    # Market is open: scan and trade
                    if self.top_pick is None and self.shares == 0:
                        if time.time() - self._last_scan_time > 1:
                            self._last_scan_time = time.time()
                            self._scan_and_rank()
                    elif time.time() - self._last_scan_time > self.cfg.SCAN_INTERVAL_SECONDS:
                        self._last_scan_time = time.time()
                        self._scan_and_rank()
                    
                    if self.top_pick and self.shares == 0:
                        self._attempt_entry()
                else:
                    # Market is closed: train instead of scan
                    if int(time.time()) % 60 == 0:  # log every minute
                        log.info(f"⏸ MARKET CLOSED: {market_reason} — training instead")
                    if time.time() - self._last_scan_time > 300:  # train every 5 min
                        self._last_scan_time = time.time()
                        self._train_off_hours()
                
                self._refresh_account_balance()
                self._write_live_metrics()
                self._maybe_daily_push()
                
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self._shutdown()
    
    def _scan_and_rank(self):
        t0 = time.perf_counter()
        # Use a smaller active screen to reduce IB load; not the full universe every scan
        screen_list = getattr(self.cfg, "SCAN_UNIVERSE", PENNY_STOCK_UNIVERSE[:40])
        log.info(f"🔍 HA-NUN SCAN: {len(screen_list)} tickers (screened subset)...")
        results: List[Dict] = []

        def _scan_one(ticker: str) -> Optional[Dict]:
            try:
                cfg_ticker = self.cfg.TICKER
                self.cfg.TICKER = ticker
                dm = DataManager(self.conn, self.cfg)
                # Institutional momentum shows on 1-min bars; fetch 1 day for tape heartbeat
                hist = dm.fetch_historical(duration="1 D", bar_size="1 min")
                if hist is None or len(hist) < 60:
                    return None
                score = self._score_ticker(ticker, hist)
                self.cfg.TICKER = cfg_ticker
                return score if score and score.get("total_score", 0) > 0 else None
            except Exception:
                return None

        # Sequential-ish with small worker count to avoid IB rate limits
        workers = min(len(screen_list), 6)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scan_one, t): t for t in screen_list}
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
                ticker=best["ticker"], price=best["price"], volume=best["volume"],
                avg_volume=best["avg_volume"], relative_volume=best["rel_vol"],
                rank_score=best["total_score"], reason=best["reasons"],
            )
            log.info(f"🎯 TOP PICK: {best['ticker']} @ ${best['price']:.2f} | Score: {best['total_score']:.0f} | Scan: {elapsed_ms:.0f}ms")
            self.notifier.info(f"🎯 TOP PICK: {best['ticker']} @ ${best['price']:.2f}\nScore: {best['total_score']:.0f}\n{best['reasons']}")
        else:
            self.top_pick = None
            log.info(f"🔍 No setups — rescanning in 1s ({elapsed_ms:.0f}ms)")
    
    def _score_ticker(self, ticker: str, df: pd.DataFrame) -> Dict:
        closes = df["close"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])
        if not _only_uptrend(df, current_px):
            return {"ticker": ticker, "total_score": 0, "price": current_px, "volume": volumes[-1], "avg_volume": np.mean(volumes[-20:]), "rel_vol": 1.0, "reasons": "not_uptrend"}
        score = 0.0
        reasons = []
        ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) > 10 else 0
        ret_20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else 0
        mom_score = ret_5 * 0.5 + ret_10 * 0.3 + ret_20 * 0.2
        score += mom_score * 2.0
        if mom_score > 2:
            reasons.append(f"strong_mom_{mom_score:.1f}")
        vol_avg20 = np.mean(volumes[-20:])
        vol_avg5 = np.mean(volumes[-5:])
        vol_ratio = vol_avg5 / (vol_avg20 + 1e-9)
        score += max(0, vol_ratio - 1.0) * 15
        if vol_ratio > 1.3:
            reasons.append(f"vol_{vol_ratio:.1f}x")
        inst = InstitutionalDetector()
        for i in range(-20, 0):
            inst.feed_bar(float(volumes[i]), float(closes[i]))
        sig = inst.scan()
        if sig.direction == "accumulating" and sig.strength > 0.5:
            score += sig.strength * 20
            reasons.append(f"inst_{sig.strength:.1f}")
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        vwap_hist = np.array([np.average(typical[max(0, i-19):i+1], weights=volumes[max(0, i-19):i+1]) for i in range(19, len(typical))])
        vwap_slope = (vwap_hist[-1] - vwap_hist[-5]) / (vwap_hist[-5] + 1e-9) * 100
        score += max(0, vwap_slope) * 5
        if vwap_slope > 0.5:
            reasons.append(f"vwap_up_{vwap_slope:.2f}%")
        atr = compute_atr(df, period=10)
        atr_pct = (atr / current_px) * 100
        if 0.3 < atr_pct < 3.0:
            score += 5
        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().iloc[-1]
        dist = (current_px - ema9) / (pd.Series(closes).diff().rolling(20).std().iloc[-1] + 1e-9)
        if abs(dist) < 1.5:
            score += 5
        return {
            "ticker": ticker, "price": current_px, "volume": int(volumes[-1]),
            "avg_volume": int(vol_avg20), "rel_vol": round(vol_ratio, 2),
            "total_score": round(score, 1), "reasons": " | ".join(reasons[:3]) if reasons else "balanced",
        }
    
    def _attempt_entry(self):
        ticker = self.top_pick.ticker
        self.current_ticker = ticker
        try:
            self.cfg.TICKER = ticker
            df_fast = self.data.fetch_historical(duration="1 D", bar_size="1 min")
            if df_fast is None or len(df_fast) < 20:
                return
            current_px = float(df_fast["close"].iloc[-1])
            if not _only_uptrend(df_fast, current_px):
                self.top_pick = None
                return
            inst = self.institutional.scan()
            override, reason = self.institutional.should_override_buy()
            if override:
                self.top_pick = None
                return
            fast_atr = compute_atr(df_fast, period=5)
            momentum = compute_momentum_score(df_fast, lookback=5)
            deploy_usd = min(self.cfg.MAX_TRADE_SIZE_USD, self.bot_cash * 0.95)
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
                side="LONG", entry_price=current_px, shares=float(shares),
                initial_stop_price=round(current_px - stop_dist, 4),
                take_profit_price=round(tp_price, 4),
                risk_usd=round(shares * stop_dist, 2), atr_at_entry=fast_atr,
            )
            self.bracket_handle = self.broker.place_bracket_buy(
                quantity=shares, limit_or_market_price=current_px,
                stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
            )
            self.ib.sleep(1)
            cost = shares * current_px * (1 + self.cfg.TRANSACTION_COST_PCT)
            self.bot_cash -= cost
            self.shares = float(shares)
            self.bot_nav = self.bot_cash + self.shares * current_px
            self._entry_price = current_px
            self._prev_shares = self.shares
            self.risk.open_position(plan)
            self.trades_today += 1
            log.info(f"🎯 FOCUS ENTRY: {shares}x {ticker} @ ${current_px:.2f} | Stop -{stop_dist/current_px:.2%} | TP +{tp_dist/current_px:.2%} | Deployed: ${cost:,.0f} | Score: {self.top_pick.rank_score:.0f}")
            self.notifier.info(
                f"🎯 HA-NUN ENTRY\n"
                f"Ticker: {ticker}\n"
                f"Qty: {shares}\n"
                f"Entry: ${current_px:.2f}\n"
                f"Stop: ${plan.initial_stop_price:.2f}\n"
                f"Target: ${plan.take_profit_price:.2f}\n"
                f"Deployed: ${cost:,.0f}\n"
                f"Score: {self.top_pick.rank_score:.0f}"
            )
            push_trade(ticker, "BUY", current_px, shares)
            self.top_pick = None
        except Exception as exc:
            log.error(f"Entry error on {ticker}: {exc}")
    
    @staticmethod
    def _is_market_open() -> Tuple[bool, str]:
        """
        Check if US market is open right now.
        Returns (is_open, reason).
        """
        now_et = datetime.now(MARKET_TZ)
        weekday = now_et.weekday()  # 0=Mon, 6=Sun
        
        # Weekend check
        if weekday >= 5:
            return False, f"Market CLOSED (weekend: {now_et.strftime('%A')})"
        
        # Holiday check (verify via internet if possible)
        date_str = now_et.strftime("%Y-%m-%d")
        if date_str in US_MARKET_HOLIDAYS:
            return False, f"Market CLOSED (holiday: {date_str})"
        
        # Try to fetch live holiday calendar from NYSE
        try:
            resp = requests.get("https://www.nyse.com/markets/hours-calendars", timeout=5)
            if resp.status_code == 200 and date_str.replace("-", "") in resp.text.replace("-", ""):
                return False, f"Market CLOSED (verified holiday via NYSE)"
        except Exception:
            pass
        
        # Time check: 9:30 AM - 4:00 PM ET
        hour = now_et.hour
        minute = now_et.minute
        current_minutes = hour * 60 + minute
        open_minutes = 9 * 60 + 30
        close_minutes = 16 * 60
        
        if current_minutes < open_minutes:
            return False, f"Market CLOSED (pre-market: {now_et.strftime('%H:%M')} ET)"
        elif current_minutes >= close_minutes:
            return False, f"Market CLOSED (after-hours: {now_et.strftime('%H:%M')} ET)"
        
        return True, f"Market OPEN ({now_et.strftime('%H:%M')} ET)"
    
    def _train_off_hours(self):
        """
        When market is closed, train on historical data instead of scanning.
        This improves the model for the next session.
        """
        try:
            log.info("🧠 OFF-HOURS TRAINING: Analyzing historical patterns...")
            
            # Fetch recent data for self-training
            train_data = []
            for ticker in PENNY_STOCK_UNIVERSE[:20]:  # subset for speed
                try:
                    self.cfg.TICKER = ticker
                    dm = DataManager(self.conn, self.cfg)
                    hist = dm.fetch_historical(duration="3 M", bar_size="1 day")
                    if hist is not None and len(hist) > 30:
                        train_data.append((ticker, hist))
                except Exception:
                    continue
            
            # Analyze patterns
            bullish_days = 0
            total_days = 0
            for ticker, hist in train_data:
                closes = hist["close"].values
                for i in range(20, len(closes)):
                    if closes[i] > closes[i-1]:
                        bullish_days += 1
                    total_days += 1
            
            if total_days > 0:
                bullish_pct = bullish_days / total_days
                log.info(f"🧠 Historical analysis: {bullish_pct:.0%} bullish days across {total_days} samples")
            
            # Train weights on historical data
            self._daily_self_train()
            
            log.info("🧠 Off-hours training complete. Ready for next session.")
            self.notifier.info("🧠 HA-NUN OFF-HOURS TRAINING\nMarket closed. Self-training on historical data.\nReady for next session.")
        except Exception as exc:
            log.debug(f"Off-hours training failed: {exc}")
    
    def _load_weights(self) -> Dict:
        try:
            with open(self._weights_file, "r") as f:
                return json.load(f)
        except Exception:
            return {"momentum": 2.0, "volume": 15.0, "institutional": 20.0, "vwap_slope": 5.0, "atr_bonus": 5.0, "mean_reversion": 5.0, "win_history": []}
    
    def _save_weights(self, weights: Dict):
        os.makedirs("models", exist_ok=True)
        with open(self._weights_file, "w") as f:
            json.dump(weights, f, indent=2)
        log.info(f"🧠 Learned weights saved -> {self._weights_file}")
    
    def _daily_self_train(self):
        try:
            weights = self._load_weights()
            # Load trade journal into win_history if not already there
            if self.trade_journal and not weights.get("win_history"):
                for trade in self.trade_journal:
                    weights["win_history"].append({
                        "result": trade["result"],
                        "pnl_usd": trade["pnl_usd"],
                        "weights_active": {k: weights.get(k, 1.0) for k in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]}
                    })
            wins = [w for w in weights.get("win_history", []) if w["result"] == "win"]
            losses = [w for w in weights.get("win_history", []) if w["result"] == "loss"]
            if wins or losses:
                win_rate = len(wins) / (len(wins) + len(losses))
                for w in weights.get("win_history", []):
                    factor = 1.15 if w["result"] == "win" else 0.85
                    for key in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]:
                        if key in w.get("weights_active", {}):
                            weights[key] = weights.get(key, 1.0) * factor
                for key in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]:
                    weights[key] = max(0.5, min(weights[key], 50.0))
                log.info(f"🧠 Self-train: win_rate={win_rate:.0%} | wins={len(wins)} losses={len(losses)} | weights updated")
            try:
                sim_scores = [r.rank_score for r in self.scan_results[:10]]
                if sim_scores:
                    max_score = max(sim_scores)
                    if max_score < 30:
                        weights["volume"] *= 1.2
                        weights["institutional"] *= 1.2
                        log.info(f"🧠 Weak top-score ({max_score:.0f}) → boosted volume+institutional weights")
            except Exception:
                pass
            self._save_weights(weights)
        except Exception as exc:
            log.debug(f"Self-train skipped: {exc}")
    
    def _generate_guidelines(self) -> str:
        try:
            weights = self._load_weights()
            win_rate = len([w for w in weights.get("win_history", []) if w["result"] == "win"]) / max(len(weights.get("win_history", [])), 1)
            rules = []
            wins = [w for w in weights.get("win_history", []) if w["result"] == "win"]
            losses = [w for w in weights.get("win_history", []) if w["result"] == "loss"]
            if win_rate < 0.4:
                rules.append("URGENT: Win rate below 40%. Tighten stop-loss (reduce SCALP_STOP_ATR_MULTIPLIER from 0.7 to 0.5).")
                rules.append("Reduce trade frequency: increase SCAN_INTERVAL_SECONDS from 300 to 600.")
            elif win_rate > 0.7:
                rules.append("Win rate excellent (>70%). Consider increasing position size or reducing SCALP_STOP_ATR_MULTIPLIER for bigger wins.")
            else:
                rules.append(f"Win rate {win_rate:.0%} — stable. Continue current risk parameters.")
            if losses:
                avg_loss = sum(l.get("pnl_usd", 0) for l in losses) / len(losses)
                if avg_loss > 30:
                    rules.append(f"Average loss ${avg_loss:.0f} is high. Consider reducing MAX_TRADE_SIZE_USD from $1,000 to $500.")
                    rules.append("Review trailing stop: tighten SCALP_TRAILING_ATR_MULTIPLIER.")
            w = weights
            if w.get("momentum", 0) > 30:
                rules.append("Momentum weight is very high — strategy is overly focused on momentum. Consider rebalancing.")
            if w.get("volume", 0) > 30:
                rules.append("Volume weight is very high — add volume_decay check to avoid chasing pumps.")
            if w.get("institutional", 0) > 30:
                rules.append("Institutional weight is very high — ensure institutional detector is accurate (check for false signals).")
            if self.scan_results:
                max_score = max(r.rank_score for r in self.scan_results[:3])
                if max_score < 20:
                    rules.append("Market conditions are weak (low scores). Consider wider SCALP_MIN_STOP_PCT or wait for better setups.")
                elif max_score > 50:
                    rules.append("Strong market conditions. Increase SCALP_MAX_TP_PCT from 3% to 5% to capture more upside.")
            if self.bot_nav > float(self.cfg.INITIAL_CASH) * 1.5:
                rules.append(f"Account grew {self.bot_nav / float(self.cfg.INITIAL_CASH):.0%}x. Consider adding a second concurrent position (MAX_CONCURRENT_POSITIONS).")
            rules.append("Always use limit orders in fast markets (USE_LIMIT_ORDERS_IN_FAST_MARKETS = True).")
            rules.append("Monitor slippage: if fills consistently >0.4%, reduce order size.")
            pnl = self.bot_nav - float(self.cfg.INITIAL_CASH)
            pnl_pct = pnl / float(self.cfg.INITIAL_CASH)
            if pnl_pct < -0.1:
                rules.append("ALERT: Drawdown >10%. Pause trading for 24 hours and review strategy.")
                rules.append("Strengthen uptrend filter: require price > SMA50 instead of SMA20.")
            if not rules:
                rules.append("No guideline changes needed. System running optimally.")
            rules_text = "\n".join(f"• {r}" for r in rules)
            return f"🧭 HA-NUN SELF-IMPROVEMENT GUIDELINES\n{'_'*40}\n{rules_text}\n"
        except Exception as exc:
            log.debug(f"Guidelines generation failed: {exc}")
            return ""
    
    def _maybe_daily_push(self):
        try:
            now_et = datetime.now(MARKET_TZ)
            today_str = now_et.strftime("%Y-%m-%d")
            market_close_hour_et = 16
            if now_et.hour >= market_close_hour_et and self._last_daily_push_date != today_str:
                self._last_daily_push_date = today_str
                self._daily_self_train()
                guidelines = self._generate_guidelines()
                baseline = float(self.cfg.INITIAL_CASH)
                pnl = self.bot_nav - baseline
                pnl_pct = (pnl / baseline) * 100 if baseline else 0.0
                stmt = (
                    f"portfolio: {today_str} ET | "
                    f"bot_nav=${self.bot_nav:,.0f} | "
                    f"baseline=${baseline:,.0f} | "
                    f"pnl=${pnl:+,.0f} ({pnl_pct:+.2f}%) | "
                    f"trades={self.trades_today}"
                )
                push_daily_summary(self.bot_nav, self.account_equity)
                try:
                    weights = self._load_weights()
                    self.cfg._latest_account_balance = self.account_equity
                    os.makedirs("models", exist_ok=True)
                    with open("models/daily_guidelines.txt", "w") as f:
                        f.write(guidelines)
                        f.write(f"\nGenerated: {now_et.isoformat()}\n")
                        f.write(f"Weights: {json.dumps(weights, indent=2)}\n")
                        f.write(f"Performance: {stmt}\n")
                    os.system(
                        f"cd {os.getcwd()} && "
                        f"git add models/scalper_weights.json models/daily_guidelines.txt && "
                        f"git commit -m 'train: ha-nun daily self-improvement {today_str}' >/dev/null 2>&1"
                    )
                except Exception:
                    pass
                log.info(f"📤 {stmt}")
                log.info(f"🧭 Guidelines generated and pushed to git")
                self.notifier.info(f"📊 HA-NUN DAILY COMPLETE\n{stmt}\n\n{guidelines}")
        except Exception as exc:
            log.debug(f"Daily push skipped: {exc}")
    
    def _shutdown(self):
        self._refresh_account_balance()
        baseline = float(self.cfg.INITIAL_CASH)
        pnl = self.bot_nav - baseline
        pnl_pct = (pnl / baseline) * 100 if baseline else 0.0
        ib_start = self._ib_starting_balance or self.account_equity
        ib_change = self.account_equity - ib_start
        ib_change_pct = (ib_change / ib_start) * 100 if ib_start else 0.0
        summary = "📊 HA-NUN DAILY STATEMENT\n"
        summary += f" IB Account:    ${self.account_equity:>12,.2f}  (start: ${ib_start:,.2f})\n"
        summary += f" IB Change:     ${ib_change:>+12,.2f} ({ib_change_pct:+.2f}%)\n"
        summary += f" Bot Cash:      ${self.bot_cash:>12,.2f}\n"
        summary += f" Bot NAV:       ${self.bot_nav:>12,.2f}\n"
        summary += f" Day P&L:       ${pnl:>+12,.2f} ({pnl_pct:+.2f}%)\n"
        summary += f" Baseline:      ${baseline:>12,.2f}\n"
        summary += f" Trades:        {self.trades_today:>12d}\n"
        if self.shares > 0:
            summary += f" Position:      {self.shares:.0f} {self.current_ticker}\n"
            summary += " (bracket orders remain active on IB)\n"
        log.info(summary)
        self.notifier.info(summary)
        push_daily_summary(self.bot_nav, self.account_equity)
        self.conn.disconnect()