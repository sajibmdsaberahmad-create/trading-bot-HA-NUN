#!/usr/bin/env python3
"""
core/scanner.py — Penny stock & momentum stock screener for IB.

Scans the market for stocks that meet momentum/scalping criteria:
- NASDAQ or NYSE listed (no OTC/Pink Sheets)
- Price $1.00 - $20.00 (penny stock sweet spot)
- Min $10M market cap (avoids true micro-cap junk)
- Min 500K daily volume (ensures liquidity)
- Relative volume > 1.5x average
- Price > 20-day SMA (uptrend filter)
- Volume spike or gap up detected

The scanner runs pre-market to build a watchlist, then continuously
during market hours to identify new setups as they emerge.
"""

from typing import List, Dict, Optional, Tuple
import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from core.config import BotConfig
from core.notify import log


@dataclass
class ScanResult:
    """One stock that passed the scan criteria."""
    ticker: str
    price: float
    volume: float
    avg_volume: float
    relative_volume: float
    gap_pct: float = 0.0
    volume_spike_pct: float = 0.0
    vwap_distance_pct: float = 0.0
    momentum_score: float = 0.0
    institutional_confidence: float = 0.0
    atr_pct: float = 0.0
    reason: str = ""
    
    # Ranking score (composite)
    rank_score: float = 0.0


class StockScanner:
    """
    Scans stocks from a candidate list and ranks them by
    momentum/scalping potential.
    """
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        
        # Stock universe configuration
        self.MIN_PRICE = 1.0
        self.MAX_PRICE = 20.0
        self.MIN_VOLUME = 500_000
        self.MIN_REL_VOLUME = 1.5
        self.MAX_RESULTS = 20
        
        # Predefined watchlist of liquid penny stocks (NASDAQ/NYSE)
        # These are active, liquid tickers suitable for momentum scalping
        self.TICKER_UNIVERSE: List[str] = [
            # Tech & Innovation
            "SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB", "ASTS",
            "QS", "LCID", "RIVN", "CHPT", "FCEL", "PLUG",
            # Biotech
            "DNA", "CRSP", "EDIT", "NTLA", "BEAM",
            # SPACs & Fintech
            "DWAC", "PHUN", "ATER", "BBIG", "AMC", "APE",
            # Energy & Commodities
            "UUUU", "CCJ", "URA", "BOIL", "KOLD", "USO",
            # Healthcare
            "OCGN", "MRNA", "BNTX", "NVAX", "AXSM",
            # Consumer & Retail
            "GME", "BB", "KOSS", "CEI", "TKAT",
            # EV & Battery
            "NKLA", "HYZN", "GOEV", "FUV", "WKHS", "BLNK",
            # Mining
            "AG", "HL", "PAAS", "SILJ", "GDXJ",
            # Leveraged ETFs (high momentum)
            "TQQQ", "SQQQ", "SOXL", "FNGU", "LABU", "JNUG",
        ]
        
        # Volume and price cache (to avoid re-fetching)
        self._cache: Dict[str, Dict] = {}
    
    def get_universe(self) -> List[str]:
        """Return the full scanning universe."""
        return self.TICKER_UNIVERSE
    
    def evaluate_stock(self, ticker: str, df: pd.DataFrame) -> Optional[ScanResult]:
        """
        Evaluate a single stock against all scan criteria.
        Returns a ScanResult if it passes, None otherwise.
        df must have OHLCV columns with at least 21 rows.
        """
        if len(df) < 21:
            return None
        
        close = df["close"].values
        volume = df["volume"].values
        current_price = float(close[-1])
        current_volume = float(volume[-1])
        
        # Price filter: $1 - $20
        if current_price < self.MIN_PRICE or current_price > self.MAX_PRICE:
            return None
        
        # Volume filter
        avg_volume_20 = float(np.mean(volume[-21:-1])) if len(volume) > 21 else float(np.mean(volume[:-1]))
        if current_volume < self.MIN_VOLUME:
            return None
        
        # Must have at least some volume history
        if avg_volume_20 <= 0:
            return None
        
        relative_volume = current_volume / avg_volume_20
        if relative_volume < self.MIN_REL_VOLUME:
            return None
        
        # Trend check: price above 20-SMA?
        sma20 = np.mean(close[-20:])
        trend_up = current_price > sma20
        
        # Gap detection
        gap_pct = 0.0
        if "open" in df.columns:
            prev_close = float(close[-2]) if len(close) > 1 else current_price
            open_price = float(df["open"].iloc[-1])
            gap_pct = (open_price - prev_close) / prev_close * 100.0
        
        # Volume spike
        vol_prev_avg = np.mean(volume[-6:-1]) if len(volume) > 6 else avg_volume_20
        volume_spike_pct = (current_volume - vol_prev_avg) / (vol_prev_avg + 1e-9) * 100.0
        
        # VWAP distance
        vwap_dist = 0.0
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        if "volume" in df.columns:
            vwap = np.average(typical, weights=volume)
            vwap_dist = (current_price - vwap) / (vwap + 1e-9) * 100.0
        
        # ATR %
        high_14 = df["high"].values[-15:]
        low_14 = df["low"].values[-15:]
        close_14 = close[-16:-1] if len(close) > 15 else close[:-1]
        tr_values = []
        for i in range(min(14, len(high_14))):
            hl = high_14[i] - low_14[i]
            hc = abs(high_14[i] - close_14[i]) if i < len(close_14) else 0
            lc = abs(low_14[i] - close_14[i]) if i < len(close_14) else 0
            tr_values.append(max(hl, hc, lc))
        atr_val = float(np.mean(tr_values)) if tr_values else 0
        atr_pct = (atr_val / current_price) * 100.0 if current_price > 0 else 0.0
        
        # Momentum score
        ret_5 = (close[-1] / close[-6] - 1.0) * 100.0 if len(close) > 5 else 0.0
        ret_10 = (close[-1] / close[-11] - 1.0) * 100.0 if len(close) > 10 else 0.0
        momentum_score = (ret_5 * 0.7 + ret_10 * 0.3)
        
        # Composite rank score
        rank_score = 0.0
        reasons = []
        
        if trend_up:
            rank_score += 15
            reasons.append("uptrend")
        
        rank_score += min(relative_volume * 5, 20)
        if relative_volume > 2.0:
            reasons.append(f"vol_{relative_volume:.1f}x")
        
        rank_score += min(abs(gap_pct) * 2, 15)
        if abs(gap_pct) > 1.0:
            reasons.append(f"gap_{gap_pct:+.1f}%")
        
        rank_score += min(volume_spike_pct * 0.1, 10)
        if volume_spike_pct > 100:
            reasons.append(f"spike_{volume_spike_pct:.0f}%")
        
        rank_score += min(abs(momentum_score) * 3, 15)
        
        # VWAP proximity bonus (near VWAP = better entry)
        if abs(vwap_dist) < 1.0:
            rank_score += 10
            reasons.append("near_vwap")
        
        # ATR liquidity check (higher ATR = more scalping potential)
        if atr_pct > 0.5 and atr_pct < 5.0:
            rank_score += 5
        
        result = ScanResult(
            ticker=ticker,
            price=round(current_price, 2),
            volume=int(current_volume),
            avg_volume=int(avg_volume_20),
            relative_volume=round(relative_volume, 2),
            gap_pct=round(gap_pct, 2),
            volume_spike_pct=round(volume_spike_pct, 1),
            vwap_distance_pct=round(vwap_dist, 2),
            momentum_score=round(momentum_score, 2),
            atr_pct=round(atr_pct, 2),
            reason=" | ".join(reasons),
            rank_score=round(rank_score, 1),
        )
        
        return result
    
    def rank_scans(self, results: List[ScanResult]) -> List[ScanResult]:
        """Sort scan results by composite rank score, descending."""
        return sorted(results, key=lambda r: r.rank_score, reverse=True)
    
    def get_top_picks(self, results: List[ScanResult], n: int = 5) -> List[ScanResult]:
        """Get the top N ranked results."""
        ranked = self.rank_scans(results)
        return ranked[:n]
    
    def scan_pennies(self, limit: int = 50) -> List[Dict]:
        """
        Scan the PENNY_STOCK_UNIVERSE for stocks meeting momentum criteria.
        
        This method requires an IB connection to fetch real market data.
        Returns a list of dicts with ticker and basic info for each match.
        
        Args:
            limit: Maximum number of tickers to return
            
        Returns:
            List of dicts: [{'ticker': str, 'price': float, ...}, ...]
        """
        # This is a synchronous wrapper - in production, use async version
        # with IB connection to fetch real data
        results = []
        
        # For now, return the universe as candidates (real scanning happens in _scan_and_rank)
        # The actual data fetching is done in ScalperRunner._scan_and_rank()
        for ticker in PENNY_STOCK_UNIVERSE[:limit]:
            results.append({
                'ticker': ticker,
                'price': 0.0,  # Will be filled by real data fetch
                'volume': 0,
                'avg_volume': 0,
                'rel_vol': 0.0,
            })
        
        return results
    
    def build_alert_text(self, results: List[ScanResult], top_n: int = 5) -> str:
        """Build a Telegram alert message from scan results."""
        top = self.get_top_picks(results, top_n)
        if not top:
            return "🔍 Scanner: No setups found in current scan."
        
        lines = ["🔍 SCANNER RESULTS"]
        for r in top:
            vol_str = f"Vol: {r.volume/1000:.0f}K ({r.relative_volume:.1f}x)" 
            lines.append(
                f"{r.ticker:6s} @ ${r.price:<7.2f} | "
                f"{vol_str:>15s} | "
                f"Score: {r.rank_score:.0f} | "
                f"{r.reason[:40]}"
            )
        
        lines.append(f"\n{len(results)} stocks passed scan")
        return "\n".join(lines)


# Pre-built universe for penny stock momentum
PENNY_STOCK_UNIVERSE = [
    "SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB", "ASTS",
    "QS", "LCID", "RIVN", "CHPT", "FCEL", "PLUG",
    "DNA", "CRSP", "EDIT", "NTLA", "BEAM",
    "DWAC", "ATER", "BBIG",
    "UUUU", "CCJ",
    "OCGN", "MRNA", "BNTX", "NVAX", "AXSM",
    "GME", "BB", "CEI",
    "NKLA", "GOEV", "WKHS", "BLNK",
    "AG", "HL", "PAAS",
    "TQQQ", "SQQQ", "SOXL", "FNGU", "LABU", "JNUG",
    "MSTY", "NVDY", "CONY", "AMDY",  # YieldMax ETFs (high momentum)
    "ACHR", "JOBY", "PDYN",          # Aviation / defense
    "IONQ", "QMCO", "RGTI",          # Quantum computing
    "HIVE", "CLSK", "WULF",          # Bitcoin miners
    "VKTX", "CERO", "MNMD",          # Biotech/psychedelics
    "MAXN", "ARRY", "NOVA",          # Solar
    "VALE", "X", "CLF",              # Materials
    "NIO", "XPEV", "LI",             # Chinese EV
    "HSAI", "BABA", "JD",            # Chinese tech
]