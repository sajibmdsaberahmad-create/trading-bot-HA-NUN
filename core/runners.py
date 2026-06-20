#!/usr/bin/env python3
"""
core/runners.py — Warm-up training mode and offline evaluate (backtest) mode.
"""

import sys

import numpy as np

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.features import FeatureEngineer
from core.env import TradingEnv
from core.agent import build_ppo_agent, run_deterministic_episode
from core.notify import log


def run_warmup(cfg: BotConfig):
    log.info("=" * 70)
    log.info("  MODE: WARM-UP TRAINING")
    log.info(f"  Ticker: {cfg.TICKER} | History: {cfg.HISTORY_DURATION} {cfg.HISTORY_BAR_SIZE}")
    log.info(f"  Timesteps: {cfg.WARMUP_TIMESTEPS:,}")
    log.info("=" * 70)

    connector = IBConnector(cfg)
    if not connector.connect():
        sys.exit(1)

    try:
        data_mgr = DataManager(connector, cfg)
        hist_df = data_mgr.fetch_historical()
        features = FeatureEngineer.compute(hist_df)
        prices = hist_df["close"].values[-len(features):]

        if len(features) < 200:
            log.error(f"Not enough historical data after feature engineering ({len(features)} rows). Aborting.")
            sys.exit(1)

        split_idx = int(len(features) * cfg.WARMUP_SPLIT_PCT)
        train_features, eval_features = features[:split_idx], features[split_idx:]
        train_prices, eval_prices = prices[:split_idx], prices[split_idx:]

        log.info(f"Train: {len(train_features)} bars | Eval (held-out): {len(eval_features)} bars")

        train_env = TradingEnv(train_features, train_prices, cfg.INITIAL_CASH,
                                cfg.TRANSACTION_COST_PCT, cfg.WINDOW_SIZE, cfg.DEFAULT_MAX_POSITION_PCT)
        model = build_ppo_agent(train_env, cfg, model_path=None)

        log.info(f"Training for {cfg.WARMUP_TIMESTEPS:,} steps …")
        model.learn(total_timesteps=cfg.WARMUP_TIMESTEPS, progress_bar=True)
        model.save(cfg.MODEL_PATH)
        log.info(f"Model saved -> {cfg.MODEL_PATH}")

        metrics = {
            "warmup_timesteps": cfg.WARMUP_TIMESTEPS,
            "final_portfolio_value": None,
            "ppo_return_pct": None,
            "bh_return_pct": None,
            "alpha_vs_bh_pct": None,
            "action_counts": None
        }

        if len(eval_features) > cfg.WINDOW_SIZE + 5:
            eval_env = TradingEnv(eval_features, eval_prices, cfg.INITIAL_CASH,
                                   cfg.TRANSACTION_COST_PCT, cfg.WINDOW_SIZE, cfg.DEFAULT_MAX_POSITION_PCT)
            final_v, info = run_deterministic_episode(model, eval_env)
            ret = (final_v / cfg.INITIAL_CASH - 1.0) * 100.0
            bh_ret = (eval_prices[-1] / eval_prices[cfg.WINDOW_SIZE] - 1.0) * 100.0
            acts = info.get("action_counts", {})

            metrics.update({
                "final_portfolio_value": round(final_v, 2),
                "ppo_return_pct": round(ret, 2),
                "bh_return_pct": round(bh_ret, 2),
                "alpha_vs_bh_pct": round(ret - bh_ret, 2),
                "action_counts": acts
            })

            log.info("-" * 70)
            log.info("  HELD-OUT EVALUATION RESULTS")
            log.info(f"  Final portfolio value: ${final_v:,.2f}")
            log.info(f"  PPO agent return:      {ret:+.1f}%")
            log.info(f"  Buy-and-hold return:   {bh_ret:+.1f}%")
            log.info(f"  Alpha vs B&H:          {ret - bh_ret:+.1f}%")
            log.info(f"  Action breakdown:      HOLD={acts.get('HOLD',0)} BUY={acts.get('BUY',0)} SELL={acts.get('SELL',0)}")
            log.info("-" * 70)

            if acts.get("BUY", 0) == 0:
                log.warning(
                    "The agent never bought on eval data. This may mean it needs "
                    "more training steps or the entropy coef should be increased. "
                    "The model will improve during live fine-tuning."
                )

        from core.journal import record_training_session
        record_training_session(cfg, "WARMUP", metrics, cfg.MODEL_PATH)

        log.info("\nWarm-up complete!\nNext step: python main.py --mode trade")

    finally:
        connector.disconnect()


def run_evaluate(cfg: BotConfig):
    log.info("=" * 70)
    log.info("  MODE: EVALUATE (offline backtest)")
    log.info(f"  Ticker: {cfg.TICKER} | Model: {cfg.MODEL_PATH}")
    log.info("=" * 70)

    import os
    if not os.path.exists(cfg.MODEL_PATH):
        log.error(f"Model not found at '{cfg.MODEL_PATH}'. Run warmup first.")
        sys.exit(1)

    connector = IBConnector(cfg)
    if not connector.connect():
        sys.exit(1)

    try:
        data_mgr = DataManager(connector, cfg)
        hist_df = data_mgr.fetch_historical(duration="1 Y", bar_size="1 day")
        features = FeatureEngineer.compute(hist_df)
        prices = hist_df["close"].values[-len(features):]

        env = TradingEnv(features, prices, cfg.INITIAL_CASH, cfg.TRANSACTION_COST_PCT,
                          cfg.WINDOW_SIZE, cfg.DEFAULT_MAX_POSITION_PCT)
        model = build_ppo_agent(env, cfg, cfg.MODEL_PATH)
        final_v, info = run_deterministic_episode(model, env)
        ret = (final_v / cfg.INITIAL_CASH - 1.0) * 100.0
        bh_ret = (prices[-1] / prices[cfg.WINDOW_SIZE] - 1.0) * 100.0
        acts = info.get("action_counts", {})

        log.info("-" * 70)
        log.info("  BACKTEST RESULTS (last 1 year, daily bars)")
        log.info(f"  Final portfolio value: ${final_v:,.2f}")
        log.info(f"  PPO return:            {ret:+.1f}%")
        log.info(f"  Buy-and-hold return:   {bh_ret:+.1f}%")
        log.info(f"  Alpha vs B&H:          {ret - bh_ret:+.1f}%")
        log.info(f"  Action breakdown:      HOLD={acts.get('HOLD',0)} BUY={acts.get('BUY',0)} SELL={acts.get('SELL',0)}")
        log.info("-" * 70)

    finally:
        connector.disconnect()
