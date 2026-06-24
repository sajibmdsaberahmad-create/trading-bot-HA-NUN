#!/usr/bin/env python3
"""
core/hmrs.py — Hidden Markov Regime Switching Engine.

ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════
This module classifies market conditions into latent regimes using a
Gaussian Mixture Hidden Markov Model (GMM-HMM). Instead of letting models
debate blindly, the HMRS engine calculates the precise probability of the
current regime and hard-allocates voting power to the model architecture
explicitly built for that regime.

REGIMES
• QuietGrowth    — Low vol, trending up. Weight: Transformer (long-horizon)
• HighVolTrend   — High vol, strong directional move. Weight: Transformer + Ensemble
• LiquidChop     — Range-bound, noisy. Weight: BiLSTM (short-horizon mean-rev)
• LiquidityShock — Flash crash, halt, or extreme vol spike. Weight: Circuit Breakers

The regime probability vector is passed into MultiModelFusionEngine which
overrides the accuracy-based weights with these structural allocations.
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from enum import Enum
from collections import deque
import threading
import time

from core.config import BotConfig
from core.notify import log


class MarketRegime(Enum):
    QUIET_GROWTH = "quiet_growth"
    HIGH_VOL_TREND = "high_vol_trend"
    LIQUID_CHOP = "liquid_chop"
    LIQUIDITY_SHOCK = "liquidity_shock"


@dataclass
class RegimeResult:
    regime: MarketRegime
    confidence: float          # Probability of current regime
    regime_probs: Dict[str, float]  # Full probability vector
    trend_strength: float
    volatility_percentile: float
    momentum: float
    volume_regime: str
    recommendation: str

    def to_dict(self) -> Dict:
        return {
            'regime': self.regime.value,
            'confidence': round(self.confidence, 4),
            'regime_probs': {k: round(v, 4) for k, v in self.regime_probs.items()},
            'trend_strength': round(self.trend_strength, 4),
            'volatility_percentile': round(self.volatility_percentile, 4),
            'momentum': round(self.momentum, 4),
            'volume_regime': self.volume_regime,
            'recommendation': self.recommendation,
        }


class GaussianHMM:
    """
    Lightweight Gaussian Mixture HMM for regime detection.
    
    Uses EM algorithm to fit:
    - Transition matrix A (num_regimes x num_regimes)
    - Means vector mu (num_regimes x n_features)
    - Covariance matrices Sigma (num_regimes x n_features x n_features)
    - Stationary distribution pi (num_regimes,)
    
    Inference is done via Viterbi for MAP state sequence or forward algorithm
    for marginal state probabilities.
    """

    def __init__(self, n_regimes: int = 4, n_features: int = 8,
                 max_iter: int = 50, tol: float = 1e-4, random_state: int = 42):
        self.n_regimes = n_regimes
        self.n_features = n_features
        self.max_iter = max_iter
        self.tol = tol
        
        rng = np.random.default_rng(random_state)
        # Transition matrix (row-stochastic)
        self.A = rng.dirichlet([1.0] * n_regimes, size=n_regimes)
        # Means
        self.mu = rng.standard_normal((n_regimes, n_features))
        # Covariances (diagonal for stability)
        self.Sigma = np.stack([np.eye(n_features) * (0.5 + rng.random()) for _ in range(n_regimes)])
        # Stationary distribution
        self.pi = rng.dirichlet([1.0] * n_regimes)
        
        self._fitted = False
        self._log_likelihood_history: list = []

    def _gaussian_logpdf(self, x: np.ndarray, mu: np.ndarray, Sigma: np.ndarray) -> np.ndarray:
        """Compute log N(x | mu, Sigma) for diagonal covariance."""
        diff = x - mu
        var = np.diag(Sigma)
        log_det = np.sum(np.log(var + 1e-9))
        mahal = np.sum(diff ** 2 / (var + 1e-9), axis=-1)
        return -0.5 * (self.n_features * np.log(2 * np.pi) + log_det + mahal)

    def _forward(self, obs: np.ndarray) -> Tuple[np.ndarray, float]:
        """Forward algorithm: compute alpha[t,i] = P(state_i at t | obs_1:t)."""
        T = len(obs)
        alpha = np.zeros((T, self.n_regimes))
        
        # Init
        log_pi = np.log(self.pi + 1e-12)
        log_emit = np.array([self._gaussian_logpdf(obs[0], self.mu[k], self.Sigma[k])
                             for k in range(self.n_regimes)])
        alpha[0] = log_pi + log_emit
        
        # Recursion (in log-space)
        log_A = np.log(self.A + 1e-12)
        for t in range(1, T):
            for j in range(self.n_regimes):
                log_emit_j = self._gaussian_logpdf(obs[t], self.mu[j], self.Sigma[j])
                alpha[t, j] = log_emit_j + np.logaddexp.reduce(alpha[t-1] + log_A[:, j])
        
        log_likelihood = np.logaddexp.reduce(alpha[-1])
        return alpha, log_likelihood

    def _backward(self, obs: np.ndarray) -> np.ndarray:
        """Backward algorithm for gamma smoothing."""
        T = len(obs)
        beta = np.zeros((T, self.n_regimes))
        log_A = np.log(self.A.T + 1e-12)
        
        for t in range(T - 2, -1, -1):
            for i in range(self.n_regimes):
                log_emit = np.array([self._gaussian_logpdf(obs[t+1], self.mu[k], self.Sigma[k])
                                     for k in range(self.n_regimes)])
                beta[t, i] = np.logaddexp.reduce(log_A[i] + log_emit + beta[t+1])
        return beta

    def fit(self, obs: np.ndarray):
        """
        Fit the HMM to observation sequence using Baum-Welch (EM).
        
        Args:
            obs: (T, n_features) observation sequence
        """
        if obs.ndim == 1:
            obs = obs.reshape(-1, 1)
        
        T = len(obs)
        prev_ll = -np.inf
        
        for iteration in range(self.max_iter):
            # E-step: compute gamma and xi
            alpha, log_likelihood = self._forward(obs)
            beta = self._backward(obs)
            
            log_gamma = alpha + beta
            log_gamma -= np.logaddexp.reduce(log_gamma, axis=1, keepdims=True)
            gamma = np.exp(log_gamma)
            
            # xi for transitions
            log_xi = np.zeros((T - 1, self.n_regimes, self.n_regimes))
            log_A = np.log(self.A + 1e-12)
            for t in range(T - 1):
                log_emit = np.array([self._gaussian_logpdf(obs[t+1], self.mu[k], self.Sigma[k])
                                     for k in range(self.n_regimes)])
                log_xi[t] = (alpha[t][:, None] + log_A + log_emit[None, :] + beta[t+1][None, :])
            log_xi -= np.logaddexp.reduce(log_xi, axis=(1, 2), keepdims=True)
            xi = np.exp(log_xi)
            
            # M-step
            # Update pi
            self.pi = gamma[0].copy()
            self.pi /= self.pi.sum() + 1e-12
            
            # Update A
            self.A = xi.sum(axis=0)
            self.A /= (self.A.sum(axis=1, keepdims=True) + 1e-12)
            
            # Update mu and Sigma
            for k in range(self.n_regimes):
                total_weight = gamma[:, k].sum()
                if total_weight > 0:
                    self.mu[k] = (gamma[:, k][:, None] * obs).sum(axis=0) / (total_weight + 1e-12)
                    diff = obs - self.mu[k]
                    self.Sigma[k] = (gamma[:, k][:, None] * (diff ** 2)).sum(axis=0) / (total_weight + 1e-12)
                    self.Sigma[k] += np.eye(self.n_features) * 1e-4  # Regularization
            
            self._log_likelihood_history.append(log_likelihood)
            
            # Convergence check
            if abs(log_likelihood - prev_ll) < self.tol:
                log.info(f"HMRS converged at iteration {iteration}, log-likelihood={log_likelihood:.2f}")
                break
            prev_ll = log_likelihood
        
        self._fitted = True
        return log_likelihood

    def decode(self, obs: np.ndarray) -> np.ndarray:
        """Viterbi decoding: most likely state sequence."""
        if obs.ndim == 1:
            obs = obs.reshape(-1, 1)
        
        T = len(obs)
        delta = np.zeros((T, self.n_regimes))
        psi = np.zeros((T, self.n_regimes), dtype=int)
        
        # Init
        log_pi = np.log(self.pi + 1e-12)
        log_emit = np.array([self._gaussian_logpdf(obs[0], self.mu[k], self.Sigma[k])
                             for k in range(self.n_regimes)])
        delta[0] = log_pi + log_emit
        
        # Recursion
        log_A = np.log(self.A + 1e-12)
        for t in range(1, T):
            for j in range(self.n_regimes):
                log_emit_j = self._gaussian_logpdf(obs[t], self.mu[j], self.Sigma[j])
                scores = delta[t-1] + log_A[:, j]
                psi[t, j] = np.argmax(scores)
                delta[t, j] = log_emit_j + scores[psi[t, j]]
        
        # Backtrack
        states = np.zeros(T, dtype=int)
        states[T-1] = np.argmax(delta[T-1])
        for t in range(T-2, -1, -1):
            states[t] = psi[t+1, states[t+1]]
        return states

    def predict_proba(self, obs: np.ndarray) -> np.ndarray:
        """Forward algorithm: marginal state probabilities at each timestep."""
        alpha, _ = self._forward(obs)
        log_gamma = alpha - np.logaddexp.reduce(alpha, axis=1, keepdims=True)
        return np.exp(log_gamma)


class RegimeFeatureExtractor:
    """Extract stationary feature vectors suitable for HMRS classification."""
    
    def __init__(self, window: int = 60):
        self.window = window
    
    def extract(self, df: pd.DataFrame) -> np.ndarray:
        """
        Extract 8-dimensional stationary feature vector from OHLCV data.
        
        Features (all stationary/relative):
        1. log_return_window_mean (fractional return over window)
        2. log_return_std (rolling vol)
        3. volume_zscore (relative to median)
        4. spread_proxy (high-low / close)
        5. momentum_5 (5-bar return)
        6. momentum_20 (20-bar return)
        7. volume_trend (correlation of volume with price)
        8. gap_indicator (opening gap relative to ATR)
        """
        if len(df) < self.window:
            return np.zeros(self.window * 8, dtype=np.float32)
        
        close = df['close'].values[-self.window:]
        high = df['high'].values[-self.window:]
        low = df['low'].values[-self.window:]
        volume = df['volume'].values[-self.window:]
        
        # 1. Fractional log returns (using d=0.4 for stationarity)
        log_ret = np.diff(np.log(close + 1e-9))
        ret_mean = np.mean(log_ret)
        
        # 2. Volatility
        ret_std = np.std(log_ret)
        
        # 3. Volume Z-score
        vol_median = np.median(volume)
        vol_std = np.std(volume)
        vol_zscore = (volume[-1] - vol_median) / (vol_std + 1e-9)
        
        # 4. Spread proxy (HL/Close)
        spread = (high[-1] - low[-1]) / (close[-1] + 1e-9)
        
        # 5. Short momentum
        mom_5 = (close[-1] / close[-6] - 1) if len(close) >= 6 else 0.0
        
        # 6. Medium momentum
        mom_20 = (close[-1] / close[-21] - 1) if len(close) >= 21 else 0.0
        
        # 7. Volume-Price trend correlation
        if len(log_ret) >= 10:
            vol_normalized = volume[-len(log_ret):] / (vol_median + 1e-9)
            corr = np.corrcoef(log_ret[-10:], vol_normalized[-10:])[0, 1]
            vp_corr = corr if not np.isnan(corr) else 0.0
        else:
            vp_corr = 0.0
        
        # 8. Gap indicator (if we have previous close)
        if len(df) > self.window:
            prev_close = df['close'].values[-self.window - 1]
            atr = np.mean(high[-14:] - low[-14:]) if len(high) >= 14 else spread * close[-1]
            gap = (close[-1] - prev_close) / (atr + 1e-9)
        else:
            gap = 0.0
        
        features = np.array([
            ret_mean, ret_std, vol_zscore, spread,
            mom_5, mom_20, vp_corr, gap
        ], dtype=np.float32)
        
        # Normalize to roughly [-3, 3] range
        features = np.clip(features, -5, 5)
        return features


class HiddenMarkovRegimeSwitching:
    """
    Main HMRS engine that combines GMM-HMM with heuristic regime labeling.
    
    Provides:
    1. Real-time regime classification
    2. Probability-aware weight allocation per regime
    3. Regime-specific model recommendations
    4. Periodic retraining on updated data
    """
    
    # Structural weight allocation: which model dominates in which regime
    REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
        MarketRegime.QUIET_GROWTH.value: {
            'Transformer': 0.60, 'PPO': 0.25, 'LSTM': 0.10, 'Ensemble': 0.05
        },
        MarketRegime.HIGH_VOL_TREND.value: {
            'Transformer': 0.55, 'PPO': 0.20, 'LSTM': 0.15, 'Ensemble': 0.10
        },
        MarketRegime.LIQUID_CHOP.value: {
            'LSTM': 0.50, 'PPO': 0.25, 'Transformer': 0.15, 'Ensemble': 0.10
        },
        MarketRegime.LIQUIDITY_SHOCK.value: {
            'Ensemble': 0.20, 'Transformer': 0.10, 'PPO': 0.10, 'LSTM': 0.10
            # In shock, weights are near-uniform but override will short-circuit anyway
        },
    }

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.enabled = cfg.HMRS_ENABLED
        self.n_regimes = cfg.HMRS_NUM_REGIMES
        self.lookback_days = cfg.HMRS_LOOKBACK_DAYS
        self.retrain_hours = cfg.HMRS_RETRAIN_HOURS
        self.min_prob = cfg.HMRS_MIN_REGIME_PROB
        
        self.feature_extractor = RegimeFeatureExtractor(window=60)
        self.hmm: Optional[GaussianHMM] = None
        self._regime_history: deque = deque(maxlen=1000)
        self._last_train_time: float = 0.0
        # RLock (reentrant): classify() holds the lock and calls _check_retrain()
        # which re-acquires it. A plain Lock would deadlock here.
        self._lock = threading.RLock()
        self._warmup_passed = False
        
        # Heuristic fallback parameters
        self._vol_percentile_95: Optional[float] = None
        
        if self.enabled:
            log.info(f"🧠 HMRS Engine initialized: {self.n_regimes} regimes, "
                     f"lookback={self.lookback_days}d, retrain={self.retrain_hours}h")
    
    def _check_retrain(self, df: pd.DataFrame):
        """Retrain HMM if enough time has passed since last fit."""
        now = time.time()
        hours_since_last = (now - self._last_train_time) / 3600.0
        
        if hours_since_last < self.retrain_hours or self.hmm is not None:
            return
        
        with self._lock:
            if self.hmm is not None:
                return
            self._train_hmm(df)
    
    def _train_hmm(self, df: pd.DataFrame):
        """Train HMM on historical feature sequence."""
        try:
            log.info("🔄 Retraining HMRS HMM on historical data...")
            features = self._build_feature_sequence(df)
            if len(features) < 200:
                # Throttle retries (and log spam): wait before trying to train again.
                self._last_train_time = time.time()
                log.debug("Insufficient data for HMRS training, using heuristic regime")
                return
            
            self.hmm = GaussianHMM(n_regimes=self.n_regimes, n_features=features.shape[1])
            ll = self.hmm.fit(features)
            self._last_train_time = time.time()
            log.info(f"✅ HMRS trained: {len(features)} samples, log-likelihood={ll:.2f}")
        except Exception as e:
            log.error(f"HMRS training failed: {e}")
    
    def _build_feature_sequence(self, df: pd.DataFrame, step: int = 5) -> np.ndarray:
        """Build a rolling-window sequence of 8-dim regime features for HMM training."""
        window = self.feature_extractor.window
        if len(df) <= window:
            return np.zeros((1, 8))
        windows = []
        for i in range(window, len(df) + 1, step):
            window_df = df.iloc[i - window:i]
            feat = self.feature_extractor.extract(window_df)
            windows.append(feat)
        # Bound sequence length so Baum-Welch (pure-numpy EM) stays fast and
        # never blocks the trading loop for long. Keep the most recent samples.
        if len(windows) > 600:
            windows = windows[-600:]
        return np.array(windows) if windows else np.zeros((1, 8))
    
    def classify(self, df: pd.DataFrame) -> RegimeResult:
        """
        Classify current market regime.
        
        Args:
            df: Recent OHLCV DataFrame (must have at least window rows)
            
        Returns:
            RegimeResult with regime, confidence, and weight recommendations
        """
        if not self.enabled:
            return self._heuristic_classify(df)
        
        with self._lock:
            # Trigger retrain if needed
            self._check_retrain(df)
            
            if self.hmm is None:
                self._warmup_passed = True
                return self._heuristic_classify(df)
            
            try:
                features = self.feature_extractor.extract(df).reshape(1, -1)
                probs = self.hmm.predict_proba(features)[0]
                
                best_idx = int(np.argmax(probs))
                regimes = [
                    MarketRegime.QUIET_GROWTH,
                    MarketRegime.HIGH_VOL_TREND,
                    MarketRegime.LIQUID_CHOP,
                    MarketRegime.LIQUIDITY_SHOCK,
                ]
                best_regime = regimes[best_idx]
                confidence = float(probs[best_idx])
                
                # Heuristic calibration: if vol is extreme, boost liquidity shock prob
                vol_pct = self._compute_vol_percentile(df)
                if vol_pct > 0.95:
                    overlay = RegimeResult(
                        regime=MarketRegime.LIQUIDITY_SHOCK,
                        confidence=min(0.99, confidence * 1.3),
                        regime_probs={r.value: 0.0 for r in regimes},
                        trend_strength=self._trend_strength(df),
                        volatility_percentile=vol_pct,
                        momentum=self._momentum(df),
                        volume_regime=self._volume_regime(df),
                        recommendation="FULL_HALT",
                    )
                    overlay.regime_probs[MarketRegime.LIQUIDITY_SHOCK.value] = 1.0
                    self._regime_history.append(overlay)
                    return overlay
                
                regime_probs = {regimes[i].value: float(probs[i]) for i in range(len(regimes))}
                
                result = RegimeResult(
                    regime=best_regime,
                    confidence=confidence,
                    regime_probs=regime_probs,
                    trend_strength=self._trend_strength(df),
                    volatility_percentile=vol_pct,
                    momentum=self._momentum(df),
                    volume_regime=self._volume_regime(df),
                    recommendation=self._recommendation(best_regime, confidence, vol_pct),
                )
                self._regime_history.append(result)
                return result
                
            except Exception as e:
                log.debug(f"HMRS classification failed, falling back to heuristic: {e}")
                return self._heuristic_classify(df)
    
    def _heuristic_classify(self, df: pd.DataFrame) -> RegimeResult:
        """Fallback heuristic classification when HMM is not available."""
        vol_pct = self._compute_vol_percentile(df)
        trend = self._trend_strength(df)
        mom = self._momentum(df)
        vol_regime = self._volume_regime(df)
        
        if vol_pct > 0.95:
            regime = MarketRegime.LIQUIDITY_SHOCK
            conf = 0.85
        elif vol_pct > 0.75 and abs(trend) > 0.6:
            regime = MarketRegime.HIGH_VOL_TREND
            conf = 0.7
        elif vol_pct < 0.3 and abs(trend) < 0.3:
            regime = MarketRegime.LIQUID_CHOP
            conf = 0.6
        else:
            regime = MarketRegime.QUIET_GROWTH
            conf = 0.55
        
        probs = {r.value: 0.05 for r in MarketRegime}
        probs[regime.value] = conf
        # Normalize
        total = sum(probs.values())
        probs = {k: v / total for k, v in probs.items()}
        
        return RegimeResult(
            regime=regime, confidence=conf, regime_probs=probs,
            trend_strength=trend, volatility_percentile=vol_pct,
            momentum=mom, volume_regime=vol_regime,
            recommendation=self._recommendation(regime, conf, vol_pct),
        )
    
    def _compute_vol_percentile(self, df: pd.DataFrame) -> float:
        """Compute where current volatility ranks in recent history."""
        if len(df) < 20:
            return 0.5
        close = df['close'].values
        log_ret = np.diff(np.log(close[-60:] + 1e-9))
        current_vol = np.std(log_ret[-20:]) if len(log_ret) >= 20 else np.std(log_ret)
        historical_vols = [
            np.std(log_ret[i:i+20]) for i in range(len(log_ret) - 20)
        ]
        if not historical_vols:
            return 0.5
        pct = np.mean(current_vol <= np.array(historical_vols))
        return float(np.clip(pct, 0.0, 1.0))
    
    def _trend_strength(self, df: pd.DataFrame) -> float:
        """Compute normalized trend strength [-1, 1]."""
        if len(df) < 20:
            return 0.0
        close = df['close'].values[-20:]
        # Linear regression slope normalized by price
        x = np.arange(len(close))
        slope = np.polyfit(x, close, 1)[0]
        normalized = slope / (np.mean(close) + 1e-9) * 100
        return float(np.clip(normalized, -1.0, 1.0))
    
    def _momentum(self, df: pd.DataFrame) -> float:
        """Normalized momentum [-1, 1]."""
        if len(df) < 10:
            return 0.0
        close = df['close'].values
        ret_5 = close[-1] / close[-6] - 1 if len(close) >= 6 else 0.0
        return float(np.clip(ret_5 * 50, -1.0, 1.0))
    
    def _volume_regime(self, df: pd.DataFrame) -> str:
        """Classify volume relative to recent median."""
        if len(df) < 20 or 'volume' not in df.columns:
            return "normal"
        vol = df['volume'].values[-20:]
        median = np.median(vol)
        if vol[-1] > median * 2.0:
            return "high"
        elif vol[-1] < median * 0.5:
            return "low"
        return "normal"
    
    def _recommendation(self, regime: MarketRegime, confidence: float, vol_pct: float) -> str:
        """Generate action recommendation text."""
        if confidence < 0.4:
            return "UNCERTAIN: Low regime confidence"
        recs = {
            MarketRegime.QUIET_GROWTH: "NORMAL: Quiet growth regime, standard execution",
            MarketRegime.HIGH_VOL_TREND: "CAUTION: High-volatility trend, widen stops",
            MarketRegime.LIQUID_CHOP: "DEFENSIVE: Chop regime, reduce position size",
            MarketRegime.LIQUIDITY_SHOCK: "FULL_HALT: Liquidity crisis detected",
        }
        return recs.get(regime, "NEUTRAL")
    
    def get_regime_weights(self, regime_result: Optional[RegimeResult] = None) -> Dict[str, float]:
        """
        Get structural model weights for the current regime.
        
        These weights override accuracy-based weights in MultiModelFusionEngine
        when the HMRS confidence exceeds the minimum threshold.
        """
        if regime_result is None or regime_result.confidence < self.min_prob:
            return {}
        
        weights = self.REGIME_WEIGHTS.get(regime_result.regime.value, {})
        if regime_result.regime == MarketRegime.LIQUIDITY_SHOCK:
            # Force near-uniform weights (override logic handles the rest)
            weights = {k: 0.25 for k in weights}
        return weights
    
    def get_allocation(self, regime_result: RegimeResult) -> Dict[str, float]:
        """Alias for get_regime_weights for cleaner API naming."""
        return self.get_regime_weights(regime_result)
    
    @property
    def is_trained(self) -> bool:
        return self.hmm is not None and self.hmm._fitted
    
    def get_stats(self) -> Dict:
        recent = list(self._regime_history)[-20:] if self._regime_history else []
        counts = {}
        for r in recent:
            counts[r.regime.value] = counts.get(r.regime.value, 0) + 1
        return {
            'trained': self.is_trained,
            'regime_counts': counts,
            'last_regime': self._regime_history[-1].to_dict() if self._regime_history else None,
        }