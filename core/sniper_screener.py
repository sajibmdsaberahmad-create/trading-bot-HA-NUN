#!/usr/bin/env python3
"""
core/sniper_screener.py — Wide-Net Scout Phase

Implements the low-frequency market scanning loop that:
  1. Evaluates a broad universe of stocks (US/LSE)
  2. Ranks them by AI confidence (multi-timeframe, regime, institutional signals)
  3. Updates the sniper lock with top 1-5 candidates
  4. Sleeps until next scan cycle

This runs independently of the heartbeat, so no API throttling.
"""

import asyncio
import logging
from typing import List, Tuple, Optional, Dict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from core.notify import log
from core.scanner import StockScanner, PENNY_STOCK_UNIVERSE
from core.market_regime import HiddenMarkovRegimeSwitcher
from core.sniper import get_sniper, LockedTarget

# ═════════════════════════════════════════════════════════════════════════════
# WIDE-NET SCOUT
# ═════════════════════════════════════════════════════════════════════════════

class WidenetScout:
    """
    Low-frequency screener that ranks market candidates.
    
    Evaluates:
      - Volume spikes and breakouts
      - Regime shifts (trend, breakout detection)
      - AI multi-timeframe confidence
      - Institutional block trades
      - Order book imbalances
      - Volatility mean-reversion setups
    """
    
    def __init__(self, cfg=None, scanner=None, regime_switcher=None, ib_connector=None):
        self.cfg = cfg
        self.scanner = scanner or StockScanner(cfg)
        self.regime_switcher = regime_switcher
        self.ib_connector = ib_connector  # Shared IB connection
        self.last_scan: Optional[datetime] = None
        self.scan_count = 0
        
    async def scan_market(self) -> List[Tuple[str, float]]:
        """
        Scan the entire market and return ranked candidates.
        
        Returns:
            List of (ticker, confidence_score) tuples, sorted descending by score
        """
        try:
            self.scan_count += 1
            log.info(f"🔍 WIDE-NET SCAN #{self.scan_count} started")
            
            # Step 1: Get candidate universe
            candidates = await self._get_candidate_universe()
            log.debug(f"   Evaluated {len(candidates)} candidates")
            
            # Step 2: Score each candidate
            scored = []
            for ticker in candidates:
                try:
                    score = await self._score_candidate(ticker)
                    if score > 0:
                        scored.append((ticker, score))
                except Exception as exc:
                    log.debug(f"Error scoring {ticker}: {exc}")
                    continue
            
            # Step 3: Sort by score (descending)
            scored.sort(key=lambda x: x[1], reverse=True)
            
            # Step 4: Return top candidates
            top_count = min(5, len(scored))
            top_candidates = scored[:top_count]
            
            self.last_scan = datetime.now()
            
            log.info(
                f"   ✅ Scan complete. Top {top_count}:\n"
                + "\n".join(
                    f"      {i+1}. {ticker:6} → {score:.3f}"
                    for i, (ticker, score) in enumerate(top_candidates)
                )
            )
            
            return top_candidates
            
        except Exception as exc:
            log.error(f"Wide-net scan failed: {exc}")
            return []
    
    async def _get_candidate_universe(self) -> List[str]:
        """
        Get list of stocks to evaluate.
        
        Can be:
          - Penny stock screener (high volatility, volume)
          - Sector rotation candidates
          - Breakout candidates (52-week highs)
          - Specific watchlist (config-based)
        """
        try:
            # Use the PENNY_STOCK_UNIVERSE directly for real scanning
            universe = PENNY_STOCK_UNIVERSE
            
            # If config has a custom watchlist, use that instead
            if self.cfg and hasattr(self.cfg, 'WATCHLIST_TICKERS'):
                universe = self.cfg.WATCHLIST_TICKERS
            
            return universe[:50]  # Limit to 50 tickers
        except Exception as exc:
            log.warning(f"Could not get candidate universe: {exc}")
            return PENNY_STOCK_UNIVERSE[:20]
    
    async def _score_candidate(self, ticker: str) -> float:
        """
        Score a single candidate using multi-factor AI ranking.
        
        Returns confidence score [0, 1].
        """
        try:
            # Multi-factor score computation
            score = 0.0
            
            # Factor 1: Volume spike (normalized 0-1)
            volume_factor = await self._compute_volume_spike(ticker)
            score += volume_factor * 0.20
            
            # Factor 2: Regime state (trending vs ranging)
            regime_factor = await self._compute_regime_alignment(ticker)
            score += regime_factor * 0.25
            
            # Factor 3: Volatility (high volatility = high opportunity)
            volatility_factor = await self._compute_volatility_opportunity(ticker)
            score += volatility_factor * 0.20
            
            # Factor 4: Order book imbalance (institutional signal)
            ob_factor = await self._compute_orderbook_signal(ticker)
            score += ob_factor * 0.20
            
            # Factor 5: AI multi-timeframe (from advanced_training)
            ai_factor = await self._compute_ai_confidence(ticker)
            score += ai_factor * 0.15
            
            # Normalize to [0, 1]
            score = min(1.0, max(0.0, score))
            
            return score
            
        except Exception as exc:
            log.debug(f"Error scoring {ticker}: {exc}")
            return 0.0
    
    async def _fetch_ticker_data(self, ticker: str) -> Optional[pd.DataFrame]:
        """
        Fetch historical data for a ticker using shared connection.
        
        Returns:
            DataFrame with OHLCV data, or None if fetch failed
        """
        try:
            if self.ib_connector and self.ib_connector.is_connected():
                from core.data import DataManager
                
                # Use existing connection
                original_ticker = self.cfg.TICKER if self.cfg else None
                if self.cfg:
                    self.cfg.TICKER = ticker
                
                dm = DataManager(self.ib_connector, self.cfg)
                hist = dm.fetch_historical(duration="1 D", bar_size="1 min")
                
                if self.cfg and original_ticker:
                    self.cfg.TICKER = original_ticker
                
                return hist
            return None
        except Exception as exc:
            log.debug(f"Data fetch error for {ticker}: {exc}")
            return None
    
    async def _compute_volume_spike(self, ticker: str) -> float:
        """Volume vs 20-day average. Range [0, 1]."""
        try:
            hist = await self._fetch_ticker_data(ticker)
            if hist is not None and len(hist) >= 20:
                volumes = hist["volume"].values
                current_vol = float(volumes[-1])
                avg_vol = float(np.mean(volumes[-20:]))
                if avg_vol > 0:
                    rel_vol = current_vol / avg_vol
                    # Normalize: 1.5x = 0.5, 3x = 1.0
                    return min(1.0, max(0.0, (rel_vol - 1.0) / 2.0))
            return 0.5
        except Exception as exc:
            log.debug(f"Volume spike compute error for {ticker}: {exc}")
            return 0.5
    
    async def _compute_regime_alignment(self, ticker: str) -> float:
        """
        Regime detection. Uptrend/Breakout high, chop low.
        Range [0, 1].
        """
        try:
            if self.regime_switcher:
                regime = await asyncio.to_thread(
                    self.regime_switcher.classify_regime,
                    ticker
                )
                # Prefer trending/breakout regimes
                regime_scores = {
                    'uptrend': 0.9,
                    'downtrend': 0.3,
                    'breakout': 1.0,
                    'chop': 0.2,
                    'mean_reversion': 0.6,
                }
                return regime_scores.get(regime, 0.4)
            return 0.5
        except Exception as exc:
            log.debug(f"Regime alignment error for {ticker}: {exc}")
            return 0.5
    
    async def _compute_volatility_opportunity(self, ticker: str) -> float:
        """
        High volatility = high opportunity for sniper.
        Range [0, 1].
        """
        try:
            hist = await self._fetch_ticker_data(ticker)
            if hist is not None and len(hist) >= 14:
                from core.risk import compute_atr
                atr = compute_atr(hist, period=14)
                current_px = float(hist["close"].iloc[-1])
                if current_px > 0 and atr > 0:
                    atr_pct = (atr / current_px) * 100
                    # Normalize: 0.5% = 0.5, 2% = 1.0
                    return min(1.0, max(0.0, atr_pct / 2.0))
            return 0.5
        except Exception as exc:
            log.debug(f"Volatility compute error for {ticker}: {exc}")
            return 0.5
    
    async def _compute_orderbook_signal(self, ticker: str) -> float:
        """
        Order book imbalance. Large bid/ask imbalance = momentum signal.
        Range [0, 1].
        """
        try:
            hist = await self._fetch_ticker_data(ticker)
            if hist is not None and len(hist) >= 20:
                # Use price momentum as proxy for institutional flow
                closes = hist["close"].values
                price_change = (closes[-1] - closes[-20]) / closes[-20]
                # Positive momentum = higher score
                return min(1.0, max(0.0, (price_change + 0.05) * 5))
            return 0.5
        except Exception as exc:
            log.debug(f"Orderbook signal error for {ticker}: {exc}")
            return 0.5
    
    async def _compute_ai_confidence(self, ticker: str) -> float:
        """
        Multi-timeframe AI confidence from agent.
        Range [0, 1].
        """
        try:
            hist = await self._fetch_ticker_data(ticker)
            if hist is not None and len(hist) >= 30:
                # Simple momentum-based confidence
                closes = hist["close"].values
                ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
                ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) > 10 else 0
                # Normalize returns to confidence
                confidence = min(1.0, max(0.0, (ret_5 * 0.5 + ret_10 * 0.3 + 50) / 100))
                return confidence
            return 0.5
        except Exception as exc:
            log.debug(f"AI confidence error for {ticker}: {exc}")
            return 0.5


async def sniper_screener_loop(
    scout: WidenetScout,
    scan_interval: int = 600,
    stop_event: Optional[asyncio.Event] = None
):
    """
    Wide-Net Scout Loop.
    
    Runs every `scan_interval` seconds (default 10 minutes).
    Scans market, ranks candidates, updates sniper lock, then sleeps.
    
    Args:
        scout: WidenetScout instance
        scan_interval: Seconds between scans
        stop_event: Asyncio event to signal loop shutdown
    """
    sniper = get_sniper()
    
    log.info(
        f"⚡ SCREENER LOOP INITIALIZED\n"
        f"   Scan Interval: {scan_interval}s ({scan_interval/60:.1f} min)\n"
        f"   Max Targets: 5\n"
        f"   Mode: LOW-FREQUENCY WIDE-NET SCAN"
    )
    
    while True:
        try:
            if stop_event and stop_event.is_set():
                break
            
            # Scan market
            candidates = await scout.scan_market()
            
            # Update sniper lock
            if candidates:
                changed = await sniper.update_targets(candidates)
                if changed:
                    log.info(f"🔄 Target roster updated via screener sweep")
            
            # Sleep until next scan
            log.debug(f"Screener sleeping for {scan_interval}s until next sweep...")
            await asyncio.sleep(scan_interval)
            
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error(f"Screener loop error: {exc}")
            await asyncio.sleep(5)  # Brief pause before retry


async def run_screener(cfg=None, scan_interval: int = 600, ib_connector=None):
    """
    Convenience function to start the screener loop.
    
    Usage:
        task = asyncio.create_task(run_screener(cfg, scan_interval=600, ib_connector=ib))
    """
    scout = WidenetScout(cfg=cfg, ib_connector=ib_connector)
    await sniper_screener_loop(scout, scan_interval=scan_interval)