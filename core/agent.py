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
"""

import os
from typing import Optional, Tuple, Dict

import numpy as np

try:
    import gymnasium as gym
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
except ImportError:
    raise SystemExit("ERROR: gymnasium/stable-baselines3 not installed.")

from core.config import BotConfig
from core.env import TradingEnv
from core.notify import log


def build_ppo_agent(env: gym.Env, cfg: BotConfig, model_path: Optional[str] = None) -> PPO:
    """Load an existing model if present, otherwise build a new PPO agent."""
    vec_env = DummyVecEnv([lambda: env])

    if model_path and os.path.exists(model_path):
        log.info(f"Loading existing model from {model_path} …")
        model = PPO.load(model_path, env=vec_env)
        model.set_env(vec_env)
        return model

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
    return model


def run_deterministic_episode(model: PPO, env: gym.Env) -> Tuple[float, Dict]:
    obs, _ = env.reset()
    done = False
    info = {}
    actions = []

    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, truncated, info = env.step(int(action))
        done = terminated or truncated
        actions.append(int(action))

    action_counts = {"HOLD": actions.count(0), "BUY": actions.count(1), "SELL": actions.count(2)}
    info["action_counts"] = action_counts
    return float(info.get("portfolio_value", 0.0)), info


class OnlineLearningManager:
    """
    Incremental fine-tuning during live trading. Every FINE_TUNE_EVERY_BARS
    new decision bars, retrains briefly on the recent window so the model
    adapts to the current market regime without forgetting prior learning
    too fast (PPO's clip_range bounds how much the policy can move per
    update, which is what makes this safe to do continuously).
    """

    def __init__(self, model: PPO, cfg: BotConfig):
        self.model = model
        self.cfg = cfg
        self._bars_since = 0
        self._tune_count = 0

    def notify_new_bar(self, features: np.ndarray, prices: np.ndarray) -> bool:
        self._bars_since += 1
        if len(features) < self.cfg.MIN_BARS_FOR_FINETUNE or self._bars_since < self.cfg.FINE_TUNE_EVERY_BARS:
            return False
        self._fine_tune(features, prices)
        self._bars_since = 0
        return True

    def _fine_tune(self, features: np.ndarray, prices: np.ndarray):
        self._tune_count += 1
        log.info(f"Online fine-tune #{self._tune_count} | {len(features)} bars | {self.cfg.FINE_TUNE_STEPS:,} PPO steps")
        try:
            env = TradingEnv(features, prices, self.cfg.INITIAL_CASH, self.cfg.TRANSACTION_COST_PCT,
                              self.cfg.WINDOW_SIZE, self.cfg.MAX_POSITION_PCT)
            vec_env = DummyVecEnv([lambda: env])
            self.model.set_env(vec_env)
            self.model.learn(total_timesteps=self.cfg.FINE_TUNE_STEPS, reset_num_timesteps=False, progress_bar=False)
            self.model.save(self.cfg.MODEL_PATH)
            log.info(f"Fine-tune #{self._tune_count} saved -> {self.cfg.MODEL_PATH}")

            # Record fine-tuning event in journal
            metrics = {
                "fine_tune_number": self._tune_count,
                "fine_tune_steps": self.cfg.FINE_TUNE_STEPS,
                "features_length": len(features)
            }
            from core.journal import record_training_session
            record_training_session(self.cfg, f"FINETUNE_{self._tune_count}", metrics, self.cfg.MODEL_PATH)
        except Exception as exc:
            log.error(f"Fine-tune #{self._tune_count} failed: {exc}")
