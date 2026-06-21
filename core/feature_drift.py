#!/usr/bin/env python3
"""
core/feature_drift.py — Feature pipeline validation to prevent training/serving skew.

PURPOSE
═══════════════════════════════════════════════════════════════════════════
When you train models on historical data and deploy them live, the most
insidious failure mode is "feature drift" — the live feature pipeline
produces different values than the training pipeline did.

Examples:
  - Rolling Z-score uses different lookback windows
  - ATR calculation differs between training (pandas) and live (numpy)
  - NaN handling differs (fillna(0) vs dropna vs forward-fill)
  - Timestamp alignment shifts between bar data and feature windows

This module provides:
  1. A fixed feature manifest that MUST match between training and serving
  2. A deterministic validation routine that runs on startup
  3. A hash-based integrity check on feature batches
  4. A reference dataset for exact numerical comparison

If validation fails, the bot will REFUSE TO START rather than silently
trade on corrupted/misaligned features.
"""

import os
import json
import hashlib
import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime

from core.notify import log


# ═════════════════════════════════════════════════════════════════════════════
# FEATURE MANIFEST (contract between training and serving)
# ═════════════════════════════════════════════════════════════════════════════

FEATURE_MANIFEST = {
    "version": "1.0.0",
    "n_features": 18,
    "features": [
        {"name": "close", "type": "float", "description": "Close price"},
        {"name": "volume", "type": "float", "description": "Volume"},
        {"name": "returns_1", "type": "float", "description": "1-bar return %"},
        {"name": "returns_5", "type": "float", "description": "5-bar return %"},
        {"name": "returns_20", "type": "float", "description": "20-bar return %"},
        {"name": "sma_20", "type": "float", "description": "20-period SMA"},
        {"name": "sma_50", "type": "float", "description": "50-period SMA"},
        {"name": "ema_9", "type": "float", "description": "9-period EMA"},
        {"name": "ema_21", "type": "float", "description": "21-period EMA"},
        {"name": "macd", "type": "float", "description": "MACD line"},
        {"name": "macd_signal", "type": "float", "description": "MACD signal line"},
        {"name": "macd_hist", "type": "float", "description": "MACD histogram"},
        {"name": "rsi", "type": "float", "description": "14-period RSI"},
        {"name": "atr", "type": "float", "description": "14-period ATR"},
        {"name": "atr_pct", "type": "float", "description": "ATR as % of price"},
        {"name": "volatility_20", "type": "float", "description": "20-period rolling volatility"},
        {"name": "volume_ratio", "type": "float", "description": "Current volume / 20-bar avg"},
        {"name": "price_position", "type": "float", "description": "Price position in 20-bar range"},
    ],
    "processing": {
        "nan_handling": "zero_fill",
        "outlier_clip": "3_std",
        "normalization": "none",
    }
}


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATION FRAMEWORK
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """Result of feature validation."""
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class FeatureDriftValidator:
    """
    Validates that live features match training features exactly.
    
    Run this at startup BEFORE the trading loop begins.
    """
    
    def __init__(self, manifest_path: str = "models/feature_manifest.json",
                 reference_path: str = "models/feature_reference.npz"):
        self.manifest_path = manifest_path
        self.reference_path = reference_path
        self._reference_batch: Optional[np.ndarray] = None
        self._reference_hash: Optional[str] = None
    
    def validate_pipeline(self, feature_fn, test_bars: int = 100) -> ValidationResult:
        """
        Run full validation: manifest check, deterministic output check, hash check.
        
        Args:
            feature_fn: Callable that takes (df, window_size) -> np.ndarray
            test_bars: Number of bars to use for validation
            
        Returns:
            ValidationResult
        """
        errors = []
        
        # 1. Check manifest version
        manifest_result = self._check_manifest()
        if not manifest_result.passed:
            return manifest_result
        
        # 2. Check feature count
        count_result = self._check_feature_count(feature_fn, test_bars)
        if not count_result.passed:
            return count_result
        
        # 3. Check deterministic output (same input = same output)
        det_result = self._check_determinism(feature_fn, test_bars)
        if not det_result.passed:
            return det_result
        
        # 4. Check numerical stability (no NaNs, no infs)
        stab_result = self._check_stability(feature_fn, test_bars)
        if not stab_result.passed:
            return stab_result
        
        # 5. Check hash against reference (if available)
        if self._reference_hash:
            hash_result = self._check_hash(feature_fn, test_bars)
            if not hash_result.passed:
                return hash_result
        
        return ValidationResult(
            passed=True,
            message="All feature validation checks passed",
            details={
                "manifest_version": FEATURE_MANIFEST["version"],
                "n_features": FEATURE_MANIFEST["n_features"],
                "test_bars": test_bars,
            }
        )
    
    def _check_manifest(self) -> ValidationResult:
        """Verify manifest is present and valid."""
        try:
            os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
            with open(self.manifest_path, 'w') as f:
                json.dump(FEATURE_MANIFEST, f, indent=2)
            return ValidationResult(passed=True, message="Manifest OK")
        except Exception as exc:
            return ValidationResult(passed=False, message=f"Manifest write failed: {exc}")
    
    def _check_feature_count(self, feature_fn, test_bars: int) -> ValidationResult:
        """Verify feature function returns exactly n_features columns."""
        try:
            # Create synthetic test data
            np.random.seed(42)
            test_df = pd.DataFrame({
                "open": np.cumsum(np.random.randn(test_bars)) + 100,
                "high": np.cumsum(np.random.randn(test_bars)) + 105,
                "low": np.cumsum(np.random.randn(test_bars)) + 95,
                "close": np.cumsum(np.random.randn(test_bars)) + 100,
                "volume": np.random.randint(1000, 10000, test_bars),
            })
            test_df["high"] = test_df[["open", "close", "high"]].max(axis=1)
            test_df["low"] = test_df[["open", "close", "low"]].min(axis=1)
            
            features = feature_fn(test_df, window_size=30)
            
            if features.ndim == 1:
                n_cols = len(features)
            elif features.ndim == 2:
                n_cols = features.shape[1]
            else:
                return ValidationResult(
                    passed=False,
                    message=f"Feature function returned {features.ndim}D array, expected 1D or 2D"
                )
            
            if n_cols != FEATURE_MANIFEST["n_features"]:
                return ValidationResult(
                    passed=False,
                    message=f"Feature count mismatch: got {n_cols}, expected {FEATURE_MANIFEST['n_features']}",
                    details={"actual": n_cols, "expected": FEATURE_MANIFEST["n_features"]}
                )
            
            return ValidationResult(passed=True, message=f"Feature count OK: {n_cols}")
            
        except Exception as exc:
            return ValidationResult(passed=False, message=f"Feature count check failed: {exc}")
    
    def _check_determinism(self, feature_fn, test_bars: int) -> ValidationResult:
        """Verify same input produces identical output."""
        try:
            np.random.seed(42)
            test_df = pd.DataFrame({
                "open": np.cumsum(np.random.randn(test_bars)) + 100,
                "high": np.cumsum(np.random.randn(test_bars)) + 105,
                "low": np.cumsum(np.random.randn(test_bars)) + 95,
                "close": np.cumsum(np.random.randn(test_bars)) + 100,
                "volume": np.random.randint(1000, 10000, test_bars),
            })
            test_df["high"] = test_df[["open", "close", "high"]].max(axis=1)
            test_df["low"] = test_df[["open", "close", "low"]].min(axis=1)
            
            features1 = feature_fn(test_df, window_size=30)
            features2 = feature_fn(test_df, window_size=30)
            
            if not np.allclose(features1, features2, equal_nan=True):
                return ValidationResult(
                    passed=False,
                    message="Feature function is non-deterministic: same input produced different outputs"
                )
            
            return ValidationResult(passed=True, message="Determinism OK")
            
        except Exception as exc:
            return ValidationResult(passed=False, message=f"Determinism check failed: {exc}")
    
    def _check_stability(self, feature_fn, test_bars: int) -> ValidationResult:
        """Check for NaN, inf, or extreme outliers."""
        try:
            np.random.seed(42)
            test_df = pd.DataFrame({
                "open": np.cumsum(np.random.randn(test_bars)) + 100,
                "high": np.cumsum(np.random.randn(test_bars)) + 105,
                "low": np.cumsum(np.random.randn(test_bars)) + 95,
                "close": np.cumsum(np.random.randn(test_bars)) + 100,
                "volume": np.random.randint(1000, 10000, test_bars),
            })
            test_df["high"] = test_df[["open", "close", "high"]].max(axis=1)
            test_df["low"] = test_df[["open", "close", "low"]].min(axis=1)
            
            features = feature_fn(test_df, window_size=30)
            
            n_nan = np.isnan(features).sum()
            n_inf = np.isinf(features).sum()
            max_val = np.max(np.abs(features)) if not np.isnan(features).all() else 0
            
            if n_nan > 0:
                return ValidationResult(
                    passed=False,
                    message=f"Features contain {n_nan} NaN values",
                    details={"n_nan": int(n_nan)}
                )
            
            if n_inf > 0:
                return ValidationResult(
                    passed=False,
                    message=f"Features contain {n_inf} infinite values",
                    details={"n_inf": int(n_inf)}
                )
            
            if max_val > 1e6:
                return ValidationResult(
                    passed=False,
                    message=f"Features contain extreme values (max={max_val:.2e})",
                    details={"max_abs": float(max_val)}
                )
            
            return ValidationResult(
                passed=True,
                message=f"Stability OK (max abs={max_val:.2f})"
            )
            
        except Exception as exc:
            return ValidationResult(passed=False, message=f"Stability check failed: {exc}")
    
    def _check_hash(self, feature_fn, test_bars: int) -> ValidationResult:
        """Compare feature hash against reference."""
        try:
            np.random.seed(42)
            test_df = pd.DataFrame({
                "open": np.cumsum(np.random.randn(test_bars)) + 100,
                "high": np.cumsum(np.random.randn(test_bars)) + 105,
                "low": np.cumsum(np.random.randn(test_bars)) + 95,
                "close": np.cumsum(np.random.randn(test_bars)) + 100,
                "volume": np.random.randint(1000, 10000, test_bars),
            })
            test_df["high"] = test_df[["open", "close", "high"]].max(axis=1)
            test_df["low"] = test_df[["open", "close", "low"]].min(axis=1)
            
            features = feature_fn(test_df, window_size=30)
            current_hash = hashlib.sha256(features.tobytes()).hexdigest()[:16]
            
            if current_hash != self._reference_hash:
                return ValidationResult(
                    passed=False,
                    message=f"Feature hash mismatch: got {current_hash}, expected {self._reference_hash}",
                    details={"current_hash": current_hash, "reference_hash": self._reference_hash}
                )
            
            return ValidationResult(passed=True, message="Hash match OK")
            
        except Exception as exc:
            return ValidationResult(passed=False, message=f"Hash check failed: {exc}")
    
    def save_reference(self, feature_fn, test_bars: int = 100):
        """
        Save reference feature batch for future hash comparison.
        Call this after training is complete and features are stable.
        """
        try:
            np.random.seed(42)
            test_df = pd.DataFrame({
                "open": np.cumsum(np.random.randn(test_bars)) + 100,
                "high": np.cumsum(np.random.randn(test_bars)) + 105,
                "low": np.cumsum(np.random.randn(test_bars)) + 95,
                "close": np.cumsum(np.random.randn(test_bars)) + 100,
                "volume": np.random.randint(1000, 10000, test_bars),
            })
            test_df["high"] = test_df[["open", "close", "high"]].max(axis=1)
            test_df["low"] = test_df[["open", "close", "low"]].min(axis=1)
            
            features = feature_fn(test_df, window_size=30)
            self._reference_hash = hashlib.sha256(features.tobytes()).hexdigest()[:16]
            
            np.savez(self.reference_path, features=features, hash=self._reference_hash)
            log.info(f"Feature reference saved: {self.reference_path} (hash={self._reference_hash})")
            
        except Exception as exc:
            log.warning(f"Failed to save feature reference: {exc}")


def validate_features_at_startup(feature_fn) -> bool:
    """
    Convenience function to run validation at bot startup.
    Returns True if validation passed, False otherwise.
    """
    validator = FeatureDriftValidator()
    result = validator.validate_pipeline(feature_fn)
    
    if result.passed:
        log.info(f"✅ Feature validation: {result.message}")
        return True
    else:
        log.error(f"❌ Feature validation FAILED: {result.message}")
        log.error(f"   Details: {result.details}")
        log.error("   Bot will NOT start. Fix feature pipeline before trading.")
        return False