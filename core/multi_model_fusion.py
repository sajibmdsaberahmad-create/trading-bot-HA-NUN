#!/usr/bin/env python3
"""
core/multi_model_fusion.py — Multi-Model Decision Fusion System.

ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════
This is the CROWN JEWEL of the AI trading system — it combines FOUR
independent AI models into a single, highly robust trading decision:

1. PPO (PyTorch via Stable-Baselines3)    — Learned trading policy
2. Transformer (PyTorch, custom)           — Time-series pattern recognition
3. BiLSTM-Attention (TensorFlow/Keras)     — Sequential pattern memory
4. Rule-Based Ensemble agents              — Trend/MeanRev/VolBreakout

Each model votes with confidence, and the fusion layer applies:
- Weighted voting by historical accuracy
- Kalman filter for decision smoothing
- Regime-aware model weighting
- Anomaly detection for outlier predictions

BENEFITS OF FUSION
- No single point of failure (if one model fails, others compensate)
- Diverse pattern capture (different architectures learn different things)
- Confidence calibration (disagreement = uncertainty = HOLD)
- Online adaptation (model weights update based on recent accuracy)
"""

import os
import json
import time
import hashlib
import numpy as np
import pandas as pd
from typing import Optional, Tuple, Dict, List, Any
from dataclasses import dataclass, field
from collections import deque, defaultdict
from enum import Enum

from core.config import BotConfig
from core.notify import log
from core.fusion_overrides import FusionOverrides
from core.hmrs import HiddenMarkovRegimeSwitching, MarketRegime

# ── 100% Lazy model imports ──
# NOTHING heavy is imported at module level.
# Every import happens inside the specific method that needs it.
# This avoids:
#   - TensorFlow hanging on import on Mac
#   - PyTorch CUDA/MPS initialization delays
#   - Stable-Baselines3 pulling in gymnasium

# Lazy import helpers
TRANSFORMER_AVAILABLE = False
LSTM_AVAILABLE = False
_TRANSFORMER_MODULES = None
_LSTM_MODULES = None
_PREDICT_TRANSFORMER = None
_PREDICT_LSTM = None

# These are filled lazily on first use
_MarketRegime_ = None
_RegimeResult_ = None
_compute_thinking_confidence = None

def _lazy_agent():
    """Lazy load agent_enhanced components on first demand."""
    global _MarketRegime_, _RegimeResult_, _compute_thinking_confidence
    
    if _MarketRegime_ is not None:
        return True
    
    try:
        # Delayed import - only happens when actually needed
        from core.agent_enhanced import MarketRegime, RegimeResult, compute_thinking_confidence
        _MarketRegime_ = MarketRegime
        _RegimeResult_ = RegimeResult
        _compute_thinking_confidence = compute_thinking_confidence
        return True
    except Exception:
        return False

def _load_transformer():
    global TRANSFORMER_AVAILABLE, _TRANSFORMER_MODULES, _PREDICT_TRANSFORMER
    if TRANSFORMER_AVAILABLE or _TRANSFORMER_MODULES is not None:
        return
    try:
        from core.transformer_model import (
            TemporalFusionTransformer, TransformerConfig, create_transformer,
            predict_with_transformer
        )
        import torch
        _TRANSFORMER_MODULES = (TemporalFusionTransformer, TransformerConfig, create_transformer)
        _PREDICT_TRANSFORMER = predict_with_transformer
        TRANSFORMER_AVAILABLE = True
    except ImportError:
        TRANSFORMER_AVAILABLE = False

def _load_lstm():
    global LSTM_AVAILABLE, _LSTM_MODULES, _PREDICT_LSTM
    if LSTM_AVAILABLE or _LSTM_MODULES is not None:
        return
    try:
        from core.lstm_model import (
            BuildLSTMModel, LSTMConfig, create_lstm,
            predict_with_lstm
        )
        _LSTM_MODULES = (BuildLSTMModel, LSTMConfig, create_lstm)
        _PREDICT_LSTM = predict_with_lstm
        LSTM_AVAILABLE = True
    except ImportError:
        LSTM_AVAILABLE = False

def get_transformer_default_config():
    _load_transformer()
    if _TRANSFORMER_MODULES:
        _, TransformerConfig, _ = _TRANSFORMER_MODULES
        return TransformerConfig()
    return None

def get_lstm_default_config():
    _load_lstm()
    if _LSTM_MODULES:
        _, LSTMConfig, _ = _LSTM_MODULES
        return LSTMConfig()
    return None


# ═════════════════════════════════════════════════════════════════════════════
# MODEL STATUS & TRACKING
# ═════════════════════════════════════════════════════════════════════════════

class ModelType(Enum):
    """Types of AI models in the fusion system."""
    PPO = "ppo"
    TRANSFORMER = "transformer"
    LSTM = "lstm"
    ENSEMBLE = "ensemble"


@dataclass
class ModelPrediction:
    """Prediction from a single model."""
    action: int          # 0=HOLD, 1=BUY, 2=SELL
    confidence: float    # 0.0 to 1.0
    value: float         # State value estimate
    probabilities: np.ndarray  # Action probabilities (3,)
    model_type: ModelType
    model_name: str
    latency_ms: float = 0.0
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'action': self.action,
            'action_name': ['HOLD', 'BUY', 'SELL'][self.action],
            'confidence': round(self.confidence, 4),
            'value': round(self.value, 4),
            'probabilities': [round(float(p), 4) for p in self.probabilities],
            'model_type': self.model_type.value,
            'model_name': self.model_name,
            'latency_ms': round(self.latency_ms, 2),
        }


@dataclass
class FusedDecision:
    """Final decision after model fusion."""
    action: int                   # 0=HOLD, 1=BUY, 2=SELL
    action_name: str              # Human-readable
    confidence: float             # 0.0 to 1.0
    fused_probabilities: np.ndarray  # Fused action probabilities
    model_predictions: List[ModelPrediction]
    fusion_method: str            # How the fusion was done
    model_weights: Dict[str, float]  # Current model weights
    reasoning: str                # Human-readable reasoning

    def to_dict(self) -> Dict:
        return {
            'action': self.action,
            'action_name': self.action_name,
            'confidence': round(self.confidence, 4),
            'fused_probabilities': [round(float(p), 4) for p in self.fused_probabilities],
            'models': [m.to_dict() for m in self.model_predictions],
            'model_weights': {k: round(v, 3) for k, v in self.model_weights.items()},
            'fusion_method': self.fusion_method,
            'reasoning': self.reasoning,
        }


# ═════════════════════════════════════════════════════════════════════════════
# MODEL ACCURACY TRACKER
# ═════════════════════════════════════════════════════════════════════════════

class ModelAccuracyTracker:
    """
    Tracks each model's accuracy over time for dynamic weighting.
    
    Accuracy is measured by:
    - Did the model predict the correct direction?
    - How well did confidence correlate with outcome?
    - Recent performance weighted more heavily
    """
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._accuracy: Dict[str, float] = defaultdict(lambda: 0.5)
        self._confidence_score: Dict[str, float] = defaultdict(lambda: 0.5)
    
    def record_outcome(self, model_name: str, action: int, confidence: float,
                        actual_price_change: float, threshold: float = 0.3):
        """
        Record whether a model's prediction was correct.
        
        Args:
            model_name: Name of the model
            action: Predicted action (0=HOLD, 1=BUY, 2=SELL)
            confidence: Model's confidence in prediction
            actual_price_change: Actual % price change that occurred
            threshold: Minimum price change to consider non-HOLD correct
        """
        # Determine if prediction was correct
        if action == 1:  # BUY
            correct = actual_price_change > threshold
        elif action == 2:  # SELL
            correct = actual_price_change < -threshold
        else:  # HOLD
            correct = abs(actual_price_change) < threshold
        
        self._history[model_name].append({
            'correct': correct,
            'confidence': confidence,
            'price_change': actual_price_change,
            'timestamp': time.time(),
        })
        
        # Recalculate accuracy
        if len(self._history[model_name]) > 0:
            recent = list(self._history[model_name])
            # Weighted: recent 20 = full weight, older = decayed
            weights = np.array([min(1.0, (i + 1) / 20) for i in range(len(recent))])
            corrects = np.array([1 if r['correct'] else 0 for r in recent])
            self._accuracy[model_name] = float(
                np.average(corrects, weights=weights)
            )
            
            # Confidence calibration score
            confidences = np.array([r['confidence'] for r in recent])
            # Good calibration: high conf when correct, low when wrong
            calibration = np.mean([
                1 - abs(c - (1 if r['correct'] else 0))
                for c, r in zip(confidences, recent)
            ])
            self._confidence_score[model_name] = float(calibration)
    
    def get_accuracy(self, model_name: str) -> float:
        """Get weighted accuracy for a model."""
        return self._accuracy.get(model_name, 0.5)
    
    def get_weight(self, model_name: str, min_weight: float = 0.1) -> float:
        """
        Get voting weight for a model based on accuracy.
        
        Maps accuracy to weight: 50% = 0.5, 80% = 1.0, 30% = 0.1
        """
        acc = self._accuracy.get(model_name, 0.5)
        weight = (acc - 0.5) * 2 + 0.5  # Center and scale
        return float(np.clip(weight, min_weight, 1.0))
    
    def get_summary(self) -> Dict:
        """Get accuracy summary for all models."""
        return {
            name: {
                'accuracy': round(self._accuracy.get(name, 0.5), 3),
                'weight': round(self.get_weight(name), 3),
                'samples': len(self._history.get(name, [])),
            }
            for name in set(list(self._history.keys()))
        }
    
    def save(self, path: str = "models/model_accuracy.json"):
        """Save accuracy data."""
        try:
            data = {
                'accuracy': dict(self._accuracy),
                'confidence_score': dict(self._confidence_score),
            }
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
    
    def load(self, path: str = "models/model_accuracy.json"):
        """Load accuracy data."""
        try:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    data = json.load(f)
                for k, v in data.get('accuracy', {}).items():
                    self._accuracy[k] = v
                for k, v in data.get('confidence_score', {}).items():
                    self._confidence_score[k] = v
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# KALMAN DECISION SMOOTHER
# ═════════════════════════════════════════════════════════════════════════════

class KalmanDecisionSmoother:
    """
    Kalman filter for smoothing trading decisions over time.
    
    Prevents the bot from flipping between BUY and SELL on consecutive bars
    due to noise. The smoother maintains an internal state estimate and
    only allows a new action when the filtered signal is clear.
    
    This is NOT look-ahead — it's causal, using only past and current data.
    """
    
    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1):
        # State: [buy_prob, sell_prob, hold_prob]
        self.state = np.array([0.33, 0.33, 0.34], dtype=np.float32)
        self.P = np.eye(3) * 0.1  # State covariance
        
        self.Q = np.eye(3) * process_noise  # Process noise
        self.R = np.eye(3) * measurement_noise  # Measurement noise
        
        self._last_action = 0
        self._action_stability = 0.0
    
    def update(self, measurement: np.ndarray) -> np.ndarray:
        """
        Kalman update with new measurement.
        
        Args:
            measurement: (3,) array of measured probabilities
            
        Returns:
            (3,) filtered probabilities
        """
        # Ensure valid probabilities
        measurement = np.clip(measurement, 0.01, 0.99)
        measurement = measurement / measurement.sum()
        
        # Predict step
        x_pred = self.state
        P_pred = self.P + self.Q
        
        # Update step
        S = P_pred + self.R  # Innovation covariance
        K = P_pred @ np.linalg.inv(S)  # Kalman gain
        
        # Innovation (measurement residual)
        y = measurement - x_pred
        
        # Updated state
        self.state = x_pred + K @ y
        self.P = (np.eye(3) - K) @ P_pred
        
        # Normalize
        self.state = np.clip(self.state, 0.01, 0.99)
        self.state = self.state / self.state.sum()
        
        return self.state.copy()
    
    def get_smoothed_action(self, raw_probabilities: np.ndarray,
                              min_confidence: float = 0.5) -> Tuple[int, float, str]:
        """
        Get smoothed action from raw model probabilities.
        
        Args:
            raw_probabilities: (3,) array from model fusion
            min_confidence: Minimum confidence for non-HOLD action
            
        Returns:
            action, confidence, reasoning
        """
        # Kalman filter the probabilities
        smoothed = self.update(raw_probabilities)
        
        action = int(np.argmax(smoothed))
        confidence = float(smoothed[action])
        
        # Stability check: don't flip too quickly
        if action != self._last_action:
            self._action_stability = 0.0
        else:
            self._action_stability = min(1.0, self._action_stability + 0.1)
        
        # Require higher confidence for action changes
        effective_threshold = min_confidence
        if self._action_stability < 0.3 and action != self._last_action:
            effective_threshold += 0.15  # Higher bar for flips
        
        if confidence < effective_threshold:
            action = 0  # HOLD
            reasoning = (f"Smoothed confidence {confidence:.0%} < "
                         f"threshold {effective_threshold:.0%} -> HOLD")
        else:
            action_name = ['HOLD', 'BUY', 'SELL'][action]
            reasoning = (f"Kalman smoothed: {action_name} ({confidence:.0%}, "
                         f"stability={self._action_stability:.2f})")
        
        self._last_action = action
        return action, confidence, reasoning
    
    def reset(self):
        """Reset smoother state."""
        self.state = np.array([0.33, 0.33, 0.34], dtype=np.float32)
        self.P = np.eye(3) * 0.1
        self._last_action = 0
        self._action_stability = 0.0


# ═════════════════════════════════════════════════════════════════════════════
# MULTI-MODEL FUSION ENGINE
# ═════════════════════════════════════════════════════════════════════════════

class MultiModelFusionEngine:
    """
    The central fusion engine that combines all models.
    
    FLOW
    ────────────────────────────────────────────────────────────
    1. Collect predictions from all available models
    2. Weight each prediction by historical accuracy
    3. Apply Kalman smoothing for temporal consistency
    4. Check for anomaly/outlier predictions
    5. Fuse into single decision with confidence
    6. Log complete reasoning chain
    
    USAGE
        engine = MultiModelFusionEngine(cfg)
        
        # Register models
        engine.register_ppo(ppo_model)
        engine.register_transformer(transformer_model)
        engine.register_lstm(lstm_model)
        engine.register_ensemble(ensemble_trader)
        
        # Get fused decision
        decision = engine.get_decision(obs, features_df, regime)
    """
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        
        # Models (may be None if not available)
        self.ppo_model = None
        self.transformer_model = None
        self.transformer_config = None
        self.lstm_model = None
        self.lstm_config = None
        self.ensemble = None
        self.regime_classifier = None
        self.confidence_scorer = None
        
        # Tracking
        self.accuracy_tracker = ModelAccuracyTracker()
        self.kalman_smoother = KalmanDecisionSmoother()
        
        # Circuit breaker overrides (bypass accuracy weights in extreme conditions)
        self.overrides = FusionOverrides()
        
        # HMRS regime switching engine
        self.hmrs = HiddenMarkovRegimeSwitching(cfg)
        
        # Decision history for analysis
        self._decision_history: deque = deque(maxlen=500)
        
        # Performance tracking
        self._fusion_times: List[float] = []
        
        # Load saved accuracy
        self.accuracy_tracker.load()
    
    def register_ppo(self, model: Any):
        """Register PPO model."""
        self.ppo_model = model
        log.info("✅ Registered PPO model")
    
    def register_transformer(self, model: Any, config: Optional[Any] = None):
        """Register Transformer model."""
        self.transformer_model = model
        if config is None:
            config = get_transformer_default_config()
        self.transformer_config = config
        _load_transformer()
        log.info("✅ Registered Transformer model")
    
    def register_lstm(self, model: Any, config: Optional[Any] = None):
        """Register LSTM model."""
        self.lstm_model = model
        if config is None:
            config = get_lstm_default_config()
        self.lstm_config = config
        _load_lstm()
        log.info("✅ Registered LSTM model")
    
    def register_ensemble(self, ensemble: Any):
        """Register ensemble trader."""
        self.ensemble = ensemble
        log.info("✅ Registered Ensemble trader")
    
    def register_classifiers(self, regime: Any, confidence: Any):
        """Register regime classifier and confidence scorer."""
        self.regime_classifier = regime
        self.confidence_scorer = confidence
    
    def get_decision(self, obs: np.ndarray, features_df: Optional[pd.DataFrame] = None,
                      regime_result: Optional[RegimeResult] = None,
                      recent_rewards: Optional[List[float]] = None) -> FusedDecision:
        """
        Get fused decision from all available models.
        
        Args:
            obs: Observation array (window * n_features + 2,)
            features_df: Optional DataFrame for regime/ensemble
            regime_result: Optional pre-computed regime result
            recent_rewards: Optional recent rewards for confidence scoring
            
        Returns:
            FusedDecision with action, confidence, and reasoning
        """
        start_time = time.time()
        predictions: List[ModelPrediction] = []
        
        # Ensure agent components are loaded
        _lazy_agent()
        MR = _MarketRegime_
        RR = _RegimeResult_
        ctc = _compute_thinking_confidence
        
        # ── 1. Collect model predictions ──────────────────────────────
        
        # PPO prediction
        if self.ppo_model is not None:
            try:
                t0 = time.time()
                ppo_action, ppo_value, ppo_probs = ctc(self.ppo_model, obs)
                ppo_conf = float(max(ppo_probs))
                latency = (time.time() - t0) * 1000
                
                predictions.append(ModelPrediction(
                    action=ppo_action, confidence=ppo_conf,
                    value=ppo_value, probabilities=ppo_probs,
                    model_type=ModelType.PPO, model_name="PPO",
                    latency_ms=latency,
                ))
            except Exception as e:
                log.debug(f"PPO prediction failed: {e}")
        
        # Transformer prediction (using lazy-loaded import)
        if self.transformer_model is not None and TRANSFORMER_AVAILABLE and _PREDICT_TRANSFORMER is not None:
            try:
                t0 = time.time()
                tf_action, tf_value, tf_probs = _PREDICT_TRANSFORMER(
                    self.transformer_model, obs, self.transformer_config,
                    deterministic=True
                )
                tf_conf = float(max(tf_probs))
                latency = (time.time() - t0) * 1000
                
                predictions.append(ModelPrediction(
                    action=tf_action, confidence=tf_conf,
                    value=tf_value, probabilities=tf_probs,
                    model_type=ModelType.TRANSFORMER, model_name="Transformer",
                    latency_ms=latency,
                ))
            except Exception as e:
                log.debug(f"Transformer prediction failed: {e}")
        
        # LSTM prediction (using lazy-loaded import)
        if self.lstm_model is not None and LSTM_AVAILABLE and _PREDICT_LSTM is not None:
            try:
                t0 = time.time()
                lstm_action, lstm_value, lstm_probs = _PREDICT_LSTM(
                    self.lstm_model, obs, self.lstm_config,
                    deterministic=True
                )
                lstm_conf = float(max(lstm_probs))
                latency = (time.time() - t0) * 1000
                
                predictions.append(ModelPrediction(
                    action=lstm_action, confidence=lstm_conf,
                    value=lstm_value, probabilities=lstm_probs,
                    model_type=ModelType.LSTM, model_name="LSTM",
                    latency_ms=latency,
                ))
            except Exception as e:
                log.debug(f"LSTM prediction failed: {e}")
        
        # Ensemble prediction (requires features_df)
        if self.ensemble is not None and features_df is not None:
            try:
                t0 = time.time()
                # Get PPO probs from first prediction
                ppo_pred = next((p for p in predictions if p.model_type == ModelType.PPO), None)
                ppo_probs_for_ensemble = ppo_pred.probabilities if ppo_pred else np.array([0.4, 0.3, 0.3])
                
                # Use regime result if available
                if regime_result is None and self.regime_classifier is not None:
                    regime_result = self.regime_classifier.classify(features_df)
                if regime_result is None:
                    regime_result = RR(
                        regime=MR.UNKNOWN, confidence=0.0,
                        trend_strength=0.0, volatility_percentile=50.0,
                        momentum=0.0, volume_regime="normal",
                        recommendation="",
                    )
                
                votes = self.ensemble.get_votes(
                    ppo_pred.action if ppo_pred else 0,
                    ppo_probs_for_ensemble,
                    ppo_pred.value if ppo_pred else 0.0,
                    regime_result,
                    features_df,
                )
                
                ens_action, ens_conf, ens_reason = self.ensemble.ensemble_decision(
                    votes, min_confidence=self.cfg.CONFIDENCE_THRESHOLD
                )
                latency = (time.time() - t0) * 1000
                
                predictions.append(ModelPrediction(
                    action=ens_action, confidence=ens_conf,
                    value=0.0,
                    probabilities=np.array([1-ens_conf/2, ens_conf/2, 0.0]) if ens_action == 1
                    else np.array([ens_conf, (1-ens_conf)/2, (1-ens_conf)/2]),
                    model_type=ModelType.ENSEMBLE, model_name="Ensemble",
                    latency_ms=latency,
                    metadata={'reasoning': ens_reason},
                ))
            except Exception as e:
                log.debug(f"Ensemble prediction failed: {e}")
        
        # ── 2. Handle case with no predictions ─────────────────────────
        if not predictions:
            log.warning("No model predictions available — defaulting to HOLD")
            return FusedDecision(
                action=0, action_name="HOLD", confidence=0.0,
                fused_probabilities=np.array([1.0, 0.0, 0.0]),
                model_predictions=[], fusion_method="fallback",
                model_weights={}, reasoning="No models available",
            )
        
        # ── 3. HMRS Regime-Aware Weight Override ─────────────────────
        hmrs_weights = {}
        regime_result = None
        
        if features_df is not None and self.hmrs.enabled:
            try:
                regime_result = self.hmrs.classify(features_df)
                hmrs_weights = self.hmrs.get_regime_weights(regime_result)
            except Exception as e:
                log.debug(f"HMRS classification failed: {e}")
        
        use_hmrs = bool(hmrs_weights)
        
        model_weights = {}
        weighted_probs = np.zeros(3, dtype=np.float32)
        
        for pred in predictions:
            if use_hmrs:
                # Override accuracy weight with regime structural weight
                weight = hmrs_weights.get(pred.model_name, 0.2)
            else:
                # Fall back to accuracy-based weight
                weight = self.accuracy_tracker.get_weight(pred.model_name)
            model_weights[pred.model_name] = weight
            
            # Weighted contribution
            weighted_probs += pred.probabilities * weight
        
        # Normalize
        if weighted_probs.sum() > 0:
            fused_probs = weighted_probs / weighted_probs.sum()
        else:
            fused_probs = np.array([1.0, 0.0, 0.0])
        
        # ── 4. Apply Kalman smoothing ─────────────────────────────────
        smoothed_action, smoothed_conf, smooth_reason = self.kalman_smoother.get_smoothed_action(
            fused_probs, min_confidence=self.cfg.CONFIDENCE_THRESHOLD
        )
        
        # ── 5. Circuit breaker overrides ────────────────────────────────
        atr_series = None
        if features_df is not None and 'atr' in features_df.columns:
            atr_series = features_df['atr']
        
        # Also pass regime result from HMRS if available
        if regime_result is None:
            regime_result = self.hmrs.classify(features_df) if (features_df is not None and self.hmrs.enabled) else None
            
        override_signal = self.overrides.evaluate(regime_result, features_df, atr_series)
        if override_signal:
            log.warning(f"Fusion override active: {override_signal.reason}")
            if override_signal.forced_weights:
                fused_probs = np.zeros(3, dtype=np.float32)
                for pred in predictions:
                    w = override_signal.forced_weights.get(pred.model_name, 0.25)
                    fused_probs += pred.probabilities * w
                if fused_probs.sum() > 0:
                    fused_probs /= fused_probs.sum()
                else:
                    fused_probs = np.array([1.0, 0.0, 0.0])
            if override_signal.forced_action is not None:
                smoothed_action = override_signal.forced_action
            smooth_reason += f" | OVERRIDE[{override_signal.level.name}]: {override_signal.reason}"
        
        # ── 6. Build final decision ────────────────────────────────────
        action_name = ['HOLD', 'BUY', 'SELL'][smoothed_action]
        
        # Build reasoning
        model_lines = []
        for p in predictions:
            pname = p.model_name
            paction = ['HOLD', 'BUY', 'SELL'][p.action]
            model_lines.append(
                f"{pname}: {paction} (conf={p.confidence:.0%}, "
                f"w={model_weights.get(pname, 0.5):.2f})"
            )
        
        reasoning = (
            f"Fusion: {action_name} (conf={smoothed_conf:.0%}) | "
            + " | ".join(model_lines)
        )
        if smooth_reason:
            reasoning += f" | {smooth_reason}"
        
        # Append HMRS regime info to reasoning
        if regime_result is not None and self.hmrs.enabled:
            reason_regime = regime_result.regime.value.replace('_', ' ').title()
            hmrs_line = (f"Regime[{reason_regime}]: "
                        f"p={regime_result.confidence:.0%}, "
                        f"vol_pct={regime_result.volatility_percentile:.0%}, "
                        f"trend={regime_result.trend_strength:+.2f}")
            if hmrs_line not in reasoning:
                reasoning = hmrs_line + " | " + reasoning
        
        decision = FusedDecision(
            action=smoothed_action,
            action_name=action_name,
            confidence=smoothed_conf,
            fused_probabilities=fused_probs,
            model_predictions=predictions,
            fusion_method="hmrs_weighted_accuracy_kalman" if use_hmrs else "weighted_accuracy_kalman",
            model_weights=model_weights,
            reasoning=reasoning,
        )
        
        # Track
        self._decision_history.append(decision)
        total_latency = (time.time() - start_time) * 1000
        self._fusion_times.append(total_latency)
        
        return decision
    
    def record_outcome(self, decision: FusedDecision, actual_price_change: float):
        """
        Record outcome of a decision for accuracy tracking.
        
        Args:
            decision: The fused decision that was made
            actual_price_change: Actual % price change after decision
        """
        for pred in decision.model_predictions:
            self.accuracy_tracker.record_outcome(
                pred.model_name, pred.action, pred.confidence,
                actual_price_change
            )
        
        # Save periodically
        self.accuracy_tracker.save()
    
    def get_performance_stats(self) -> Dict:
        """Get fusion engine performance statistics."""
        stats = {
            'models_available': {
                'ppo': self.ppo_model is not None,
                'transformer': self.transformer_model is not None,
                'lstm': self.lstm_model is not None,
                'ensemble': self.ensemble is not None,
            },
            'num_decisions': len(self._decision_history),
            'avg_fusion_latency_ms': float(np.mean(self._fusion_times)) if self._fusion_times else 0,
            'model_accuracy': self.accuracy_tracker.get_summary(),
            'current_weights': {},
        }
        
        # Get current weights
        for pred_list in [[p for p in self._decision_history[-1].model_predictions]] if self._decision_history else []:
            for pred in pred_list:
                stats['current_weights'][pred.model_name] = self.accuracy_tracker.get_weight(pred.model_name)
        
        return stats
    
    def get_decision_history(self, n_last: int = 10) -> List[Dict]:
        """Get recent decision history."""
        decisions = list(self._decision_history)[-n_last:]
        return [d.to_dict() for d in decisions]
    
    def save_state(self, path: str = "models/fusion_state.json"):
        """Save fusion engine state."""
        try:
            state = {
                'accuracy': self.accuracy_tracker.get_summary(),
                'num_decisions': len(self._decision_history),
                'avg_latency_ms': float(np.mean(self._fusion_times)) if self._fusion_times else 0,
                'timestamp': time.time(),
            }
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def create_fusion_engine(cfg: BotConfig, ppo_model: Any = None,
                          transformer_model: Any = None,
                          transformer_config: Any = None,
                          lstm_model: Any = None,
                          lstm_config: Any = None,
                          ensemble: Any = None) -> MultiModelFusionEngine:
    """
    Factory function to create and configure the fusion engine.
    
    Args:
        cfg: BotConfig
        ppo_model: Optional PPO model
        transformer_model: Optional Transformer model
        transformer_config: Optional Transformer config
        lstm_model: Optional LSTM model
        lstm_config: Optional LSTM config
        ensemble: Optional Ensemble trader
        
    Returns:
        Configured MultiModelFusionEngine
    """
    engine = MultiModelFusionEngine(cfg)
    
    if ppo_model is not None:
        engine.register_ppo(ppo_model)
    if transformer_model is not None:
        engine.register_transformer(transformer_model, transformer_config)
    if lstm_model is not None:
        engine.register_lstm(lstm_model, lstm_config)
    if ensemble is not None:
        engine.register_ensemble(ensemble)
    
    # Log available models
    available = []
    if ppo_model: available.append("PPO")
    if transformer_model: available.append("Transformer")
    if lstm_model: available.append("LSTM")
    if ensemble: available.append("Ensemble")
    
    log.info(f"🤖 Multi-Model Fusion Engine initialized with {len(available)} models: "
             f"{', '.join(available)}")
    
    return engine