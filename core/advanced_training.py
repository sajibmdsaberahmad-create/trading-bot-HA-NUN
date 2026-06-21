#!/usr/bin/env python3
"""
core/advanced_training.py — Advanced Multi-Model Training Pipeline.

This is the MASTER TRAINER that orchestrates training of all models:
1. PPO (Stable-Baselines3)
2. Transformer (PyTorch, custom)
3. LSTM (TensorFlow/Keras)

TRAINING STRATEGY
═══════════════════════════════════════════════════════════════════════════
Phase 1: Data Preparation
  - Fetch historical data from IB or generate synthetic
  - Compute enhanced features (18 features)
  - Create training/validation/test splits
  - Prepare sequential datasets for each model type

Phase 2: Supervised Pre-training (Transformer & LSTM)
  - Train on labeled historical data (future price movement)
  - Cross-validation to prevent overfitting
  - Early stopping with patience
  - Model checkpointing

Phase 3: Reinforcement Learning (PPO)
  - Train PPO on historical environment
  - Parallel environment for faster training
  - Curriculum learning (easy -> hard regimes)

Phase 4: Ensemble Calibration
  - Evaluate all models on validation set
  - Calibrate fusion weights
  - Run backtest with fused system

Phase 5: Integration
  - Save all trained models
  - Create fusion engine with loaded models
  - Test complete pipeline
"""

import os
import sys
import json
import time
import math
import random
import numpy as np
import pandas as pd
from typing import Optional, Tuple, Dict, List, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

import torch

from core.config import BotConfig
from core.features_enhanced import FeatureEngineerEnhanced
from core.notify import log
from core.stationary_features import (
    compute_microstructure_features, get_feature_columns,
    validate_stationarity
)
import requests

# ── Conditional imports ────────────────────────────────────────────────

# PPO
try:
    import gymnasium as gym
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
    PPO_AVAILABLE = True
except ImportError:
    PPO_AVAILABLE = False

# Transformer
TRANSFORMER_AVAILABLE = False
try:
    from core.transformer_model import (
        TemporalFusionTransformer, TransformerConfig, create_transformer,
        TransformerTrainer
    )
    TRANSFORMER_AVAILABLE = True
except ImportError:
    pass

# LSTM
LSTM_AVAILABLE = False
try:
    from core.lstm_model import (
        BuildLSTMModel, LSTMConfig, create_lstm, LSTMTrainer,
        prepare_lstm_dataset
    )
    import tensorflow as tf
    LSTM_AVAILABLE = True
except ImportError:
    pass

# Fusion
try:
    from core.multi_model_fusion import (
        MultiModelFusionEngine, create_fusion_engine
    )
    FUSION_AVAILABLE = True
except ImportError:
    FUSION_AVAILABLE = False


@dataclass
class TrainingConfig:
    """Configuration for the multi-model training pipeline."""
    
    # Data
    ticker: str = "SPY"
    train_start: str = "2020-01-01"
    train_end: str = "2024-06-01"
    val_start: str = "2024-06-01"
    val_end: str = "2024-12-01"
    test_start: str = "2025-01-01"
    test_end: str = "2025-06-01"
    bar_size: str = "1 min"
    
    # Feature engineering
    n_features: int = 18
    
    # Training
    batch_size: int = 64
    epochs: int = 50
    early_stopping_patience: int = 10
    validation_split: float = 0.2
    
    # PPO
    ppo_timesteps: int = 500_000
    ppo_learning_rate: float = 2.5e-4
    
    # Transformer
    transformer_learning_rate: float = 1e-4
    transformer_d_model: int = 256
    transformer_nhead: int = 8
    transformer_layers: int = 4
    
    # LSTM
    lstm_units: int = 128
    lstm_layers: int = 3
    lstm_learning_rate: float = 1e-3
    
    # Saving
    ppo_save_path: str = "ppo_trader.zip"
    transformer_save_path: str = "models/transformer_model.pth"
    lstm_save_path: str = "models/lstm_model.h5"
    fusion_save_path: str = "models/fusion_state.json"
    
    # Backtest after training
    run_backtest: bool = True
    backtest_start: str = "2025-01-01"
    backtest_end: str = "2025-06-01"
    
    # Device
    device: str = "auto"
    
    # Verbose
    verbose: bool = True


class AdvancedTrainingPipeline:
    """
    Master training pipeline for all AI models.
    
    Orchestrates the complete training process from data preparation
    through model training, fusion, and validation.
    """
    
    def __init__(self, config: TrainingConfig):
        self.config = config
        self.bot_cfg = BotConfig()
        
        # Data
        self.train_data: Optional[pd.DataFrame] = None
        self.val_data: Optional[pd.DataFrame] = None
        self.test_data: Optional[pd.DataFrame] = None
        
        self.train_features: Optional[np.ndarray] = None
        self.val_features: Optional[np.ndarray] = None
        self.test_features: Optional[np.ndarray] = None
        
        self.train_prices: Optional[np.ndarray] = None
        self.val_prices: Optional[np.ndarray] = None
        self.test_prices: Optional[np.ndarray] = None
        
        # Models
        self.ppo_model: Optional[PPO] = None
        self.transformer_model: Optional[TemporalFusionTransformer] = None
        self.transformer_trainer: Optional[TransformerTrainer] = None
        self.lstm_model = None
        self.lstm_trainer = None
        
        # Fusion engine
        self.fusion_engine: Optional[MultiModelFusionEngine] = None
        
        # Metrics
        self.training_history: Dict[str, Any] = {
            'start_time': datetime.utcnow().isoformat(),
            'models_trained': [],
            'metrics': {},
        }
        
        # Ensure directories
        os.makedirs('models/checkpoints', exist_ok=True)
        os.makedirs('models/backups', exist_ok=True)
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: DATA PREPARATION
    # ══════════════════════════════════════════════════════════════════════
    
    def prepare_data(self, use_synthetic: bool = True, use_stationary_features: bool = False):
        """
        Prepare all datasets for training.
        
        Args:
            use_synthetic: If True, generate synthetic data when IB unavailable
            use_stationary_features: If True, replace raw features with
                fractional-diff / VPIN / Amihud stationary geometries
        """
        log.info("=" * 70)
        log.info("  PHASE 1: DATA PREPARATION")
        log.info("=" * 70)
        
        # Try to fetch from IB first
        data = None
        if not use_synthetic:
            data = self._fetch_data(
                self.config.train_start, self.config.test_end
            )
        
        if data is None:
            data = self._generate_synthetic_data(
                self.config.train_start, self.config.test_end
            )
        
        if data is None or len(data) < 200:
            log.error("Insufficient data for training")
            return False
        
        log.info(f"Total data: {len(data)} bars from {data.index[0]} to {data.index[-1]}")
        
        # Split into train/val/test (with purge/embargo if enabled)
        if self.bot_cfg.PURGE_EMBARGO_ENABLED:
            self.train_data, self.val_data, self.test_data = self._purged_embargoed_split(
                data, self.bot_cfg.PURGE_BARS, self.bot_cfg.EMBARGO_BARS
            )
        else:
            train_mask = (data.index >= self.config.train_start) & (data.index < self.config.val_start)
            val_mask = (data.index >= self.config.val_start) & (data.index < self.config.test_start)
            test_mask = (data.index >= self.config.test_start) & (data.index <= self.config.test_end)
            self.train_data = data[train_mask].copy()
            self.val_data = data[val_mask].copy()
            self.test_data = data[test_mask].copy()
        
        log.info(f"Train: {len(self.train_data)} bars | Val: {len(self.val_data)} bars | Test: {len(self.test_data)} bars")
        
        # Compute features
        if len(self.train_data) >= 100:
            if use_stationary_features and self.bot_cfg.USE_FRACTIONAL_DIFF:
                self.train_data = compute_microstructure_features(
                    self.train_data,
                    frac_diff_order=self.bot_cfg.FRACTIONAL_DIFF_D,
                    vpin_window=self.bot_cfg.VPIN_WINDOW,
                    amihud_window=self.bot_cfg.AMIHUD_WINDOW,
                )
                feat_cols = get_feature_columns()
                self.train_features = self.train_data[feat_cols].values
                self.train_features = np.nan_to_num(self.train_features, nan=0.0, posinf=0.0, neginf=0.0)
                log.info(f"Train features (stationary): {self.train_features.shape}")
            else:
                self.train_features = FeatureEngineerEnhanced.compute(self.train_data)
            self.train_prices = self.train_data['close'].values[-len(self.train_features):]
        
        if len(self.val_data) >= 100:
            if use_stationary_features and self.bot_cfg.USE_FRACTIONAL_DIFF:
                self.val_data = compute_microstructure_features(
                    self.val_data,
                    frac_diff_order=self.bot_cfg.FRACTIONAL_DIFF_D,
                    vpin_window=self.bot_cfg.VPIN_WINDOW,
                    amihud_window=self.bot_cfg.AMIHUD_WINDOW,
                )
                feat_cols = get_feature_columns()
                self.val_features = self.val_data[feat_cols].values
                self.val_features = np.nan_to_num(self.val_features, nan=0.0, posinf=0.0, neginf=0.0)
            else:
                self.val_features = FeatureEngineerEnhanced.compute(self.val_data)
            self.val_prices = self.val_data['close'].values[-len(self.val_features):]
        
        if len(self.test_data) >= 100:
            if use_stationary_features and self.bot_cfg.USE_FRACTIONAL_DIFF:
                self.test_data = compute_microstructure_features(
                    self.test_data,
                    frac_diff_order=self.bot_cfg.FRACTIONAL_DIFF_D,
                    vpin_window=self.bot_cfg.VPIN_WINDOW,
                    amihud_window=self.bot_cfg.AMIHUD_WINDOW,
                )
                feat_cols = get_feature_columns()
                self.test_features = self.test_data[feat_cols].values
                self.test_features = np.nan_to_num(self.test_features, nan=0.0, posinf=0.0, neginf=0.0)
            else:
                self.test_features = FeatureEngineerEnhanced.compute(self.test_data)
            self.test_prices = self.test_data['close'].values[-len(self.test_features):]
        
        self.training_history['data'] = {
            'total_bars': len(data),
            'train_bars': len(self.train_data),
            'val_bars': len(self.val_data),
            'test_bars': len(self.test_data),
            'features_shape': self.train_features.shape if self.train_features is not None else (0, 0),
            'stationary_features': use_stationary_features and self.bot_cfg.USE_FRACTIONAL_DIFF,
        }
        
        return True
    
    def _fetch_data(self, start: str, end: str) -> Optional[pd.DataFrame]:
        """Fetch historical data."""
        try:
            from core.connector import IBConnector
            from core.data import DataManager
            
            conn = IBConnector(self.bot_cfg)
            if not conn.connect():
                log.warning("Cannot connect to IB")
                return None
            
            dm = DataManager(conn, self.bot_cfg)
            
            # Fetch daily data for the full period
            df = dm.fetch_historical(
                duration="5 Y",
                bar_size="1 day",
            )
            
            conn.disconnect()
            return df
            
        except Exception as exc:
            log.warning(f"Data fetch failed: {exc}")
            return None
    
    def _purged_embargoed_split(self, data: pd.DataFrame, purge_bars: int, embargo_bars: int):
        """
        Produce train/val/test splits with purging and embargo periods to
        prevent look-ahead leakage in time-series cross-validation.
        
        Purge: Remove training labels that overlap with validation labels.
        Embargo: Remove training samples immediately after validation ends.
        """
        n = len(data)
        
        # Determine cut points
        train_end = int(n * self.bot_cfg.TRAIN_VAL_SPLIT)
        val_end = int(n * (self.bot_cfg.TRAIN_VAL_SPLIT + (1 - self.bot_cfg.TRAIN_VAL_SPLIT) / 2))
        
        # Apply purge forward and embargo backward
        train_start = 0
        train_end_adj = train_end - purge_bars
        val_start = train_end + purge_bars + embargo_bars
        val_end_adj = val_end - purge_bars - embargo_bars
        test_start = val_end + purge_bars + embargo_bars
        test_end = n
        
        train_data = data.iloc[train_start:train_end_adj].copy()
        val_data = data.iloc[val_start:val_end_adj].copy()
        test_data = data.iloc[test_start:test_end].copy()
        
        log.info(f"Purged/Embargoed split: train={len(train_data)}, "
                 f"val={len(val_data)}, test={len(test_data)} "
                 f"(purge={purge_bars}, embargo={embargo_bars})")
        
        return train_data, val_data, test_data

    def _sample_regime_bootstrap(self, data: pd.DataFrame, n_samples: int,
                                  regime: str = "high_volatility") -> pd.DataFrame:
        """
        Regime-aware bootstrapping: select historical chunks matching the
        target regime (e.g. High-Vol Breakout) rather than uniform random.
        
        This targets the co-adaptation collapse failure mode by ensuring
        the student sees diverse market environments during distillation.
        
        Args:
            data: Full historical DataFrame
            n_samples: Number of rows to sample
            regime: Target regime label
            
        Returns:
            Bootstrapped DataFrame slice
        """
        if len(data) < n_samples:
            return data.copy()
        
        # Compute regime labels per chunk
        if 'volatility_regime' not in data.columns:
            return data.sample(n=n_samples, replace=False)
        
        vol_z = data['volatility_regime'].fillna(0).values
        price_changes = data['close'].pct_change().fillna(0).abs().values
        
        if regime == "high_volatility":
            mask = (np.abs(vol_z) > 1.0) | (price_changes > 0.02)
        elif regime == "low_volatility":
            mask = (np.abs(vol_z) < 0.3) & (price_changes < 0.005)
        elif regime == "trending":
            # Use cached rolling momentum if available, else simple heuristic
            close = data['close'].values
            mom = np.array([close[i] / close[i-20] - 1 if i >= 20 else 0
                            for i in range(len(close))])
            mask = np.abs(mom) > 0.01
        else:
            mask = np.ones(len(data), dtype=bool)
        
        candidates = data[mask]
        if len(candidates) < n_samples // 2:
            # Fallback: random sample with volatility weighting
            weights = 1 + np.abs(vol_z)
            weights = weights / weights.sum()
            idx = np.random.choice(len(data), size=n_samples, replace=False, p=weights)
            return data.iloc[idx].copy()
        
        # Sample without replacement from regime-matched subset
        sampled = candidates.sample(n=min(n_samples, len(candidates)), replace=False)
        return sampled

    def _generate_synthetic_data(self, start: str, end: str) -> pd.DataFrame:
        """Generate synthetic market data."""
        log.info("Generating synthetic market data...")
        
        np.random.seed(42)
        
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        
        # Generate daily data
        timestamps = []
        current = start_dt
        while current <= end_dt:
            if current.weekday() < 5:  # Weekdays only
                timestamps.append(current)
            current += timedelta(days=1)
        
        if not timestamps:
            return pd.DataFrame()
        
        n = len(timestamps)
        
        # Generate realistic price series with different regimes
        price = 100.0
        prices = []
        
        # Market regimes: trending, ranging, volatile, calm
        regime_length = n // 4
        
        for regime_idx in range(4):
            regime_start = regime_idx * regime_length
            regime_end = min((regime_idx + 1) * regime_length, n)
            regime_n = regime_end - regime_start
            
            if regime_idx == 0:  # Trending up
                drift = 0.002
                vol = 0.008
            elif regime_idx == 1:  # Ranging
                drift = 0.0001
                vol = 0.005
            elif regime_idx == 2:  # Volatile
                drift = 0.0
                vol = 0.025
            else:  # Calm with slight downtrend
                drift = -0.0005
                vol = 0.003
            
            for i in range(regime_n):
                noise = np.random.randn() * vol
                price *= (1 + drift + noise)
                price = max(price, 10.0)
                prices.append(price)
        
        # Build DataFrame with OHLCV — ensure all arrays have exactly the same length
        m = len(prices)
        df = pd.DataFrame({
            'open': [prices[i] * (1 - 0.002 * np.random.rand()) for i in range(m)],
            'high': [prices[i] * (1 + 0.003 * np.random.rand()) for i in range(m)],
            'low': [prices[i] * (1 - 0.003 * np.random.rand()) for i in range(m)],
            'close': prices[:m],
            'volume': [int(5_000_000 * (0.3 + np.random.rand())) for _ in range(m)],
        }, index=timestamps[:m])
        
        df.index.name = 'date'
        
        log.info(f"Synthetic data: {len(df)} bars, "
                 f"${df['low'].min():.2f} - ${df['high'].max():.2f}, "
                 f"regimes: trending/ranging/volatile/calm")
        
        return df
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: PPO TRAINING
    # ══════════════════════════════════════════════════════════════════════
    
    def train_ppo(self):
        """Train PPO model using reinforcement learning."""
        log.info("=" * 70)
        log.info("  PHASE 2A: PPO REINFORCEMENT LEARNING")
        log.info("=" * 70)
        
        if not PPO_AVAILABLE:
            log.warning("PPO not available. Install stable-baselines3")
            return False
        
        if self.train_features is None or len(self.train_features) < 100:
            log.error("Insufficient training data")
            return False
        
        from core.env import TradingEnv
        
        log.info(f"Training PPO for {self.config.ppo_timesteps:,} timesteps...")
        
        try:
            # Create environment
            env = TradingEnv(
                self.train_features, self.train_prices,
                self.bot_cfg.INITIAL_CASH,
                self.bot_cfg.TRANSACTION_COST_PCT,
                self.bot_cfg.WINDOW_SIZE,
                self.bot_cfg.DEFAULT_MAX_POSITION_PCT,
            )
            vec_env = DummyVecEnv([lambda: env])
            
            # Detect device
            device = "auto"
            if self.config.device == "auto":
                if torch.cuda.is_available():
                    device = "cuda"
                    log.info("Using CUDA GPU for PPO training")
                elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                    device = "mps"
                    log.info("Using Apple MPS for PPO training")
                else:
                    log.info("Using CPU for PPO training")
            
            # Build PPO with deep architecture
            policy_kwargs = {
                "net_arch": [1024, 512, 256],
                "activation_fn": torch.nn.Tanh,
            }
            
            self.ppo_model = PPO(
                policy="MlpPolicy",
                env=vec_env,
                n_steps=2048,
                batch_size=256,
                n_epochs=15,
                clip_range=0.15,
                max_grad_norm=0.5,
                learning_rate=self.config.ppo_learning_rate,
                gamma=0.99,
                gae_lambda=0.95,
                ent_coef=0.01,
                vf_coef=0.5,
                policy_kwargs=policy_kwargs,
                verbose=1,
                device=device,
            )
            
            # Train
            start_time = time.time()
            self.ppo_model.learn(
                total_timesteps=self.config.ppo_timesteps,
                reset_num_timesteps=True,
                progress_bar=True,
            )
            elapsed = time.time() - start_time
            
            # Save
            self.ppo_model.save(self.config.ppo_save_path)
            log.info(f"PPO trained in {elapsed:.0f}s -> {self.config.ppo_save_path}")
            
            self.training_history['models_trained'].append('ppo')
            self.training_history['metrics']['ppo'] = {
                'timesteps': self.config.ppo_timesteps,
                'training_time_s': round(elapsed, 1),
            }
            
            return True
            
        except Exception as exc:
            log.error(f"PPO training failed: {exc}")
            import traceback
            traceback.print_exc()
            return False
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: TRANSFORMER TRAINING
    # ══════════════════════════════════════════════════════════════════════
    
    def train_transformer(self):
        """Train Transformer model using supervised learning."""
        log.info("=" * 70)
        log.info("  PHASE 2B: TRANSFORMER SUPERVISED LEARNING")
        log.info("=" * 70)
        
        if not TRANSFORMER_AVAILABLE:
            log.warning("Transformer not available")
            return False
        
        if self.train_features is None or len(self.train_features) < 200:
            log.error("Insufficient training data")
            return False
        
        log.info("Preparing Transformer dataset...")
        
        try:
            # Prepare supervised dataset
            seq_len = self.bot_cfg.WINDOW_SIZE
            n_features = self.train_features.shape[1]
            
            # Apply regime bootstrapping if enabled
            if self.bot_cfg.REGIME_BOOTSTRAP_ENABLED:
                boot_df = self._sample_regime_bootstrap(
                    pd.DataFrame(self.train_features, columns=[f'f{i}' for i in range(n_features)]),
                    n_samples=self.bot_cfg.BOOTSTRAP_SAMPLES,
                    regime="high_volatility",
                )
                X_train, y_train_actions, y_train_values = self._prepare_sequence_dataset(
                    boot_df.values, self.train_prices[-len(boot_df):], seq_len
                )
            else:
                X_train, y_train_actions, y_train_values = self._prepare_sequence_dataset(
                    self.train_features, self.train_prices, seq_len
                )
            
            if len(X_train) < 100:
                log.error(f"Too few training sequences: {len(X_train)}")
                return False
            
            # Create config
            tf_config = TransformerConfig(
                input_dim=n_features,
                d_model=self.config.transformer_d_model,
                nhead=self.config.transformer_nhead,
                num_layers=self.config.transformer_layers,
                dim_feedforward=self.config.transformer_d_model * 2,
                dropout=0.1,
                max_seq_len=seq_len,
                num_actions=3,
                learning_rate=self.config.transformer_learning_rate,
                epochs=self.config.epochs,
                device=self.config.device,
            )
            
            # Log trajectory for later meta-optimization
            self.training_history['data']['transformer_input_dim'] = n_features
            
            # Create model
            model, trainer = create_transformer(tf_config)
            self.transformer_model = model
            self.transformer_trainer = trainer
            
            log.info(f"Transformer architecture: {model}")
            log.info(f"Training samples: {len(X_train)}")
            
            # Training loop
            n_train = len(X_train)
            n_val = int(n_train * 0.2)
            
            # Simple training loop
            model.train()
            best_loss = float('inf')
            patience_counter = 0
            
            for epoch in range(self.config.epochs):
                # Shuffle
                indices = np.random.permutation(n_train)
                epoch_loss = 0.0
                n_batches = 0
                
                for start_idx in range(0, n_train - n_val, self.config.batch_size):
                    batch_indices = indices[start_idx:min(start_idx + self.config.batch_size, n_train - n_val)]
                    
                    batch_obs = torch.FloatTensor(X_train[batch_indices]).to(model.device)
                    batch_actions = torch.LongTensor(y_train_actions[batch_indices]).to(model.device)
                    batch_values = torch.FloatTensor(y_train_values[batch_indices]).to(model.device)
                    
                    trainer.optimizer.zero_grad()
                    
                    action_logits, value_pred = model(batch_obs)
                    
                    loss_action = trainer.criterion_action(action_logits, batch_actions)
                    loss_value = trainer.criterion_value(value_pred.squeeze(), batch_values)
                    loss = loss_action + 0.5 * loss_value
                    
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
                    trainer.optimizer.step()
                    
                    epoch_loss += loss.item()
                    n_batches += 1
                
                # Validation
                val_indices = indices[n_train - n_val:]
                if len(val_indices) > 0:
                    val_obs = torch.FloatTensor(X_train[val_indices]).to(model.device)
                    val_actions = torch.LongTensor(y_train_actions[val_indices]).to(model.device)
                    val_values = torch.FloatTensor(y_train_values[val_indices]).to(model.device)
                    
                    with torch.no_grad():
                        val_logits, val_preds = model(val_obs)
                        val_loss = trainer.criterion_action(val_logits, val_actions).item()
                else:
                    val_loss = epoch_loss / max(n_batches, 1)
                
                avg_loss = epoch_loss / max(n_batches, 1)
                
                if epoch % 5 == 0:
                    log.info(f"Epoch {epoch:3d}: train_loss={avg_loss:.4f}, val_loss={val_loss:.4f}")
                
                # Early stopping
                if val_loss < best_loss:
                    best_loss = val_loss
                    patience_counter = 0
                    # Save best model
                    trainer.save(self.config.transformer_save_path.replace('.pth', '_best.pth'))
                else:
                    patience_counter += 1
                    if patience_counter >= self.config.early_stopping_patience:
                        log.info(f"Early stopping at epoch {epoch}")
                        break
                
                trainer.scheduler.step()
            
            # Load best model
            if os.path.exists(self.config.transformer_save_path.replace('.pth', '_best.pth')):
                trainer.load(self.config.transformer_save_path.replace('.pth', '_best.pth'))
                # Save as final
                trainer.save(self.config.transformer_save_path)
                log.info(f"Transformer trained -> {self.config.transformer_save_path}")
            
            self.training_history['models_trained'].append('transformer')
            self.training_history['metrics']['transformer'] = {
                'epochs': epoch + 1,
                'best_val_loss': round(best_loss, 4),
                'samples': len(X_train),
            }
            
            return True
            
        except Exception as exc:
            log.error(f"Transformer training failed: {exc}")
            import traceback
            traceback.print_exc()
            return False
    
    def _prepare_sequence_dataset(self, features: np.ndarray, prices: np.ndarray,
                                   seq_length: int = 60, lookahead: int = 1) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Prepare sequential dataset for supervised training."""
        n_samples = len(features)
        n_features = features.shape[1]
        
        X, actions, values = [], [], []
        
        for i in range(seq_length, n_samples - lookahead):
            seq = features[i - seq_length:i]
            X.append(seq)
            
            current_price = prices[i - 1]
            future_price = prices[i + lookahead - 1]
            
            pct_change = (future_price / current_price - 1) * 100
            
            if pct_change > 0.3:
                action = 1  # BUY
            elif pct_change < -0.3:
                action = 2  # SELL
            else:
                action = 0  # HOLD
            
            actions.append(action)
            values.append(pct_change / 100.0)
        
        return (
            np.array(X, dtype=np.float32),
            np.array(actions, dtype=np.int64),
            np.array(values, dtype=np.float32).reshape(-1, 1),
        )
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4: LSTM TRAINING
    # ══════════════════════════════════════════════════════════════════════
    
    def train_lstm(self):
        """Train LSTM model using supervised learning."""
        log.info("=" * 70)
        log.info("  PHASE 2C: LSTM SUPERVISED LEARNING")
        log.info("=" * 70)
        
        if not LSTM_AVAILABLE:
            log.warning("LSTM not available. Install tensorflow")
            return False
        
        if self.train_features is None or len(self.train_features) < 200:
            log.error("Insufficient training data")
            return False
        
        try:
            # Prepare dataset with optional regime bootstrapping
            seq_len = self.bot_cfg.WINDOW_SIZE
            if self.bot_cfg.REGIME_BOOTSTRAP_ENABLED:
                boot_df = self._sample_regime_bootstrap(
                    pd.DataFrame(self.train_features, columns=[f'f{i}' for i in range(self.train_features.shape[1])]),
                    n_samples=self.bot_cfg.BOOTSTRAP_SAMPLES,
                    regime="trending",
                )
                X_train, y_actions, y_values = prepare_lstm_dataset(
                    boot_df.values, self.train_prices[-len(boot_df):], seq_len, lookahead=1
                )
            else:
                X_train, y_actions, y_values = prepare_lstm_dataset(
                    self.train_features, self.train_prices, seq_len, lookahead=1
                )
            
            if len(X_train) < 100:
                log.error(f"Too few training sequences: {len(X_train)}")
                return False
            
            # Create config
            config = LSTMConfig(
                input_dim=self.config.n_features,
                seq_length=seq_len,
                lstm_units=self.config.lstm_units,
                lstm_layers=self.config.lstm_layers,
                bidirectional=True,
                dropout=0.2,
                use_attention=True,
                learning_rate=self.config.lstm_learning_rate,
                epochs=min(self.config.epochs, 30),  # LSTM trains faster
                batch_size=self.config.batch_size,
            )
            
            # Create model
            model, trainer = create_lstm(config)
            self.lstm_model = model
            self.lstm_trainer = trainer
            
            # Build model by passing a dummy batch to avoid Keras build-before-count error
            try:
                dummy_x = tf.zeros((1, config.seq_length, config.input_dim))
                _ = model(dummy_x, training=False)
                param_count = model.count_params()
                log.info(f"LSTM architecture: {param_count:,} parameters")
            except Exception as exc:
                log.debug(f"LSTM param count skipped: {exc}")
            log.info(f"Training samples: {len(X_train)}")
            
            # Build tf.data dataset
            n_val = int(len(X_train) * 0.2)
            X_val, y_val_actions, y_val_values = X_train[:n_val], y_actions[:n_val], y_values[:n_val]
            X_train_split, y_train_actions, y_train_values = X_train[n_val:], y_actions[n_val:], y_values[n_val:]
            
            train_dataset = tf.data.Dataset.from_tensor_slices((
                X_train_split,
                y_train_actions,
                y_train_values,
            )).batch(config.batch_size).prefetch(tf.data.AUTOTUNE)
            
            val_dataset = tf.data.Dataset.from_tensor_slices((
                X_val, y_val_actions, y_val_values,
            )).batch(config.batch_size).prefetch(tf.data.AUTOTUNE)
            
            # Train
            history = trainer.fit(
                train_dataset,
                validation_dataset=val_dataset,
                epochs=config.epochs,
            )
            
            # Save
            trainer.save(self.config.lstm_save_path)
            log.info(f"LSTM trained -> {self.config.lstm_save_path}")
            
            self.training_history['models_trained'].append('lstm')
            self.training_history['metrics']['lstm'] = {
                'epochs': len(history['loss']),
                'final_loss': round(history['loss'][-1], 4),
                'samples': len(X_train),
            }
            
            return True
            
        except Exception as exc:
            log.error(f"LSTM training failed: {exc}")
            import traceback
            traceback.print_exc()
            return False
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 5: FUSION & BACKTEST
    # ══════════════════════════════════════════════════════════════════════
    
    def setup_fusion_engine(self):
        """Create fusion engine with all trained models."""
        log.info("=" * 70)
        log.info("  PHASE 3: FUSION ENGINE SETUP")
        log.info("=" * 70)
        
        if not FUSION_AVAILABLE:
            log.warning("Fusion engine not available")
            return None
        
        from core.agent_enhanced import EnsembleTrader
        
        engine = MultiModelFusionEngine(self.bot_cfg)
        
        if self.ppo_model is not None:
            engine.register_ppo(self.ppo_model)
        
        if self.transformer_model is not None:
            engine.register_transformer(
                self.transformer_model,
                TransformerConfig(input_dim=self.config.n_features)
            )
        
        if self.lstm_model is not None:
            engine.register_lstm(
                self.lstm_model,
                LSTMConfig(input_dim=self.config.n_features)
            )
        
        # Ensemble always available
        ensemble = EnsembleTrader(self.bot_cfg)
        engine.register_ensemble(ensemble)
        
        self.fusion_engine = engine
        log.info(f"✅ Fusion engine ready with {len(engine.accuracy_tracker._history)} models")
        
        return engine
    
    def run_backtest(self):
        """Run backtest with the trained fusion system."""
        log.info("=" * 70)
        log.info("  PHASE 4: BACKTEST VALIDATION")
        log.info("=" * 70)
        
        if self.fusion_engine is None:
            log.warning("Fusion engine not initialized, setting up...")
            self.setup_fusion_engine()
        
        if self.test_features is None or len(self.test_features) < 100:
            log.warning("No test data available for backtest")
            return None
        
        log.info(f"Running backtest on {len(self.test_features)} bars...")
        
        # Simple backtest using the fusion engine
        cash = float(self.bot_cfg.INITIAL_CASH)
        shares = 0.0
        entry_price = 0.0
        trade_pnls = []
        nav_history = [cash]
        
        window_size = self.bot_cfg.WINDOW_SIZE
        n_features = self.config.n_features
        
        for i in range(window_size, len(self.test_features)):
            # Build observation
            window = self.test_features[i - window_size:i].flatten()
            cash_ratio = cash / (cash + 0.01)  # Avoid div by zero
            pos_ratio = 0.0
            obs = np.concatenate([window, [cash_ratio, pos_ratio]]).astype(np.float32)
            
            # Get decision
            decision = self.fusion_engine.get_decision(obs)
            
            # Execute (simplified)
            current_price = self.test_prices[i]
            nav = cash + shares * current_price
            nav_history.append(nav)
            
            if decision.action == 1 and shares == 0:  # BUY
                max_shares = int(cash * 0.95 / current_price)
                if max_shares >= 1:
                    shares = float(max_shares)
                    cash -= shares * current_price
                    entry_price = current_price
                    
            elif decision.action == 2 and shares > 0:  # SELL
                pnl = (current_price - entry_price) * shares
                trade_pnls.append(pnl)
                cash += shares * current_price
                shares = 0.0
                
                # Record outcome
                self.fusion_engine.record_outcome(decision, (current_price / entry_price - 1) * 100)
        
        # Close any remaining position
        if shares > 0:
            final_price = self.test_prices[-1]
            pnl = (final_price - entry_price) * shares
            trade_pnls.append(pnl)
            cash += shares * final_price
            shares = 0.0
        
        final_nav = cash
        total_return = (final_nav / self.bot_cfg.INITIAL_CASH - 1) * 100
        
        # Metrics
        nav_arr = np.array(nav_history)
        peak = np.maximum.accumulate(nav_arr)
        dd = (peak - nav_arr) / (peak + 1e-9)
        max_dd = dd.max() * 100
        
        wins = sum(1 for p in trade_pnls if p > 0)
        losses = sum(1 for p in trade_pnls if p < 0)
        total_trades = len(trade_pnls)
        win_rate = wins / max(total_trades, 1) * 100
        
        avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
        avg_loss = np.mean([p for p in trade_pnls if p < 0]) if losses > 0 else 0
        
        # Sharpe
        trade_returns = [p / self.bot_cfg.INITIAL_CASH for p in trade_pnls]
        sharpe = 0.0
        if len(trade_returns) >= 5 and np.std(trade_returns) > 0:
            sharpe = float(np.mean(trade_returns) / np.std(trade_returns) * np.sqrt(252))
        
        results = {
            'initial_nav': round(self.bot_cfg.INITIAL_CASH, 2),
            'final_nav': round(final_nav, 2),
            'total_return_pct': round(total_return, 2),
            'total_pnl': round(final_nav - self.bot_cfg.INITIAL_CASH, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'sharpe_ratio': round(sharpe, 3),
            'trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate_pct': round(win_rate, 1),
            'avg_win': round(avg_win, 2) if avg_win else 0,
            'avg_loss': round(avg_loss, 2) if avg_loss else 0,
            'model_accuracy': self.fusion_engine.accuracy_tracker.get_summary(),
        }
        
        log.info(f"Backtest Results:")
        log.info(f"  Return: {results['total_return_pct']:+.2f}% | DD: {results['max_drawdown_pct']:.2f}% | Sharpe: {results['sharpe_ratio']:.3f}")
        log.info(f"  Trades: {results['trades']} ({results['win_rate_pct']:.0f}% WR)")
        log.info(f"  Models: {results['model_accuracy']}")
        
        self.training_history['metrics']['backtest'] = results
        
        return results
    
    # ══════════════════════════════════════════════════════════════════════
    # RUN ALL
    # ══════════════════════════════════════════════════════════════════════
    
    def run_all(self):
        """
        Run the complete training pipeline.
        
        Returns:
            Dict with training results and metrics
        """
        log.info("=" * 70)
        log.info("  🧠 ADVANCED MULTI-MODEL TRAINING PIPELINE")
        log.info("=" * 70)
        log.info(f"  Model Zoo: PPO + Transformer + LSTM + Ensemble")
        log.info(f"  Data: {self.config.train_start} → {self.config.test_end}")
        log.info(f"  Device: {self.config.device}")
        log.info("=" * 70)
        
        overall_start = time.time()
        
        # Phase 1: Data
        if not self.prepare_data():
            return self.training_history
        
        # Phase 2a: PPO
        self.train_ppo()
        
        # Phase 2b: Transformer
        self.train_transformer()
        
        # Phase 2c: LSTM
        self.train_lstm()
        
        # Phase 3: Fusion
        self.setup_fusion_engine()
        
        # Phase 4: Backtest
        if self.config.run_backtest:
            self.run_backtest()
        
        # Save training history
        self.training_history['total_time_s'] = round(time.time() - overall_start, 1)
        self.training_history['end_time'] = datetime.utcnow().isoformat()
        
        # Save to file
        history_path = f"training_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(history_path, 'w') as f:
            json.dump(self.training_history, f, indent=2, default=str)
        
        log.info("=" * 70)
        log.info("  ✅ TRAINING COMPLETE")
        log.info(f"  Total time: {self.training_history['total_time_s']:.0f}s")
        log.info(f"  Models trained: {', '.join(self.training_history['models_trained'])}")
        log.info(f"  History saved: {history_path}")
        log.info("=" * 70)
        
        # Preserve artifacts outside of Git
        try:
            from core.model_preservation import preserve_all
            model_paths = []
            for c in [self.config.ppo_save_path, self.config.transformer_save_path,
                      self.config.lstm_save_path, self.config.fusion_save_path]:
                if c and os.path.isfile(c):
                    model_paths.append(c)
            if model_paths:
                preserve_all(
                    model_paths=model_paths,
                    github_repo=getattr(self.bot_cfg, 'GITHUB_REPO', '') or os.getenv('GITHUB_REPO', ''),
                    github_token=getattr(self.bot_cfg, 'GITHUB_TOKEN', '') or os.getenv('GITHUB_TOKEN', ''),
                    hf_repo_id=getattr(self.bot_cfg, 'GITHUB_GRANDMASTER_REPO', '') or os.getenv('HF_REPO_ID', ''),
                    hf_token=os.getenv('HF_TOKEN', '') or os.getenv('HUGGINGFACE_TOKEN', ''),
                    tag='grandmaster-latest',
                )
        except Exception as exc:
            log.debug(f"Model preservation skipped: {exc}")
        
        return self.training_history


# ═════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def run_training_cli():
    """Run training from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Advanced Multi-Model AI Training Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ticker", default="SPY", help="Ticker symbol")
    parser.add_argument("--train-start", default="2020-01-01", help="Training start")
    parser.add_argument("--train-end", default="2024-06-01", help="Training end")
    parser.add_argument("--ppo-timesteps", type=int, default=500_000, help="PPO training steps")
    parser.add_argument("--epochs", type=int, default=50, help="Transformer/LSTM epochs")
    parser.add_argument("--no-backtest", action="store_true", help="Skip backtest")
    parser.add_argument("--use-synthetic", action="store_false", help="Use real data")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"],
                        default="auto", help="Training device")
    
    args = parser.parse_args()
    
    config = TrainingConfig(
        ticker=args.ticker.upper(),
        train_start=args.train_start,
        train_end=args.train_end,
        ppo_timesteps=args.ppo_timesteps,
        epochs=args.epochs,
        run_backtest=not args.no_backtest,
        device=args.device,
    )
    
    pipeline = AdvancedTrainingPipeline(config)
    results = pipeline.run_all()
    
    return results


if __name__ == "__main__":
    run_training_cli()