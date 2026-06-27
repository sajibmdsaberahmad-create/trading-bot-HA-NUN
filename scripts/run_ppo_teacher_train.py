#!/usr/bin/env python3
"""Run PPO teacher session now — cloud API analyzes trades and retrains PPO."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import BotConfig
from core.notify import log
from core.ppo_teacher_training import run_ppo_teacher_session, trade_stats


def main() -> int:
    parser = argparse.ArgumentParser(description="PPO teacher–student training via Groq/Gemini")
    parser.add_argument("--force", action="store_true", help="Run even if win rate looks OK")
    parser.add_argument(
        "--model",
        default=os.getenv("REPLAY_MODEL_PATH", "models/ppo_trader_replay.zip"),
    )
    args = parser.parse_args()

    cfg = BotConfig()
    if args.model:
        cfg.MODEL_PATH = args.model

    stats = trade_stats()
    log.info(
        f"Trade stats: {stats['count']} round-trips | WR={stats.get('win_rate', 0):.1%} "
        f"avg_pnl=${stats.get('avg_pnl', 0):+.2f}"
    )

    model = None
    try:
        from core.agent import build_ppo_agent
        from core.env import TradingEnv
        import numpy as np

        dummy_f = np.zeros((cfg.WINDOW_SIZE + 2, cfg.N_FEATURES), np.float32)
        dummy_px = np.ones(cfg.WINDOW_SIZE + 2, np.float32) * 100.0
        env = TradingEnv(
            dummy_f, dummy_px, cfg.INITIAL_CASH,
            cfg.TRANSACTION_COST_PCT, cfg.WINDOW_SIZE, cfg.DEFAULT_MAX_POSITION_PCT,
        )
        path = cfg.MODEL_PATH if os.path.isfile(cfg.MODEL_PATH) else None
        model = build_ppo_agent(env, cfg, path)
    except Exception as exc:
        log.warning(f"Could not load PPO model: {exc}")

    result = run_ppo_teacher_session(cfg, model=model, trigger="cli", force=args.force)
    if result.get("skipped"):
        log.info(f"Skipped: {result.get('reason')}")
        return 0
    if not result.get("ok"):
        log.error(f"Teacher session failed: {result}")
        return 1
    log.info(f"Done: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
