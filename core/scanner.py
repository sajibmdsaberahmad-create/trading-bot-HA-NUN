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


def _req_scanner_with_timeout(
    ib,
    scan,
    timeout_sec: float,
    *,
    filter_options=None,
    empty_bail_sec: float = 0.0,
) -> List[Any]:
    """
    Fetch scanner rows with a hard timeout via streaming subscription.

    Avoids ``ib.run()`` / nested asyncio (breaks tick/bar streams with patchAsyncio).
    """
    import time

    timeout_sec = max(6.0, float(timeout_sec))
    empty_bail_sec = max(0.0, float(empty_bail_sec))
    scan_code = getattr(scan, "scanCode", "?")
    location = getattr(scan, "locationCode", "?")
    filters = list(filter_options or [])
    data_list = ib.reqScannerSubscription(scan, [], filters)
    deadline = time.time() + timeout_sec
    started = time.time()
    last_log = time.time()
    last_count = 0
    stable_since = time.time()
    try:
        while time.time() < deadline:
            ib.sleep(0.25)
            n = len(data_list)
            now = time.time()
            if n != last_count:
                last_count = n
                stable_since = now
            elif n >= 8 and (now - stable_since) >= 1.0:
                break
            if n >= 50:
                break
            if n == 0 and empty_bail_sec > 0 and (now - started) >= empty_bail_sec:
                log.debug(
                    f"  scanner {scan_code}@{location}: 0 rows after {empty_bail_sec:.0f}s "
                    f"— bailing (wrong session code?)"
                )
                break
            if now - last_log >= 3.0:
                from core.startup_log import sinfo
                sinfo(
                    None,
                    f"  scanner {scan_code}@{location}: {n} rows "
                    f"({'waiting on IB…' if n == 0 else 'receiving'})",
                )
                last_log = now
        else:
            log.warning(
                f"  scanner {scan_code}@{location}: timed out after {timeout_sec:.0f}s "
                f"({len(data_list)} rows)"
            )
    finally:
        try:
            ib.cancelScannerSubscription(data_list)
        except Exception:
            pass
    if data_list:
        from core.startup_log import sinfo
        sinfo(None, f"  scanner {scan_code}@{location}: {len(data_list)} rows")
    return list(data_list)


def _warm_scanner(ib, timeout_sec: float = 3.0) -> bool:
    """Optional scanner warm-up — skipped by default (paper gateways often hang)."""
    return False


def emergency_scan_universe(
    connector,
    cfg: BotConfig,
    *,
    reason: str = "empty",
) -> List[str]:
    """
    Last-resort universe when IB live scanner returns nothing — keeps bot trading.
    Curated US momentum list first; open USD positions with known US primary only.
    """
    from core.scanner import PENNY_STOCK_UNIVERSE
    from core.universe_filter import ALLOWED_PRIMARY_EXCHANGES, passes_profit_hunt_universe

    position_tickers: List[str] = []
    seen: set = set()
    try:
        ib = connector.ib
        ib.reqPositions()
        ib.sleep(0.25)
        for p in ib.positions():
            if abs(float(p.position)) < 0.5:
                continue
            c = p.contract
            sym = (getattr(c, "symbol", "") or "").upper().strip()
            if not sym or sym in seen:
                continue
            currency = (getattr(c, "currency", "") or "USD").upper()
            if currency != "USD":
                continue
            primary = str(
                getattr(c, "primaryExchange", "")
                or getattr(c, "primaryExch", "")
                or ""
            ).upper()
            if not primary or primary not in ALLOWED_PRIMARY_EXCHANGES:
                continue
            ok, _ = passes_profit_hunt_universe(cfg, sym, primary)
            if not ok:
                continue
            seen.add(sym)
            position_tickers.append(sym)
    except Exception:
        pass

    tickers: List[str] = []
    for sym in PENNY_STOCK_UNIVERSE:
        if sym not in seen:
            seen.add(sym)
            tickers.append(sym)

    combined = position_tickers + [t for t in tickers if t not in set(position_tickers)]

    out: List[str] = []
    for t in combined:
        if is_tradeable_ticker_local(t, cfg):
            out.append(t)
        if len(out) >= int(getattr(cfg, "SCAN_UNIVERSE_MAX", 30)):
            break

    if out:
        preview = f"{', '.join(out[:8])}{'…' if len(out) > 8 else ''}"
        if reason == "deferred":
            log.info(
                f"📋 Startup curated universe: {len(out)} tickers ({preview}) "
                "— IB live scanner deferred for instant lock"
            )
        elif reason == "outside_session":
            log.info(
                f"📋 Session curated universe: {len(out)} tickers ({preview}) "
                "— IB scanner off outside RTH/AH window"
            )
        else:
            log.warning(
                f"⚠️ IB scanner empty — emergency universe: {len(out)} tickers ({preview})"
            )
    return out


def is_tradeable_ticker_local(ticker: str, cfg: BotConfig) -> bool:
    try:
        from core.pilot_mode import is_tradeable_ticker
        return is_tradeable_ticker(ticker, cfg=cfg)
    except Exception:
        return bool(ticker and len(ticker) <= 5)


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
        self._scanner_warmed: bool = False

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
                from core.scanner_session import ib_scanner_profile, should_run_ib_scanner
                from core.universe_filter import passes_profit_hunt_universe
                ib = ib_connector.ib

                run_ok, run_reason = should_run_ib_scanner(self.cfg)
                if not run_ok:
                    log.info(f"🔍 IB scanner skipped — {run_reason}")
                    return []

                profile = ib_scanner_profile(self.cfg)
                scan_codes = list(profile.scan_codes)
                filter_options = list(profile.filter_options)
                if not scan_codes:
                    log.info(f"🔍 IB scanner skipped — no codes for session {profile.session}")
                    return []

                if not self._scanner_warmed:
                    _warm_scanner(ib, timeout_sec=float(
                        getattr(self.cfg, "IB_SCANNER_WARMUP_SEC", 3.0)
                    ))
                    self._scanner_warmed = True

                max_codes = int(getattr(self.cfg, "IB_SCANNER_MAX_CODES_PER_RUN", 2))
                disabled_codes: set = set()
                skipped_universe = 0
                location_codes = ["STK.US", "STK.US.MAJOR"]
                deadline = now + float(getattr(self.cfg, "IB_SCANNER_TIMEOUT_SEC", 25))
                per_code_cap = profile.per_code_sec or float(
                    getattr(self.cfg, "IB_SCANNER_PER_CODE_SEC", 18)
                )
                empty_bail = float(getattr(self.cfg, "IB_SCANNER_EMPTY_BAIL_SEC", 4))
                codes_tried = 0

                from core.startup_log import sinfo
                filt_note = f" | filters={len(filter_options)}" if filter_options else ""
                log.info(
                    f"🔍 IB scanner ({profile.label}) "
                    f"budget {getattr(self.cfg, 'IB_SCANNER_TIMEOUT_SEC', 25):.0f}s{filt_note}…"
                )

                for location_code in location_codes:
                    if tickers:
                        break
                    if time.time() > deadline or codes_tried >= max_codes:
                        break
                    for scan_code in scan_codes:
                        if scan_code in disabled_codes:
                            continue
                        if time.time() > deadline or codes_tried >= max_codes:
                            break
                        scan = ScannerSubscription(
                            instrument='STK',
                            locationCode=location_code,
                            scanCode=scan_code,
                        )
                        try:
                            remaining = max(6.0, deadline - time.time())
                            code_budget = min(per_code_cap, remaining)
                            sinfo(
                                self.cfg,
                                f"  scanner req {scan_code} @ {location_code} "
                                f"({profile.label}, budget {code_budget:.0f}s)…",
                            )
                            scan_results = _req_scanner_with_timeout(
                                ib,
                                scan,
                                code_budget,
                                filter_options=filter_options,
                                empty_bail_sec=empty_bail if profile.use_extended_filters else 0.0,
                            )
                            codes_tried += 1
                        except Exception as exc:
                            if '162' in str(exc) or 'disabled' in str(exc).lower():
                                disabled_codes.add(scan_code)
                                log.debug(f"Scanner {scan_code} unavailable — skipping")
                            continue
                        if not scan_results:
                            ib.sleep(0.35)
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
                    from core.startup_log import sinfo
                    sinfo(
                        self.cfg,
                        f"IB scanner: {len(tickers)} tickers ({profile.label}) "
                        f"(skipped {skipped_universe} OTC/distressed)",
                        force=True,
                    )
                else:
                    log.warning(
                        f"No tickers from IB scanner ({profile.label}) — "
                        "check session (RTH vs after-hours) / subscription"
                    )
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