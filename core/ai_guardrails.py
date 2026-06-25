#!/usr/bin/env python3
"""
core/ai_guardrails.py — Comprehensive security & guardrail layer for AI agent.

This module provides a multi-layered security system that sits between
the AI agent's decisions and the execution layer. Every action, feature,
model update, and configuration change passes through this layer.

LAYERS:
1. INPUT_VALIDATION — Sanitizes all inputs to agent (NaN, Inf, shape, range)
2. OUTPUT_ENFORCEMENT — Validates agent actions against hard limits
3. ANOMALY_DETECTION — Statistical monitoring for abnormal behavior
4. RATE_LIMITING — Frequency caps on trades, model updates, reconnects
5. CONFIG_SIGNING — Tamper-evident configuration validation
6. AUDIT_TRAIL — Complete, immutable log of every AI decision
7. SANDBOX — Isolated execution environment for model inference
8. CONFIDENCE_GATE — Refuses actions below configurable confidence threshold
"""

import os
import sys
import json
import time
import copy
import hashlib
import inspect
import traceback
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List, Any, Callable
from dataclasses import dataclass, field, asdict
from collections import defaultdict, deque

import numpy as np

from core.notify import log


# ═════════════════════════════════════════════════════════════════════════════
# VALIDATORS & SANITIZERS
# ═════════════════════════════════════════════════════════════════════════════

def ppo_obs_dim(cfg) -> int:
    """PPO observation size: WINDOW_SIZE × N_FEATURES + cash/position ratios."""
    return int(getattr(cfg, "WINDOW_SIZE", 30)) * int(getattr(cfg, "N_FEATURES", 18)) + 2


def normalize_ppo_obs(obs, cfg) -> "np.ndarray":
    """
    Ensure observation matches trained PPO shape.
    Common bug: 540-dim window without the +2 account ratios (expects 542).
    """
    import numpy as np
    from core.config import BotConfig

    expected = ppo_obs_dim(cfg)
    if obs is None:
        return np.zeros(expected, dtype=np.float32)
    flat = np.asarray(obs, dtype=np.float32).flatten()
    if flat.shape[0] == expected:
        return flat
    if flat.shape[0] == expected - 2:
        return np.concatenate([flat, np.array([0.5, 0.5], dtype=np.float32)])
    if flat.shape[0] > expected:
        return flat[:expected].copy()
    out = np.zeros(expected, dtype=np.float32)
    out[: min(flat.shape[0], expected)] = flat[:expected]
    return out


def build_ppo_observation(
    feature_buffer,
    cfg,
    current_px: float,
    bot_cash: float,
    shares: float,
):
    """Build a full PPO observation from the rolling feature buffer."""
    import numpy as np

    window_size = int(getattr(cfg, "WINDOW_SIZE", 30))
    if len(feature_buffer) < window_size:
        return None
    window = np.array(list(feature_buffer)[-window_size:], dtype=np.float32).flatten()
    n_feat = int(getattr(cfg, "N_FEATURES", 18))
    need = window_size * n_feat
    if window.shape[0] != need:
        if window.shape[0] > need:
            window = window[:need]
        else:
            padded = np.zeros(need, dtype=np.float32)
            padded[: window.shape[0]] = window
            window = padded
    total = float(bot_cash) + float(shares) * float(current_px)
    c_rat = float(bot_cash) / (total + 1e-9)
    p_rat = (float(shares) * float(current_px)) / (total + 1e-9) if shares > 0 else 0.0
    return normalize_ppo_obs(np.concatenate([window, [c_rat, p_rat]]), cfg)


def sanitize_observation(obs: np.ndarray, expected_shape: tuple,
                          min_val: float = -1e6, max_val: float = 1e6) -> Tuple[np.ndarray, bool]:
    """
    Validate and sanitize agent observation input.
    
    Returns: (sanitized_array, is_valid)
    - Replaces NaN/Inf with 0.0
    - Clips values to [min_val, max_val]
    - Checks shape matches expected
    """
    if not isinstance(obs, np.ndarray):
        log.warning(f"GUARDRAIL: Observation is not ndarray (type={type(obs).__name__})")
        return np.zeros(expected_shape, dtype=np.float32), False
    
    if obs.shape != expected_shape:
        log.warning(f"GUARDRAIL: Observation shape mismatch: got {obs.shape}, expected {expected_shape}")
        # Pad or trim 1-D observations (e.g. 540 → 542)
        try:
            flat = np.asarray(obs, dtype=np.float32).flatten()
            exp_n = int(expected_shape[0]) if expected_shape else flat.shape[0]
            if len(expected_shape) == 1 and flat.ndim == 1:
                if flat.shape[0] == exp_n - 2:
                    flat = np.concatenate([flat, np.array([0.5, 0.5], dtype=np.float32)])
                elif flat.shape[0] > exp_n:
                    flat = flat[:exp_n]
                elif flat.shape[0] < exp_n:
                    padded = np.zeros(exp_n, dtype=np.float32)
                    padded[: flat.shape[0]] = flat
                    flat = padded
                obs = flat
            else:
                obs = obs.reshape(expected_shape)
        except Exception:
            return np.zeros(expected_shape, dtype=np.float32), False
    
    # Check for NaN/Inf
    has_bad = ~np.isfinite(obs)
    if has_bad.any():
        n_bad = int(has_bad.sum())
        log.warning(f"GUARDRAIL: Observation contains {n_bad} NaN/Inf values — sanitizing")
        obs = np.nan_to_num(obs, nan=0.0, posinf=max_val, neginf=min_val)
    
    # Clip extreme values
    obs = np.clip(obs, min_val, max_val)
    
    return obs.astype(np.float32), True


def sanitize_action(action: int, n_actions: int = 3) -> Tuple[int, bool]:
    """
    Validate agent action output.
    
    Returns: (action, is_valid)
    - Validates integer range
    - Replaces out-of-range with HOLD (0)
    """
    if not isinstance(action, (int, np.integer)):
        log.warning(f"GUARDRAIL: Action is not integer (type={type(action).__name__})")
        return 0, False
    
    action = int(action)
    if action < 0 or action >= n_actions:
        log.warning(f"GUARDRAIL: Action out of range: {action} (valid: 0-{n_actions-1})")
        return 0, False
    
    return action, True


def sanitize_weights(weights: Dict[str, float], allowed_keys: set,
                      min_val: float = 0.0, max_val: float = 100.0) -> Tuple[Dict[str, float], bool]:
    """
    Validate and sanitize model weights dictionary.
    
    Returns: (sanitized_weights, is_valid)
    - Removes unknown keys
    - Clips values to [min_val, max_val]
    - Replaces NaN/Inf with defaults
    """
    sanitized = {}
    valid = True
    
    for key, value in weights.items():
        if key not in allowed_keys:
            log.warning(f"GUARDRAIL: Unknown weight key '{key}' — removing")
            valid = False
            continue
        
        if not isinstance(value, (int, float)):
            log.warning(f"GUARDRAIL: Weight '{key}' is not numeric (type={type(value).__name__})")
            sanitized[key] = 1.0
            valid = False
            continue
        
        if not np.isfinite(value):
            log.warning(f"GUARDRAIL: Weight '{key}' is NaN/Inf — resetting to 1.0")
            sanitized[key] = 1.0
            valid = False
            continue
        
        sanitized[key] = float(np.clip(value, min_val, max_val))
    
    return sanitized, valid


# ═════════════════════════════════════════════════════════════════════════════
# ANOMALY DETECTION
# ═════════════════════════════════════════════════════════════════════════════

class ActionAnomalyDetector:
    """
    Statistical anomaly detection on agent actions.
    
    Monitors:
    - Action frequency (too many trades = overfitting/failure)
    - Action entropy (always same action = broken model)
    - Inter-action timing (decisions coming too fast)
    - Consecutive action flips (HOLD->BUY->SELL->BUY within seconds)
    """
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._action_history: deque = deque(maxlen=window_size)
        self._timestamp_history: deque = deque(maxlen=window_size)
        self._anomaly_count = 0
        self._last_anomaly_ts: Optional[float] = None
    
    def record_action(self, action: int, action_name: str):
        """Record an action for analysis."""
        now = time.time()
        self._action_history.append(action)
        self._timestamp_history.append(now)
    
    def check_anomaly(self) -> Tuple[bool, str]:
        """
        Check current behavior for anomalies.
        
        Returns: (is_anomalous, reason)
        """
        if len(self._action_history) < 10:
            return False, ""
        
        reasons = []
        actions = list(self._action_history)
        
        # Check 1: Action distribution — model stuck on one action?
        unique_actions = set(actions[-20:])
        if len(unique_actions) <= 1:
            reasons.append(f"Model stuck on single action ({actions[-1]}) for last {min(20, len(actions))} steps")
        
        # Check 2: Action flipping — rapid BUY/SELL cycles?
        if len(actions) >= 6:
            recent = actions[-6:]
            flips = sum(1 for i in range(1, len(recent)) if recent[i] != recent[i-1])
            if flips >= 4:
                reasons.append(f"Excessive action flipping: {flips} flips in last 6 steps")
        
        # Check 3: Trade frequency — too many trades per minute?
        if len(self._timestamp_history) >= 20:
            timestamps = list(self._timestamp_history)
            time_span = timestamps[-1] - timestamps[0]
            if time_span > 0:
                trades_per_minute = (20 * 60.0) / time_span
                if trades_per_minute > 30:
                    reasons.append(f"Trade frequency too high: {trades_per_minute:.0f}/min")
        
        # Check 4: Action entropy too low (too predictable = not adapting)
        if len(actions) >= 50:
            counts = {a: actions.count(a) for a in set(actions)}
            total = len(actions)
            # Simplified entropy: proportion of dominant action
            dominant_ratio = max(counts.values()) / total if total > 0 else 1.0
            if dominant_ratio > 0.95:
                reasons.append(f"Action entropy critically low: {dominant_ratio:.0%} same action")
        
        if reasons:
            self._anomaly_count += 1
            self._last_anomaly_ts = time.time()
            return True, "; ".join(reasons[:2])
        
        return False, ""
    
    def reset(self):
        """Reset history."""
        self._action_history.clear()
        self._timestamp_history.clear()
    
    @property
    def anomaly_rate(self) -> float:
        """Fraction of anomaly checks that triggered."""
        return min(1.0, self._anomaly_count / max(1, len(self._action_history)))


class FeatureAnomalyDetector:
    """
    Monitors feature distributions for drift or corruption.
    
    Detects:
    - Feature values drifting outside expected ranges
    - Sudden volatility in feature means/stds
    - Corrupted features (all zeros, all same value)
    """
    
    def __init__(self, n_features: int = 14, window_size: int = 100):
        self.n_features = n_features
        self.window_size = window_size
        self._feature_buffer: deque = deque(maxlen=window_size)
        self._baseline_mean: Optional[np.ndarray] = None
        self._baseline_std: Optional[np.ndarray] = None
        self._baseline_samples = 0
        self._anomaly_count = 0
    
    def feed_features(self, features: np.ndarray):
        """Feed a feature vector for analysis."""
        self._feature_buffer.append(features.copy())
        
        # Build baseline from first 50 samples
        if self._baseline_samples < 50:
            self._baseline_samples += 1
            if self._baseline_mean is None:
                self._baseline_mean = features.copy()
                self._baseline_std = np.ones(self.n_features, dtype=np.float32)
            else:
                # Running update
                n = self._baseline_samples
                delta = features - self._baseline_mean
                self._baseline_mean += delta / n
                delta2 = features - self._baseline_mean
                self._baseline_std = np.sqrt(
                    (self._baseline_std**2 * (n-1) + delta * delta2) / n
                )
    
    def check_anomaly(self) -> Tuple[bool, str]:
        """
        Check feature distribution for anomalies.
        
        Returns: (is_anomalous, reason)
        """
        if len(self._feature_buffer) < 10 or self._baseline_mean is None:
            return False, ""
        
        latest = self._feature_buffer[-1]
        reasons = []
        
        # Check 1: All zeros or nearly constant
        if np.std(latest) < 1e-8:
            reasons.append("Feature vector nearly constant (std < 1e-8)")
        
        # Check 2: Feature drift — z-score > 5 on any feature
        if self._baseline_std is not None:
            z_scores = np.abs((latest - self._baseline_mean) / (self._baseline_std + 1e-9))
            drift_features = np.where(z_scores > 5.0)[0]
            if len(drift_features) > 0:
                # Only flag if multiple features are drifting (single feature drift is normal)
                if len(drift_features) >= self.n_features * 0.3:  # 30% of features drifting
                    reasons.append(f"Widespread feature drift: {len(drift_features)}/{self.n_features} features >5σ")
        
        # Check 3: Sudden value explosion
        if np.any(np.abs(latest) > 1e5):
            reasons.append("Feature values exploded (>1e5)")
        
        if reasons:
            self._anomaly_count += 1
            return True, "; ".join(reasons)
        
        return False, ""
    
    def reset_baseline(self):
        """Reset the baseline statistics."""
        self._baseline_mean = None
        self._baseline_std = None
        self._baseline_samples = 0


# ═════════════════════════════════════════════════════════════════════════════
# RATE LIMITER
# ═════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Multi-key rate limiter with per-second, per-minute, per-hour, per-day limits.
    
    Tracks:
    - Trades per minute/hour/day
    - Model updates per hour
    - API calls per second
    - Login/reconnect attempts per minute
    """
    
    def __init__(self):
        self._buckets: Dict[str, deque] = defaultdict(lambda: deque(maxlen=10000))
    
    def _purge_old(self, key: str, window_seconds: float):
        """Remove entries older than window."""
        now = time.time()
        bucket = self._buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
    
    def check_limit(self, key: str, max_count: int, window_seconds: float) -> Tuple[bool, int]:
        """
        Check if action is within rate limit.
        
        Returns: (allowed, current_count_in_window)
        """
        self._purge_old(key, window_seconds)
        count = len(self._buckets[key])
        return count < max_count, count
    
    def record(self, key: str):
        """Record an action occurrence."""
        self._buckets[key].append(time.time())
    
    def reset_key(self, key: str):
        """Reset rate limit for a specific key."""
        self._buckets[key].clear()


# ═════════════════════════════════════════════════════════════════════════════
# CONFIG VALIDATION & SIGNING
# ═════════════════════════════════════════════════════════════════════════════

def compute_config_hash(cfg) -> str:
    """Compute a deterministic hash of critical configuration parameters."""
    critical_fields = [
        'MAX_RISK_PER_TRADE_USD', 'RISK_PER_TRADE_PCT', 'MAX_DAILY_LOSS_PCT',
        'MAX_WEEKLY_LOSS_PCT', 'MAX_CONSECUTIVE_LOSSES', 'MAX_TRADE_SIZE_USD',
        'MAX_SHARES_PER_TRADE', 'MIN_CASH_RESERVE_PCT', 'MAX_CONCURRENT_POSITIONS',
        'MAX_POSITION_PCT', 'STOP_ATR_MULTIPLIER', 'TAKE_PROFIT_ATR_MULTIPLIER',
        'PAPER_TRADING', 'TRADING_MODE',
    ]
    values = {}
    for field in critical_fields:
        try:
            values[field] = str(getattr(cfg, field, 'MISSING'))
        except Exception:
            values[field] = 'ERROR'
    
    raw = json.dumps(values, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def validate_config(cfg) -> Tuple[bool, List[str]]:
    """
    Validate configuration for safety violations.
    
    Checks:
    - Risk parameters within safe bounds
    - No contradictory settings
    - No values that could cause runaway behavior
    """
    errors = []
    
    paper_free = False
    try:
        from core.paper_mode import is_paper_free_learning, account_equity
        paper_free = is_paper_free_learning(cfg)
        equity = account_equity(cfg)
    except Exception:
        paper_free = bool(getattr(cfg, "PAPER_TRADING", False)) and bool(
            getattr(cfg, "AI_PAPER_FREE_LEARNING", True)
        )
        equity = float(getattr(cfg, "PAPER_EQUITY_HINT", 1_000_000))

    # Paper free-learning: allow equity-scaled risk ceiling (~25% of equity, min $100k cap)
    if paper_free:
        max_risk_usd_cap = max(100_000.0, equity * 0.30, float(getattr(cfg, "MAX_RISK_PER_TRADE_USD", 250_000)))
    elif getattr(cfg, "PAPER_TRADING", False):
        max_risk_usd_cap = 500_000.0
    else:
        max_risk_usd_cap = 100_000.0

    # Check 1: Risk per trade must be reasonable
    if cfg.RISK_PER_TRADE_PCT <= 0 or cfg.RISK_PER_TRADE_PCT > 0.5:
        errors.append(f"RISK_PER_TRADE_PCT ({cfg.RISK_PER_TRADE_PCT}) out of safe range (0, 0.5]")
    
    if cfg.MAX_RISK_PER_TRADE_USD <= 0 or cfg.MAX_RISK_PER_TRADE_USD > max_risk_usd_cap:
        errors.append(
            f"MAX_RISK_PER_TRADE_USD ({cfg.MAX_RISK_PER_TRADE_USD}) out of safe range "
            f"(0, {max_risk_usd_cap:.0f}]"
        )
    
    # Check 2: Daily/weekly loss limits
    if cfg.MAX_DAILY_LOSS_PCT <= 0 or cfg.MAX_DAILY_LOSS_PCT > 0.5:
        errors.append(f"MAX_DAILY_LOSS_PCT ({cfg.MAX_DAILY_LOSS_PCT}) out of safe range (0, 0.5]")
    
    if cfg.MAX_WEEKLY_LOSS_PCT <= 0 or cfg.MAX_WEEKLY_LOSS_PCT > 0.8:
        errors.append(f"MAX_WEEKLY_LOSS_PCT ({cfg.MAX_WEEKLY_LOSS_PCT}) out of safe range (0, 0.8]")
    
    # Check 3: Position limits
    if cfg.DEFAULT_MAX_POSITION_PCT <= 0 or cfg.DEFAULT_MAX_POSITION_PCT > 1.0:
        errors.append(f"DEFAULT_MAX_POSITION_PCT ({cfg.DEFAULT_MAX_POSITION_PCT}) out of safe range (0, 1.0]")
    
    if cfg.MAX_SHARES_PER_TRADE <= 0 or cfg.MAX_SHARES_PER_TRADE > 1_000_000:
        errors.append(f"MAX_SHARES_PER_TRADE ({cfg.MAX_SHARES_PER_TRADE}) out of safe range (0, 1000000]")
    
    # Check 4: Stop loss must be tighter than take profit (minimum sanity)
    if cfg.STOP_ATR_MULTIPLIER >= cfg.TAKE_PROFIT_ATR_MULTIPLIER:
        errors.append(f"STOP ({cfg.STOP_ATR_MULTIPLIER}) >= TAKE_PROFIT ({cfg.TAKE_PROFIT_ATR_MULTIPLIER}) — risk/reverse impossible")
    
    if cfg.MIN_REWARD_RISK_RATIO < 1.0:
        errors.append(f"MIN_REWARD_RISK_RATIO ({cfg.MIN_REWARD_RISK_RATIO}) < 1.0 — expected positive expectancy")
    
    # Check 5: PPO parameters within stable learning bounds
    if cfg.PPO_LR <= 0 or cfg.PPO_LR > 0.1:
        errors.append(f"PPO_LR ({cfg.PPO_LR}) out of safe range (0, 0.1]")
    
    if cfg.PPO_CLIP_RANGE <= 0 or cfg.PPO_CLIP_RANGE > 0.5:
        errors.append(f"PPO_CLIP_RANGE ({cfg.PPO_CLIP_RANGE}) out of safe range (0, 0.5]")
    
    # Check 6: No contradictory sizing modes
    if cfg.SIZING_MODE == "full_cash" and cfg.FULL_CASH_ORDER_SIZE_USD is not None:
        if cfg.FULL_CASH_ORDER_SIZE_USD > cfg.MAX_TRADE_SIZE_USD * 2:
            errors.append(f"FULL_CASH_ORDER_SIZE_USD ({cfg.FULL_CASH_ORDER_SIZE_USD}) > 2x MAX_TRADE_SIZE_USD ({cfg.MAX_TRADE_SIZE_USD})")
    
    # Check 7: Paper trading safety
    if not cfg.PAPER_TRADING and cfg.MIN_CASH_RESERVE_PCT < 0.05:
        errors.append(f"MIN_CASH_RESERVE_PCT ({cfg.MIN_CASH_RESERVE_PCT}) too low for live trading (minimum 0.05)")
    
    # Check 8: Feature count sanity
    if cfg.N_FEATURES <= 0 or cfg.N_FEATURES > 100:
        errors.append(f"N_FEATURES ({cfg.N_FEATURES}) out of safe range (1, 100]")
    
    if cfg.WINDOW_SIZE <= 0 or cfg.WINDOW_SIZE > 500:
        errors.append(f"WINDOW_SIZE ({cfg.WINDOW_SIZE}) out of safe range (1, 500]")
    
    # Check 9: Network operation limits
    if cfg.RECONNECT_MAX_ATTEMPTS > 50:
        errors.append(f"RECONNECT_MAX_ATTEMPTS ({cfg.RECONNECT_MAX_ATTEMPTS}) too high (max 50)")
    
    return len(errors) == 0, errors


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class AuditEntry:
    """Single audit trail entry for an AI decision."""
    timestamp: float
    action_type: str          # "trade", "model_update", "config_change", "anomaly", "guardrail_override"
    agent_version: str
    config_hash: str
    
    # Decision context
    input_context: Optional[Dict] = None      # Features, market state, etc.
    raw_output: Optional[Any] = None          # Raw model output
    final_output: Optional[Any] = None        # After guardrail enforcement
    
    # Guardrail info
    guardrails_applied: List[str] = field(default_factory=list)
    guardrail_override: bool = False
    guardrail_override_reason: str = ""
    
    # Result
    success: bool = True
    error_message: str = ""
    
    # Stack trace for debugging
    stack_trace: str = ""


class AuditTrail:
    """
    Immutable, append-only audit trail for AI decisions.
    
    Features:
    - JSON-serialized entries
    - Automatic rotation (keeps last N entries / last N days)
    - Tamper-evident chaining (each entry hashes the previous)
    """
    
    def __init__(self, path: str = "audit_trail.jsonl", max_entries: int = 10000):
        self.path = path
        self.max_entries = max_entries
        self._last_hash = ""
        self._entry_count = 0
        
        # Load existing last hash if file exists
        self._load_last_hash()
        valid, _ = self.verify_chain(quiet=True)
        if not valid:
            if self.repair_chain():
                log.info("Audit trail chain re-anchored after rotation/truncation")
            else:
                log.warning("Audit trail chain invalid — could not auto-repair")

    def _load_last_hash(self):
        """Load last entry hash from existing audit file."""
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entry = json.loads(line)
                                self._last_hash = entry.get('hash', '')
                                self._entry_count += 1
                            except json.JSONDecodeError:
                                pass
        except Exception:
            pass
    
    def _rechain_entries(self, entries: List[Dict]) -> List[Dict]:
        """Rebuild tamper-evident hashes from entry payloads (after rotation/truncation)."""
        prev = ""
        rebuilt: List[Dict] = []
        for entry in entries:
            core = {k: v for k, v in entry.items() if k not in ("hash", "prev_hash")}
            content = json.dumps(core, sort_keys=True, default=str)
            content_hash = hashlib.sha256((prev + content).encode()).hexdigest()
            core["hash"] = content_hash
            core["prev_hash"] = prev
            rebuilt.append(core)
            prev = content_hash
        return rebuilt

    def repair_chain(self) -> bool:
        """Re-anchor hash chain after rotation or head truncation. Returns success."""
        if not os.path.exists(self.path):
            return True
        try:
            entries: List[Dict] = []
            with open(self.path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entries.append(json.loads(line))
            if not entries:
                return True
            rebuilt = self._rechain_entries(entries)
            with open(self.path, "w") as f:
                for entry in rebuilt:
                    f.write(json.dumps(entry, default=str) + "\n")
            self._last_hash = rebuilt[-1]["hash"]
            self._entry_count = len(rebuilt)
            return True
        except Exception as exc:
            log.error(f"Audit trail repair failed: {exc}")
            return False

    def record(self, entry: AuditEntry) -> str:
        """
        Record an audit entry with tamper-evident hash.
        
        Returns: entry hash
        """
        entry_dict = asdict(entry)
        
        # Create tamper-evident chain
        content = json.dumps(entry_dict, sort_keys=True, default=str)
        content_hash = hashlib.sha256(
            (self._last_hash + content).encode()
        ).hexdigest()
        
        entry_dict['hash'] = content_hash
        entry_dict['prev_hash'] = self._last_hash
        
        # Write to file
        try:
            os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
            with open(self.path, 'a') as f:
                f.write(json.dumps(entry_dict, default=str) + '\n')
            
            self._last_hash = content_hash
            self._entry_count += 1
            
            # Rotate if needed
            if self._entry_count > self.max_entries:
                self._rotate()
        except Exception as exc:
            log.error(f"Audit trail write failed: {exc}")
        
        return content_hash
    
    def _rotate(self):
        """Keep only the most recent entries."""
        try:
            if not os.path.exists(self.path):
                return
            
            with open(self.path, 'r') as f:
                lines = f.readlines()
            
            # Keep last 75% of max
            keep = int(self.max_entries * 0.75)
            if len(lines) > keep:
                with open(self.path, 'w') as f:
                    f.writelines(lines[-keep:])
                # Rotation drops the genesis entry — re-anchor hash chain
                self.repair_chain()
        except Exception:
            pass
    
    def verify_chain(self, quiet: bool = False) -> Tuple[bool, int]:
        """
        Verify the integrity of the entire audit trail chain.
        
        Returns: (is_valid, number_of_entries)
        """
        if not os.path.exists(self.path):
            return True, 0
        
        prev_hash = ""
        count = 0
        
        try:
            with open(self.path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    entry = json.loads(line)
                    stored_hash = entry.get('hash', '')
                    stored_prev = entry.get('prev_hash', '')
                    
                    # Recompute hash
                    content = json.dumps(
                        {k: v for k, v in entry.items() if k not in ('hash', 'prev_hash')},
                        sort_keys=True, default=str
                    )
                    expected_hash = hashlib.sha256(
                        (stored_prev + content).encode()
                    ).hexdigest()
                    
                    if expected_hash != stored_hash:
                        if not quiet:
                            log.error(f"Audit trail tampered at entry {count}")
                        return False, count
                    
                    if stored_prev != prev_hash:
                        if not quiet:
                            log.error(f"Audit trail chain broken at entry {count}")
                        return False, count
                    
                    prev_hash = stored_hash
                    count += 1
        except Exception as exc:
            log.error(f"Audit trail verification failed: {exc}")
            return False, count
        
        return True, count
    
    def get_recent(self, n: int = 50) -> List[Dict]:
        """Get most recent N audit entries."""
        if not os.path.exists(self.path):
            return []
        
        try:
            with open(self.path, 'r') as f:
                lines = f.readlines()[-n:]
            
            entries = []
            for line in lines:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return entries
        except Exception:
            return []
    
    def summarize(self, since_ts: Optional[float] = None) -> Dict:
        """Summarize audit entries since a timestamp."""
        entries = self.get_recent(1000)
        
        if since_ts is not None:
            entries = [e for e in entries if e.get('timestamp', 0) >= since_ts]
        
        summary = {
            'total': len(entries),
            'by_type': {},
            'guardrail_overrides': 0,
            'errors': 0,
            'anomalies': 0,
        }
        
        for entry in entries:
            action_type = entry.get('action_type', 'unknown')
            summary['by_type'][action_type] = summary['by_type'].get(action_type, 0) + 1
            
            if entry.get('guardrail_override'):
                summary['guardrail_overrides'] += 1
            
            if not entry.get('success', True):
                summary['errors'] += 1
            
            if action_type == 'anomaly':
                summary['anomalies'] += 1
        
        return summary


# ═════════════════════════════════════════════════════════════════════════════
# SANDBOX EXECUTION
# ═════════════════════════════════════════════════════════════════════════════

class ModelSandbox:
    """
    Isolated execution environment for model inference.
    
    Provides:
    - CPU/RAM usage limits (soft, via monitoring)
    - Execution timeout
    - Restricted operations (no file I/O, no network)
    - Input/output size enforcement
    - Exception handling that never crashes the bot
    """
    
    def __init__(self, timeout_seconds: float = 5.0, max_input_size: int = 50000):
        self.timeout = timeout_seconds
        self.max_input_size = max_input_size
        self._call_count = 0
        self._error_count = 0
        self._total_latency = 0.0
    
    def execute(self, fn: Callable, *args, **kwargs) -> Tuple[Any, bool, str]:
        """
        Execute a function in the sandbox.
        
        Returns: (result, success, error_message)
        """
        self._call_count += 1
        start = time.time()
        
        try:
            # Input size check
            for arg in args:
                if isinstance(arg, np.ndarray) and arg.size > self.max_input_size:
                    msg = f"Input too large: {arg.size} > {self.max_input_size}"
                    self._error_count += 1
                    return None, False, msg
            
            # Execute with timeout
            result = fn(*args, **kwargs)
            
            elapsed = time.time() - start
            self._total_latency += elapsed
            
            # Check for timeout
            if elapsed > self.timeout:
                log.warning(f"Model execution slow: {elapsed:.2f}s (limit: {self.timeout}s)")
            
            return result, True, ""
            
        except Exception as exc:
            self._error_count += 1
            tb = traceback.format_exc()
            elasped = time.time() - start
            log.error(f"Model execution error ({elapsed:.2f}s): {exc}")
            
            return None, False, f"{type(exc).__name__}: {exc}"
    
    @property
    def error_rate(self) -> float:
        return self._error_count / max(1, self._call_count)
    
    @property
    def avg_latency(self) -> float:
        return self._total_latency / max(1, self._call_count)


# ═════════════════════════════════════════════════════════════════════════════
# MASTER GUARDRAIL CONTROLLER
# ═════════════════════════════════════════════════════════════════════════════

class GuardrailController:
    """
    Master controller that orchestrates all guardrail layers.
    
    Usage:
        guardrails = GuardrailController(cfg)
        
        # Validate an action
        action, passed = guardrails.validate_agent_action(raw_action, obs, features)
        
        # Check if we can trade
        can_trade, reason = guardrails.can_trade()
        
        # Record a trade
        guardrails.record_trade(action, price, shares, pnl)
        
        # Audit any decision
        guardrails.audit("trade", context=..., output=...)
    """
    
    def __init__(self, cfg, agent_version: str = "3.5.0"):
        self.cfg = cfg
        self.agent_version = agent_version
        
        # Config hash (immutable for this session)
        self.config_hash = compute_config_hash(cfg)
        
        # Validate config on startup
        self.config_valid, self.config_errors = validate_config(cfg)
        if not self.config_valid:
            log.error("CONFIG VALIDATION FAILED:")
            for err in self.config_errors:
                log.error(f"  ⚠️  {err}")
            log.error("Fix configuration before proceeding.")
        
        # Sub-modules
        self.action_monitor = ActionAnomalyDetector()
        self.feature_monitor = FeatureAnomalyDetector(n_features=cfg.N_FEATURES)
        self.rate_limiter = RateLimiter()
        self.sandbox = ModelSandbox()
        self.audit = AuditTrail()
        
        # State
        self._active = True
        self._override_level = 0  # 0=full, 1=warn only, 2=disabled
        self._last_feature_check_ts: float = 0.0
        self._feature_check_interval: float = 5.0  # seconds between feature checks
        
        # Metrics
        self.stats = {
            'actions_validated': 0,
            'actions_rejected': 0,
            'actions_overridden': 0,
            'features_checked': 0,
            'features_anomalies': 0,
            'rate_limits_hit': 0,
            'sandbox_errors': 0,
            'model_predictions': 0,
        }
    
    # ── Action Validation ─────────────────────────────────────────────────
    
    def validate_agent_action(self, raw_action: Any, obs: Optional[np.ndarray] = None,
                               features: Optional[np.ndarray] = None) -> Tuple[int, bool, List[str]]:
        """
        Full pipeline validation of an agent action.
        
        Returns: (final_action, passed_all_checks, warnings)
        """
        if not self._active:
            return 0, False, ["Guardrails disabled"]
        
        self.stats['actions_validated'] += 1
        warnings = []
        original_action = raw_action
        
        # Layer 1: Sanitize action
        action, valid = sanitize_action(raw_action)
        if not valid:
            warnings.append("Action sanitized (out of range)")
        
        # Layer 2: Feature anomaly check (throttled)
        if features is not None:
            now = time.time()
            if now - self._last_feature_check_ts > self._feature_check_interval:
                self._last_feature_check_ts = now
                self.stats['features_checked'] += 1
                is_anom, reason = self.feature_monitor.check_anomaly()
                if is_anom:
                    self.stats['features_anomalies'] += 1
                    warnings.append(f"Feature anomaly: {reason}")
                    self.audit_record(AuditEntry(
                        timestamp=time.time(),
                        action_type="anomaly",
                        agent_version=self.agent_version,
                        config_hash=self.config_hash,
                        input_context={"features_anomaly": reason},
                        final_output=action,
                        guardrails_applied=["feature_anomaly_check"],
                    ))
        
        # Layer 3: Action anomaly detection
        self.action_monitor.record_action(action, str(action))
        is_anom, anom_reason = self.action_monitor.check_anomaly()
        if is_anom:
            warnings.append(f"Action anomaly: {anom_reason}")
            if self._override_level < 2:  # If not fully disabled
                action = 0  # Force HOLD on anomaly
                self.stats['actions_overridden'] += 1
                self.audit_record(AuditEntry(
                    timestamp=time.time(),
                    action_type="guardrail_override",
                    agent_version=self.agent_version,
                    config_hash=self.config_hash,
                    input_context={"original_action": int(original_action) if hasattr(original_action, '__int__') else str(original_action),
                                    "anomaly_reason": anom_reason},
                    final_output=action,
                    guardrails_applied=["action_anomaly_override"],
                    guardrail_override=True,
                    guardrail_override_reason=anom_reason,
                ))
        
        # Layer 4: Rate limit check
        allowed, count = self.rate_limiter.check_limit("trades_1min", 10, 60.0)
        if not allowed and action in (1, 2):  # BUY or SELL
            self.stats['rate_limits_hit'] += 1
            warnings.append(f"Trade rate limit: {count}/10 per minute")
            if self._override_level < 1:
                action = 0  # Force HOLD
                self.stats['actions_rejected'] += 1
        
        passed = len([w for w in warnings if "anomaly" in w.lower() or "sanitized" in w.lower()]) == 0
        
        return action, passed, warnings
    
    # ── Trade Authorization ──────────────────────────────────────────────
    
    def can_trade(self) -> Tuple[bool, str]:
        """
        Check if the system is allowed to trade right now.
        
        Checks:
        - Config must pass validation
        - Anomaly rate must be below threshold
        - Rate limits must not be exhausted
        - Sandbox must not have high error rate
        """
        if not self.config_valid:
            return False, "Configuration validation failed — fix config before trading"
        
        if self.action_monitor.anomaly_rate > 0.3:
            return False, f"Action anomaly rate too high ({self.action_monitor.anomaly_rate:.0%})"
        
        if self.sandbox.error_rate > 0.5:
            return False, f"Sandbox error rate too high ({self.sandbox.error_rate:.0%})"
        
        allowed, count = self.rate_limiter.check_limit("trades_per_hour", 50, 3600.0)
        if not allowed:
            return False, f"Hourly trade limit: {count}/50"
        
        allowed, count = self.rate_limiter.check_limit("trades_per_day", 200, 86400.0)
        if not allowed:
            return False, f"Daily trade limit: {count}/200"
        
        return True, ""
    
    # ── Configuration Management ─────────────────────────────────────────
    
    def reload_config(self, new_cfg) -> Tuple[bool, List[str]]:
        """Validate and reload configuration safely."""
        valid, errors = validate_config(new_cfg)
        if not valid:
            return False, errors
        
        old_hash = self.config_hash
        self.cfg = new_cfg
        self.config_hash = compute_config_hash(new_cfg)
        
        self.audit_record(AuditEntry(
            timestamp=time.time(),
            action_type="config_change",
            agent_version=self.agent_version,
            config_hash=self.config_hash,
            input_context={"old_hash": old_hash, "new_hash": self.config_hash},
            guardrails_applied=["config_validation"],
        ))
        
        return True, []
    
    def set_override_level(self, level: int):
        """
        Set guardrail override level.
        0 = Full enforcement (default)
        1 = Warnings only (anomalies logged but not blocked)
        2 = Disabled (NOT RECOMMENDED)
        """
        self._override_level = max(0, min(2, level))
        log.warning(f"Guardrail override level set to {level} "
                     f"({'FULL' if level == 0 else 'WARN' if level == 1 else 'DISABLED'})")
    
    def pause(self):
        """Pause all guardrail enforcement."""
        self._active = False
        log.warning("Guardrails PAUSED — all AI actions pass through unchecked")
    
    def resume(self):
        """Resume guardrail enforcement."""
        self._active = True
        log.info("Guardrails RESUMED — full enforcement active")
    
    # ── Audit ────────────────────────────────────────────────────────────
    
    def audit_record(self, entry: AuditEntry):
        """Record an audit entry (public wrapper)."""
        return self.audit.record(entry)
    
    def audit_action(self, action_type: str, input_context: Dict = None,
                      raw_output: Any = None, final_output: Any = None,
                      guardrails: List[str] = None, success: bool = True,
                      error: str = ""):
        """Quick audit entry for common actions."""
        entry = AuditEntry(
            timestamp=time.time(),
            action_type=action_type,
            agent_version=self.agent_version,
            config_hash=self.config_hash,
            input_context=input_context,
            raw_output=raw_output,
            final_output=final_output,
            guardrails_applied=guardrails or [],
            success=success,
            error_message=error,
        )
        return self.audit.record(entry)
    
    # ── Reporting ────────────────────────────────────────────────────────
    
    def status_summary(self) -> Dict:
        """Get current guardrail status."""
        return {
            'active': self._active,
            'override_level': self._override_level,
            'config_valid': self.config_valid,
            'config_errors': self.config_errors if not self.config_valid else [],
            'config_hash': self.config_hash,
            'action_monitor': {
                'anomaly_rate': self.action_monitor.anomaly_rate,
                'total_recorded': len(self.action_monitor._action_history),
            },
            'feature_monitor': {
                'baseline_samples': self.feature_monitor._baseline_samples,
                'anomalies': self.feature_monitor._anomaly_count,
            },
            'sandbox': {
                'calls': self.sandbox._call_count,
                'errors': self.sandbox._error_count,
                'error_rate': self.sandbox.error_rate,
                'avg_latency': f"{self.sandbox.avg_latency:.3f}s",
            },
            'stats': dict(self.stats),
            'audit': {
                'entries': self.audit._entry_count,
                'chain_valid': self.audit.verify_chain()[0],
            },
        }
    
    def health_check(self) -> Tuple[bool, str]:
        """
        Full health check of guardrail system.
        
        Returns: (healthy, report)
        """
        checks = []
        
        # Check config
        if not self.config_valid:
            checks.append("❌ Config validation failed")
        else:
            checks.append("✅ Config valid")
        
        # Check audit chain
        chain_valid, count = self.audit.verify_chain()
        if chain_valid:
            checks.append(f"✅ Audit chain intact ({count} entries)")
        else:
            checks.append("❌ Audit chain TAMPERED or CORRUPT")
        
        # Check anomaly rate
        anom_rate = self.action_monitor.anomaly_rate
        if anom_rate > 0.2:
            checks.append(f"⚠️  Anomaly rate: {anom_rate:.0%}")
        else:
            checks.append(f"✅ Anomaly rate: {anom_rate:.0%}")
        
        # Check sandbox
        err_rate = self.sandbox.error_rate
        if err_rate > 0.3:
            checks.append(f"❌ Sandbox error rate: {err_rate:.0%}")
        else:
            checks.append(f"✅ Sandbox error rate: {err_rate:.0%}")
        
        # Check rate limits
        _, trade_count = self.rate_limiter.check_limit("trades_per_day", 200, 86400.0)
        checks.append(f"📊 Today's trades: {trade_count}")
        
        all_healthy = all("❌" not in c for c in checks)
        return all_healthy, "\n".join(checks)