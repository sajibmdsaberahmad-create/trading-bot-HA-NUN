#!/usr/bin/env python3
"""
core/agent.py — PPO agent construction, loading, and the online
fine-tuning manager used during live trading.

DEVICE SELECTION
The bot auto-detects and uses the fastest available compute on
whatever machine it runs on:
  - NVIDIA GPU (VPS with CUDA)  -> CUDA
  - Apple Silicon (M1/M2/M3/M4) -> MPS (Metal)
  - Anything else                -> CPU

No config changes needed when you move from Mac to VPS — device="auto"
in PPO handles it, and the device actually selected is logged at
startup so you can confirm acceleration is active.

ENHANCED AI MODE
When cfg.USE_ENHANCED_AI is True (default), this module automatically
integrates:
  - Market regime classifier (core/agent_enhanced.py)
  - Ensemble voting (PPO + Trend + MR + Breakout)
  - Confidence scoring with thresholds
  - AI guardrails (input sanitization, anomaly detection, rate limiting)
  - Tamper-evident audit trail
  - Adaptive online learning (regime-aware)
"""

import os
from typing import Optional, Tuple, Dict, List, Any

import numpy as np
import pandas as pd

try:
    import gymnasium as gym
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
except ImportError:
    raise SystemExit("ERROR: gymnasium/stable-baselines3 not installed.")

from collections import deque
from core.config import BotConfig
from core.env import TradingEnv
from core.notify import log
from core.pilot_experience import PilotExperienceSystem
from core.pattern_memory_bank import PatternMemoryBank

# ── Conditional imports for enhanced AI ──────────────────────────────────
_enhanced_available = False
try:
    from core.agent_enhanced import (
        MarketRegimeClassifier, MarketRegime, RegimeResult,
        ConfidenceScorer, ReasoningChain,
        EnsembleTrader, AdaptiveLearner,
        build_enhanced_agent as _build_enhanced,
        compute_thinking_confidence,
    )
    from core.ai_guardrails import (
        GuardrailController, AuditEntry, sanitize_observation,
        sanitize_action, validate_config, compute_config_hash,
    )
    from core.features_enhanced import FeatureEngineerEnhanced
    _enhanced_available = True
except ImportError as exc:
    log.warning(f"Enhanced AI components not available ({exc}). Using standard agent.")


def build_ppo_agent(env: gym.Env, cfg: BotConfig, model_path: Optional[str] = None,
                     return_components: bool = False) -> Any:
    """
    Build a PPO agent. If enhanced AI is enabled (cfg.USE_ENHANCED_AI),
    builds the full enhanced pipeline.
    
    Args:
        env: Trading environment
        cfg: Bot configuration
        model_path: Optional path to existing model
        return_components: If True, returns (model, components_dict)
        
    Returns:
        PPO model, or (PPO, dict) if return_components=True
    """
    if _enhanced_available and cfg.USE_ENHANCED_AI:
        log.info("🧠 ENHANCED AI MODE ACTIVE — building full reasoning pipeline")
        model, components = _build_enhanced(cfg, model_path, verbose=1)
        
        if return_components:
            return model, components
        return model
    
    # Standard (legacy) path
    vec_env = DummyVecEnv([lambda: env])

    if model_path and os.path.exists(model_path):
        log.info(f"Loading existing model from {model_path} …")
        # CPU for stable sub-ms live inference (MPS adds latency + SB3 warnings for MLP)
        model = PPO.load(model_path, env=vec_env, device="cpu")
        model.set_env(vec_env)
    else:
        log.info("Building new PPO agent from scratch …")
        model = PPO(
            policy="MlpPolicy",
            env=vec_env,
            n_steps=cfg.PPO_N_STEPS,
            batch_size=cfg.PPO_BATCH_SIZE,
            n_epochs=cfg.PPO_N_EPOCHS,
            clip_range=cfg.PPO_CLIP_RANGE,
            max_grad_norm=cfg.PPO_MAX_GRAD_NORM,
            learning_rate=cfg.PPO_LR,
            gamma=cfg.PPO_GAMMA,
            gae_lambda=cfg.PPO_GAE_LAM,
            ent_coef=cfg.PPO_ENT_COEF,
            vf_coef=cfg.PPO_VF_COEF,
            policy_kwargs=dict(net_arch=list(cfg.PPO_NET_ARCH)),
            verbose=1,
            device="auto",
        )

    device_name = str(model.device)
    log.info(f"PPO agent ready | device: {device_name} | obs: {env.observation_space.shape} | net: {cfg.PPO_NET_ARCH}")
    if "mps" in device_name:
        log.info("Apple Metal (MPS) GPU acceleration active")
    elif "cuda" in device_name:
        log.info("NVIDIA CUDA GPU acceleration active")
    else:
        log.info("Running on CPU (no GPU detected)")
    
    if return_components:
        return model, {}
    return model


def run_deterministic_episode(model: PPO, env: gym.Env,
                               components: Optional[Dict[str, Any]] = None) -> Tuple[float, Dict]:
    """Run a deterministic evaluation episode."""
    obs, _ = env.reset()
    done = False
    info = {}
    actions = []

    while not done:
        if components and 'guardrails' in components:
            # Use guardrail-sanitized observation
            expected_shape = (env.observation_space.shape[0],)
            obs, valid = sanitize_observation(obs, expected_shape)
        
        action, _ = model.predict(obs, deterministic=True)
        
        if components and 'guardrails' in components:
            action, passed, _ = components['guardrails'].validate_agent_action(action, obs)
        
        obs, _, terminated, truncated, info = env.step(int(action))
        done = terminated or truncated
        actions.append(int(action))

    action_counts = {"HOLD": actions.count(0), "BUY": actions.count(1), "SELL": actions.count(2)}
    info["action_counts"] = action_counts
    return float(info.get("portfolio_value", 0.0)), info


def predict_with_reasoning(model: PPO, obs: np.ndarray, cfg: BotConfig,
                            components: Dict[str, Any],
                            bar_df: Optional[pd.DataFrame] = None,
                            recent_rewards: Optional[List[float]] = None,
                            for_entry: bool = False,
                            ) -> Tuple[int, float, Optional[str]]:
    """
    Full enhanced prediction pipeline: regime → PPO → ensemble → guardrail → action.
    
    Args:
        model: PPO model
        obs: Observation vector
        cfg: Bot configuration
        components: Dict from build_ppo_agent(return_components=True)
        bar_df: Optional OHLCV DataFrame for regime classification & ensemble voting
        recent_rewards: Optional list of recent trade rewards for confidence scoring
        
    Returns:
        (action, confidence_score, reasoning_summary)
    """
    if not _enhanced_available or not cfg.USE_ENHANCED_AI:
        # Fall back to basic prediction
        action, _ = model.predict(obs, deterministic=True)
        return int(action), 0.5, None
    
    guardrails = components.get('guardrails')
    regime_classifier = components.get('regime_classifier')
    ensemble = components.get('ensemble')
    confidence_scorer = components.get('confidence_scorer')
    
    # Step 1: Sanitize observation
    expected_shape = obs.shape
    obs, obs_valid = sanitize_observation(obs, expected_shape)
    if not obs_valid:
        log.warning("Observation sanitization failed — returning HOLD")
        return 0, 0.0, "Observation invalid"
    
    # Step 2: Get PPO action with probabilities (thinking confidence)
    try:
        ppo_action, ppo_value, ppo_probs = compute_thinking_confidence(model, obs)
    except Exception as exc:
        log.error(f"Enhanced prediction failed: {exc}")
        action, _ = model.predict(obs, deterministic=True)
        return int(action), 0.3, f"Fallback: {exc}"
    
    # Step 3: Classify market regime
    regime_result = None
    if regime_classifier is not None and bar_df is not None and len(bar_df) >= 50:
        try:
            regime_result = regime_classifier.classify(bar_df)
            log.debug(f"Market regime: {regime_result.regime.value} "
                       f"(conf={regime_result.confidence:.0%}, "
                       f"adx={regime_result.trend_strength:.0f})")
        except Exception:
            pass
    
    if regime_result is None:
        regime_result = RegimeResult(
            regime=MarketRegime.UNKNOWN, confidence=0.0,
            trend_strength=0.0, volatility_percentile=50.0,
            momentum=0.0, volume_regime="normal",
            recommendation="No regime data",
        )
    
    buy_prob = float(ppo_probs[1]) if ppo_probs is not None and len(ppo_probs) > 1 else 0.0
    if for_entry and ppo_action == 2:
        ppo_action = 0

    # Step 4: Ensemble voting
    if ensemble is not None and cfg.USE_ENSEMBLE and bar_df is not None:
        try:
            votes = ensemble.get_votes(ppo_action, ppo_probs, ppo_value, regime_result, bar_df)
            ensemble_action, ensemble_conf, ensemble_reason = ensemble.ensemble_decision(
                votes, min_confidence=cfg.CONFIDENCE_THRESHOLD, for_entry=for_entry,
            )
            # Use ensemble if confident, fall back to PPO if ensemble is uncertain
            if ensemble_conf >= cfg.CONFIDENCE_THRESHOLD:
                final_action = ensemble_action
                final_confidence = ensemble_conf
                reasoning = ensemble_reason
            else:
                final_action = ppo_action
                final_confidence = float(max(ppo_probs)) if ppo_probs is not None else 0.5
                reasoning = f"Ensemble uncertain ({ensemble_conf:.0%}), using PPO"
        except Exception as exc:
            final_action = ppo_action
            final_confidence = float(max(ppo_probs)) if ppo_probs is not None else 0.5
            reasoning = f"Ensemble error: {exc}"
    else:
        final_action = ppo_action
        final_confidence = float(max(ppo_probs)) if ppo_probs is not None else 0.5
        reasoning = "PPO only (no ensemble)"

    if for_entry:
        if final_action == 2:
            final_action = 0
        if final_action != 1 and buy_prob >= cfg.CONFIDENCE_THRESHOLD:
            final_action = 1
            final_confidence = buy_prob
            reasoning = f"PPO buy prob {buy_prob:.0%} | {reasoning or ''}"
    
    # Step 5: Confidence scoring
    if confidence_scorer is not None:
        try:
            confidence = confidence_scorer.score(
                ppo_probs, ppo_value, regime_result,
                features=obs[:cfg.N_FEATURES] if len(obs) >= cfg.N_FEATURES else None,
                last_n_rewards=recent_rewards,
            )
            # Low confidence: block exits, but let entry gate decide on BUY signals
            if confidence < cfg.CONFIDENCE_THRESHOLD:
                if final_action == 2:
                    log.debug(f"Low confidence {confidence:.0%} — blocking exit signal")
                    final_action = 0
                    reasoning = f"Low confidence for exit ({confidence:.0%})"
                elif final_action == 0:
                    reasoning = f"Low confidence ({confidence:.0%})"
                # BUY (action==1): pass through — scalper entry gate applies threshold + technical override
            final_confidence = confidence
        except Exception:
            pass
    
    # Step 6: Guardrail validation
    if guardrails is not None and cfg.USE_GUARDRAILS:
        try:
            action, passed, warnings = guardrails.validate_agent_action(
                final_action, obs, 
                features=obs[:cfg.N_FEATURES] if len(obs) >= cfg.N_FEATURES else None,
            )
            if passed:
                final_action = action
            else:
                log.debug(f"Guardrail warnings: {'; '.join(warnings)}")
        except Exception as exc:
            log.warning(f"Guardrail validation error: {exc}")
    
    # Step 7: Audit trail
    if guardrails is not None:
        try:
            guardrails.audit_action(
                "trade_decision",
                input_context={"regime": regime_result.regime.value, "regime_conf": regime_result.confidence},
                raw_output=int(ppo_action),
                final_output=int(final_action),
                guardrails=[],
            )
        except Exception:
            pass
    
    return int(final_action), float(final_confidence), reasoning


class OnlineLearningManager:
    """
    Enhanced online learning manager that delegates to AdaptiveLearner
    when enhanced AI is enabled, or uses the standard fine-tuning approach.
    """

    def __init__(self, model: PPO, cfg: BotConfig, components: Optional[Dict[str, Any]] = None):
        self.model = model
        self.cfg = cfg
        self.components = components or {}
        self._bars_since = 0
        self._tune_count = 0
        
        # Use AdaptiveLearner when available
        self._adaptive = components.get('adaptive_learner') if components else None
        self._adaptive_mode = _enhanced_available and cfg.USE_ENHANCED_AI and self._adaptive is not None
        
        if self._adaptive_mode:
            log.info("🧠 Adaptive online learning enabled")

    def notify_new_bar(self, features: np.ndarray, prices: np.ndarray,
                        bar_df: Optional[pd.DataFrame] = None,
                        reward: float = 0.0) -> bool:
        """
        Process a new decision bar, with optional adaptive learning.
        
        Args:
            features: Feature matrix
            prices: Price array
            bar_df: Optional OHLCV DataFrame for regime classification
            reward: Optional reward signal
            
        Returns:
            True if an update was performed
        """
        self._bars_since += 1
        
        # Guard: Minimum data requirement
        if len(features) < self.cfg.MIN_BARS_FOR_FINETUNE:
            return False
        
        if self._adaptive_mode:
            # Use adaptive learning with regime awareness
            regime_classifier = self.components.get('regime_classifier')
            if regime_classifier is not None and bar_df is not None and len(bar_df) >= 50:
                regime = regime_classifier.classify(bar_df)
            else:
                from core.agent_enhanced import MarketRegime, RegimeResult
                regime = RegimeResult(
                    regime=MarketRegime.UNKNOWN, confidence=0.0,
                    trend_strength=0.0, volatility_percentile=50.0,
                    momentum=0.0, volume_regime="normal",
                    recommendation="No regime data",
                )
            
            should_update, reason = self._adaptive.should_update(regime, self._bars_since)
            if should_update:
                self._bars_since = 0
                success, msg = self._adaptive.update(features, prices, regime, reward)
                if success:
                    self._tune_count += 1
                return success
        
        # Standard (legacy) fine-tuning path
        if not self._adaptive_mode and self._bars_since >= self.cfg.FINE_TUNE_EVERY_BARS:
            self._bars_since = 0
            self._fine_tune(features, prices)
            return True
        
        return False

    def _fine_tune(self, features: np.ndarray, prices: np.ndarray):
        """Standard fine-tuning with Experience Replay Anchoring."""
        self._tune_count += 1
        log.info(f"Online fine-tune #{self._tune_count} | {len(features)} bars | {self.cfg.FINE_TUNE_STEPS:,} PPO steps")
        try:
            # ── Experience Replay Anchoring ───────────────────────────────
            # Blend recent live data with a diverse historical anchor buffer
            # to prevent catastrophic forgetting / policy shock on 30-bar windows.
            live_features = features
            live_prices = prices
            anchor_features = live_features
            anchor_prices = live_prices
            try:
                from core.experience_buffer import load_recent
                anchor_recs = load_recent(n=max(getattr(self.cfg, "FINE_TUNE_ANCHOR_SAMPLES", 256), 0))
                if anchor_recs:
                    af, ap = [], []
                    for rec in anchor_recs:
                        feats = rec.get("features")
                        if isinstance(feats, list) and len(feats) == getattr(self.cfg, "N_FEATURES", 18):
                            af.append(feats)
                            ap.append(float(rec.get("entry_price", 0.0) or 0.0))
                    if af:
                        anchor_features = np.vstack([live_features, np.array(af, dtype=np.float32)])
                        anchor_prices = np.concatenate([live_prices, np.array(ap, dtype=np.float32)])
                        log.info(f"Anchor replay: added {len(af)} historical records to fine-tune")
            except Exception as exc:
                log.debug(f"Anchor replay load failed: {exc}")

            env = TradingEnv(anchor_features, anchor_prices, self.cfg.INITIAL_CASH, self.cfg.TRANSACTION_COST_PCT,
                              self.cfg.WINDOW_SIZE, self.cfg.DEFAULT_MAX_POSITION_PCT)
            vec_env = DummyVecEnv([lambda: env])
            self.model.set_env(vec_env)
            self.model.learn(total_timesteps=self.cfg.FINE_TUNE_STEPS, reset_num_timesteps=False, progress_bar=False)
            self.model.save(self.cfg.MODEL_PATH)
            log.info(f"Fine-tune #{self._tune_count} saved -> {self.cfg.MODEL_PATH}")

            # Push model update to GitHub
            try:
                from core.git_sync import push_model_update
                push_model_update(self.cfg.MODEL_PATH)
            except Exception:
                pass

            # Record fine-tuning event in journal
            metrics = {
                "fine_tune_number": self._tune_count,
                "fine_tune_steps": self.cfg.FINE_TUNE_STEPS,
                "features_length": len(features)
            }
            try:
                from core.journal import record_training_session
                record_training_session(self.cfg, f"FINETUNE_{self._tune_count}", metrics, self.cfg.MODEL_PATH)
            except ImportError:
                pass
        except Exception as exc:
            log.error(f"Fine-tune #{self._tune_count} failed: {exc}")


# ── Convenience function to initialize all enhanced components ────────────

def initialize_enhanced_system(cfg: BotConfig, model: Optional[PPO] = None) -> Dict[str, Any]:
    """
    Initialize the complete enhanced AI system.
    
    Creates guardrails, regime classifier, ensemble, and adaptive learner.
    If model is provided, connects adaptive learner to it.
    
    Returns dict of components for use in predict_with_reasoning and OnlineLearningManager.
    """
    components = {}
    
    if not _enhanced_available:
        log.warning("Enhanced AI not available. Run: pip install -r requirements.txt")
        return components
    
    if not cfg.USE_ENHANCED_AI:
        return components
    
    # Always initialize guardrails (they're the safety layer)
    guardrails = GuardrailController(cfg, agent_version="3.5.0")
    guardrails.set_override_level(cfg.GUARDRAIL_OVERRIDE_LEVEL)
    components['guardrails'] = guardrails
    
    # Run health check
    healthy, report = guardrails.health_check()
    if not healthy:
        log.warning(f"Guardrail health check issues:\n{report}")
    
    # Regime classifier
    if cfg.USE_REGIME_CLASSIFIER:
        components['regime_classifier'] = MarketRegimeClassifier()
    
    # Ensemble
    if cfg.USE_ENSEMBLE:
        components['ensemble'] = EnsembleTrader(cfg)
    
    # Confidence scorer
    components['confidence_scorer'] = ConfidenceScorer(cfg)
    
    # Adaptive learner (requires model)
    if model is not None:
        components['adaptive_learner'] = AdaptiveLearner(model, cfg)
    
# Ollama local LLM reasoning head (lazy, best-effort)
    if getattr(cfg, 'OLLAMA_ENABLED', False):
        try:
            from core.ollama_brain import create_ollama_brain
            components['ollama_brain'] = create_ollama_brain(cfg)
        except Exception as exc:
            log.debug(f"Ollama brain init skipped: {exc}")

    log.info(f"✅ Enhanced AI system initialized: "
              f"guardrails={cfg.USE_GUARDRAILS}, "
              f"regime={cfg.USE_REGIME_CLASSIFIER}, "
              f"ensemble={cfg.USE_ENSEMBLE}, "
              f"ollama={getattr(cfg, 'OLLAMA_ENABLED', False)}")

    # Integrate pilot experience system
    components['pilot'] = PilotExperienceSystem(cfg)
    components['patterns'] = PatternMemoryBank(cfg)

    return components
