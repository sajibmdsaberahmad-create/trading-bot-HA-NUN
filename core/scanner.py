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

from typing import List, Dict, Optional, Tuple, Any
import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from core.config import BotConfig
from core.notify import log
from core.risk import safe_vwap


def _req_scanner_with_timeout(ib, scan, timeout_sec: float) -> List[Any]:
    """
    Fetch scanner rows with a hard timeout.

    ``ib.reqScannerData`` blocks until IB sends scannerDataEnd and can hang
    indefinitely — this uses a subscription + chunked ``ib.sleep`` instead.
    """
    import time

    timeout_sec = max(4.0, float(timeout_sec))
    deadline = time.time() + timeout_sec
    data_list = ib.reqScannerSubscription(scan)
    last_count = 0
    stable_since = time.time()
    last_log = time.time()
    try:
        while time.time() < deadline:
            ib.sleep(0.2)
            n = len(data_list)
            now = time.time()
            if n != last_count:
                last_count = n
                stable_since = now
            elif n >= 8 and (now - stable_since) >= 1.0:
                break
            if n >= 50:
                break
            if now - last_log >= 3.0:
                log.info(
                    f"  scanner {scan.scanCode}@{scan.locationCode}: "
                    f"{n} rows (waiting on IB…)"
                )
                last_log = now
        else:
            log.warning(
                f"  scanner {scan.scanCode}@{scan.locationCode}: "
                f"timed out after {timeout_sec:.0f}s ({len(data_list)} rows)"
            )
    finally:
        try:
            ib.cancelScannerSubscription(data_list)
        except Exception:
            pass
    return list(data_list)


@dataclass
class ScannerHit:
    """Lightweight IB scanner row — no historical bars required."""
    ticker: str
    rank: int = 0
    scan_code: str = ""
    distance: str = ""
    primary_exchange: str = ""
    price: float = 0.0


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
    
    Uses IB's built-in scanners for live ticker discovery - no static list.
    """
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        
        # Stock universe configuration
        self.MIN_PRICE = 1.0
        self.MAX_PRICE = 1000.0  # No upper limit - trade any profitable stock
        self.MIN_VOLUME = 10_000  # Lower bound for liquidity detection  # Lower for small caps
        self.MIN_REL_VOLUME = 1.0
        self.MAX_RESULTS = 50  # More results for wider selection
        
        # Dynamic universe from IB scanner only (no static list)
        self._dynamic_universe: List[str] = []
        self._scanner_hits: Dict[str, ScannerHit] = {}
        self._last_dynamic_fetch: float = 0.0
        self._dynamic_fetch_interval: int = 20  # Faster refresh  # Refresh every 30 seconds

    def get_scanner_hits(self) -> Dict[str, ScannerHit]:
        """Last IB scanner metadata keyed by ticker (rank, scan code, distance)."""
        return dict(self._scanner_hits)

    @staticmethod
    def score_scanner_hit(hit: ScannerHit, list_index: int = 0) -> Dict:
        """
        Rank a ticker from IB scanner metadata only — no reqHistoricalData.
        Used for sub-10s lock; bars are prefetched after lock.
        """
        rank_bonus = max(15.0, 88.0 - hit.rank * 2.2)
        code_bonus = {
            "MOST_ACTIVE": 14.0,
            "HOT_BY_VOLUME": 12.0,
            "TOP_PERC_GAIN": 12.0,
            "HOT_BY_PRICE": 10.0,
            "TOP_VOLUME": 8.0,
        }.get(hit.scan_code, 4.0)
        try:
            from core.universe_filter import exchange_score_bonus
            code_bonus += exchange_score_bonus(hit.primary_exchange)
        except Exception:
            pass
        dist_bonus = 0.0
        if hit.distance:
            try:
                raw = str(hit.distance).replace("%", "").strip()
                dist_bonus = min(abs(float(raw)) * 2.0, 15.0)
            except (TypeError, ValueError):
                pass
        if list_index > 0 and hit.rank == 0:
            rank_bonus = max(15.0, 88.0 - list_index * 2.2)
        total = round(rank_bonus + code_bonus + dist_bonus, 1)
        reason = f"scanner:{hit.scan_code or 'live'}#{hit.rank}"
        if hit.distance:
            reason += f" dist={hit.distance}"
        return {
            "ticker": hit.ticker,
            "price": 0.0,
            "volume": 0,
            "avg_volume": 0,
            "rel_vol": 1.5,
            "total_score": total,
            "reasons": reason,
            "scanner_fast": True,
        }
    
    def get_dynamic_universe(self, ib_connector=None, force: bool = False) -> List[str]:
        """
        Fetch live tickers from IB's most active scanner.
        Returns tickers currently trading with high volume — no static fallback.
        """
        import time
        now = time.time()
        if not force and self._dynamic_universe and (now - self._last_dynamic_fetch) < self._dynamic_fetch_interval:
            return self._dynamic_universe[:100]

        if force:
            self._last_dynamic_fetch = 0.0
            self._dynamic_universe = []
            self._scanner_hits = {}

        tickers = []
        hits: Dict[str, ScannerHit] = {}
        if ib_connector and hasattr(ib_connector, 'ib') and ib_connector.ib.isConnected():
            try:
                from ib_insync import ScannerSubscription
                from core.universe_filter import PROFIT_HUNT_SCAN_CODES, passes_profit_hunt_universe
                scan_codes = list(PROFIT_HUNT_SCAN_CODES)
                ib = ib_connector.ib
                disabled_codes: set = set()
                skipped_universe = 0
                location_codes = ["STK.US.MAJOR", "STK.US"]
                deadline = now + float(getattr(self.cfg, "IB_SCANNER_TIMEOUT_SEC", 25))

                log.info(
                    f"🔍 IB live scanner starting "
                    f"(budget {getattr(self.cfg, 'IB_SCANNER_TIMEOUT_SEC', 25):.0f}s)…"
                )

                per_code_cap = float(getattr(self.cfg, "IB_SCANNER_PER_CODE_SEC", 12))

                for location_code in location_codes:
                    if tickers:
                        break
                    if time.time() > deadline:
                        log.warning("IB scanner time budget reached — using partial universe")
                        break
                    for scan_code in scan_codes:
                        if scan_code in disabled_codes:
                            continue
                        if time.time() > deadline:
                            log.warning(
                                f"IB scanner timeout before {scan_code} — "
                                f"{len(tickers)} tickers so far"
                            )
                            break
                        scan = ScannerSubscription(
                            instrument='STK',
                            locationCode=location_code,
                            scanCode=scan_code,
                            numberOfRows=50,
                        )
                        try:
                            remaining = max(4.0, deadline - time.time())
                            code_budget = min(per_code_cap, remaining)
                            log.info(
                                f"  scanner req {scan_code} @ {location_code} "
                                f"(budget {code_budget:.0f}s)…"
                            )
                            scan_results = _req_scanner_with_timeout(ib, scan, code_budget)
                        except Exception as exc:
                            if '162' in str(exc) or 'disabled' in str(exc).lower():
                                disabled_codes.add(scan_code)
                                log.debug(f"Scanner {scan_code} unavailable — skipping")
                            continue
                        for idx, result in enumerate(scan_results):
                            if not result.contractDetails or not result.contractDetails.contract:
                                continue
                            contract = result.contractDetails.contract
                            symbol = contract.symbol
                            if not symbol or len(symbol) > 5:
                                continue
                            primary = (
                                getattr(contract, "primaryExchange", "")
                                or getattr(contract, "primaryExch", "")
                                or ""
                            )
                            mpx = 0.0
                            try:
                                mpx = float(getattr(result, "benchmark", 0) or 0)
                            except (TypeError, ValueError):
                                pass
                            ok, reason = passes_profit_hunt_universe(
                                self.cfg, symbol, str(primary), price=mpx,
                            )
                            if not ok:
                                skipped_universe += 1
                                log.debug(f"  ⏭ scanner skip {symbol}@{primary}: {reason}")
                                continue
                            dist = getattr(result, "distance", "") or ""
                            prev = hits.get(symbol)
                            if prev is None or idx < prev.rank:
                                hits[symbol] = ScannerHit(
                                    ticker=symbol,
                                    rank=idx,
                                    scan_code=scan_code,
                                    distance=str(dist) if dist else "",
                                    primary_exchange=str(primary),
                                    price=mpx,
                                )
                            if symbol not in tickers:
                                tickers.append(symbol)
                        if len(tickers) >= 40:
                            break

                if tickers:
                    self._dynamic_universe = tickers[:100]
                    self._scanner_hits = hits
                    self._last_dynamic_fetch = now
                    log.info(
                        f"Dynamic universe: {len(tickers)} major-exchange tickers "
                        f"(skipped {skipped_universe} PINK/OTC/distressed)"
                    )
                else:
                    log.warning("No tickers returned from IB scanner — check market hours / subscription")
            except Exception as exc:
                log.warning(f"IB scanner error: {exc}")

        return tickers[:100]
    
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
        
        # Liquidity filter - must be tradable
        # No price limits - AI can trade any stock with sufficient liquidity
        if current_price <= 0:
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
            vwap = safe_vwap(typical.values, volume.values)
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


# Tickers that fail IB contract qualification (delisted / renamed / no data)
CONTRACT_BLACKLIST = frozenset({"MNMD", "MAXN", "NOVA", "X", "CEI", "NKLA", "GOEV"})

# Pre-built universe for penny stock momentum
PENNY_STOCK_UNIVERSE = [
    "SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB", "ASTS",
    "QS", "LCID", "RIVN", "CHPT", "FCEL", "PLUG",
    "DNA", "CRSP", "EDIT", "NTLA", "BEAM",
    "ATER",
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
    "VKTX", "CERO", "ARRY",          # Biotech / solar
    "VALE", "CLF",                   # Materials
    "NIO", "XPEV", "LI",             # Chinese EV
    "HSAI", "BABA", "JD",            # Chinese tech
]