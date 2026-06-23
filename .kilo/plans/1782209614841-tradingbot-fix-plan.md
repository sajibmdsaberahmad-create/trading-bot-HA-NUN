# TradingBot Fix & Upgrade Plan

## Executive Summary
Critical issues identified: feature count mismatch causing model inference errors, missing method causing runtime failures, and date range issues in training. These must be fixed before system can operate reliably.

## Critical Fixes Required (Before Trading)

### 1. Feature Count Mismatch - CRITICAL
**Problem:** Inconsistent feature dimensions across codebase:
- `core/features.py`: 14 features
- `core/features_enhanced.py`: 18 features  
- `core/config.py`: N_FEATURES = 18
- `core/scanner.py`: Uses `FeatureEngineer.compute()` (14 features)

**Fix:** Standardize to 18 features. Update `core/scanner.py` to use `FeatureEngineerEnhanced.compute()`.

### 2. Missing Method `compute_features` - CRITICAL
**Problem:** `core/scalper_runner.py` line ~170 calls `fe.compute_features(df, window_size=window_size)` but `FeatureEngineerEnhanced` only has static `compute()` method.

**Fix:** Either rename the method call to `compute()` or add `compute_features()` wrapper.

### 3. Synthetic Data Date Range - HIGH
**Problem:** Training date splits use 2025 dates which are in the future.
- Train: 2020-01-01 to 2024-06-01
- Test: 2025-01-01 to 2025-06-01 (FUTURE DATES)

**Fix:** Adjust test/backtest dates to use historical data (e.g., 2024-06-01 to 2024-12-31).

### 4. Scanner Feature Mismatch - HIGH
**Problem:** `core/scanner.py` calls `FeatureEngineer.compute()` returning 14 features, but model expects 18.

**Fix:** Import and use `FeatureEngineerEnhanced.compute()` instead.

## Medium Priority Improvements

### 5. Market Regime Confidence Integration
**Problem:** `ConfidenceScorer.score()` expects `regime_result.stability` attribute that doesn't exist in `RegimeResult` dataclass.

**Fix:** Add `_stability` field to `RegimeResult` or calculate stability inline.

### 6. Experience Buffer Type Hint
**Problem:** `core/scalper_runner.py` references `Dict` type without importing `from typing import Dict`.

**Fix:** Add proper type imports.

### 7. Fusion Engine Validation Edge Cases
**Problem:** `run_fusion_backtest()` in main.py doesn't handle missing `recent_rewards` attribute in PerformanceTracker.

**Fix:** Add null-safety checks for optional attributes.

## Risk Management Optimizations for Profitability

### 8. Confidence Threshold Tuning
Current: `CONFIDENCE_THRESHOLD = 0.55`
Recommended: Increase to `0.65` to reduce false positives.

### 9. Scalper Stop/Take Profit Parameters
Current SCALP parameters are tight for volatile penny stocks:
- `SCALP_STOP_ATR_MULTIPLIER = 0.7` (too tight, gets stopped by noise)
- `SCALP_TP_ATR_MULTIPLIER = 1.5` (good)

Recommended: 
- `SCALP_STOP_ATR_MULTIPLIER = 0.9` (wider stop)
- `SCALP_MIN_STOP_PCT = 0.004` (4% minimum)
- `SCALP_MAX_STOP_PCT = 0.015` (1.5% maximum)

### 10. Early Loss Exit Enhancement
Current threshold is too aggressive. Recommended: Reduce `EARLY_LOSS_RISK_PCT_THRESHOLD` from 0.30 to 0.50 to avoid premature exits.

## Implementation Order

1. **Phase 1 - Critical Fixes (Day 1)**
   - Fix feature dimension mismatch
   - Fix missing `compute_features` method
   - Fix date ranges in training splits

2. **Phase 2 - Stability (Day 2)**
   - Add missing type hints
   - Fix regime confidence integration
   - Add null-safety checks

3. **Phase 3 - Optimization (Day 3)**
   - Adjust risk parameters based on backtest results
   - Tune confidence thresholds

## Files to Modify

| File | Changes |
|------|---------|
| `core/scanner.py` | Use FeatureEngineerEnhanced |
| `core/features_enhanced.py` | Add `compute_features()` wrapper method |
| `core/advanced_training.py` | Update date splits to historical |
| `core/agent_enhanced.py` | Add `_stability` to RegimeResult or fix accessor |
| `core/config.py` | Tune SCALP parameters and thresholds |

## Validation Steps

After fixes:
1. Run `python main.py --mode fusion-backtest --bt-bars 1000` to verify no errors
2. Run synthetic training to validate model pipelines work
3. Check feature shape consistency: `features.shape[1] == 18`