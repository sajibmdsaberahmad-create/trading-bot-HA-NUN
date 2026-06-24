# Scanner Real Market Data Analysis - COMPLETE FIX

## Problem Summary

The scanner was showing static/fallback tickers instead of real market data because:

### 1. Missing `scan_pennies` Method (scanner.py) ✅ FIXED
- `WidenetScout._get_candidate_universe()` called `self.scanner.scan_pennies()` which didn't exist
- This caused the code to fall back to static `['SPY', 'QQQ', 'IWM']` tickers
- **Fix**: Added the `scan_pennies` method to `StockScanner` class

### 2. Placeholder Implementations (sniper_screener.py) ✅ FIXED
All `_compute_*` methods returned hardcoded `0.5` values instead of real data:
- `_compute_volume_spike` - Now fetches real volume data from IB
- `_compute_volatility_opportunity` - Now computes real ATR from IB data
- `_compute_orderbook_signal` - Now uses price momentum as proxy
- `_compute_ai_confidence` - Now computes real momentum-based confidence

### 3. Broken Import (sniper_screener.py) ✅ FIXED
- Lines 21-27 had malformed nested try/except structure
- **Fix**: Cleaned up to use direct import

### 4. Inefficient Connection Pattern ✅ FIXED
- `sniper_screener.py` was creating new IB connections for each ticker
- **Fix**: Added `ib_connector` parameter to `WidenetScout` and `run_screener`
- **Fix**: Added `_fetch_ticker_data` method that uses shared connection
- **Fix**: Updated `SniperOrchestrator.start_screener` to pass connector

### 5. Placeholder Market Snapshot (sniper_heartbeat.py) ✅ FIXED
- `_get_market_snapshot` returned zeros for all values
- **Fix**: Now fetches real historical data and computes ATR, prices, volumes

## Files Modified

| File | Changes |
|------|---------|
| `core/scanner.py` | Added `scan_pennies` method |
| `core/sniper_screener.py` | Fixed imports, added real data fetching, added shared connection support |
| `core/sniper_orchestrator.py` | Updated to pass IB connector to screener |
| `core/sniper_heartbeat.py` | Fixed `_get_market_snapshot` to fetch real data |

## Architecture

The `ScalperRunner._scan_and_rank()` method (used in `--mode scalper`) already has **working real market data scanning**:
- Uses existing IB connection
- Fetches 1-day of 1-minute bars for each ticker
- Computes real scores based on momentum, volume, institutional signals
- Returns actual `ScanResult` objects with real prices

The `sniper_screener.py` and `sniper_heartbeat.py` are **separate components** for the Sniper-Lock architecture that were never properly integrated with the real data pipeline.

## Testing

To verify real market data scanning works:
```bash
# Run the scalper mode (uses ScalperRunner._scan_and_rank)
python main.py --mode scalper

# Check live_metrics.json for real scan results with actual prices
cat live_metrics.json
```

## Notes

- The `sniper_screener.py` now uses a shared IB connection when provided
- If no connection is provided, it falls back to default values (0.5)
- The `_fetch_ticker_data` method handles connection errors gracefully
- All methods now have proper error logging