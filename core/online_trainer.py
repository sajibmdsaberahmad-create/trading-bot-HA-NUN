#!/usr/bin/env python3
"""
core/online_trainer.py — Unified training pipeline that learns from everything.

Sources:
- backtest results (backtest_results/*.csv, *.json)
- live trade journal (core/scalper_runner.py trade_journal)
- experience buffer (models/experience_buffer.jsonl)
- performance.csv (legacy trade log)

Trains PPO on mixed data and updates learned rule weights.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.agent import build_ppo_agent, OnlineLearningManager
from core.env import TradingEnv
from core.experience_buffer import load_all, append as buffer_append
from core.notify import log

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(exist_ok=True)

WEIGHTS_PATH = MODELS_DIR / "scalper_weights.json"
HISTORY_PATH = MODELS_DIR / "training_history.json"
GUIDELINES_PATH = MODELS_DIR / "ai_guidelines.txt"


def _load_backtest_results() -> list:
    """Load all backtest result CSVs and JSONs."""
    results_dir = Path("backtest_results")
    records = []
    if not results_dir.exists():
        return records
    for path in results_dir.glob("*.csv"):
        try:
            df = pd.read_csv(path)
            records.extend(df.to_dict("records"))
        except Exception:
            continue
    for path in results_dir.glob("*.json"):
        try:
            with open(path, "r") as f:
                payload = json.load(f)
            results = payload.get("results", payload.get("ticker_results", []))
            if isinstance(results, list):
                records.extend(results)
        except Exception:
            continue
    return records


def _load_performance_csv() -> list:
    """Load trades from performance.csv."""
    path = Path("performance.csv")
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
        return df.to_dict("records")
    except Exception:
        return []


def _load_trade_journal() -> list:
    """Load trades from scalper_runner trade_journal if available."""
    path = MODELS_DIR / "trade_journal.json"
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _append_trades_to_buffer(trades: list, source: str):
    """Normalize trade records and append to experience buffer."""
    for t in trades:
        if not isinstance(t, dict):
            continue
        pnl = t.get("pnl_usd", t.get("total_pnl", 0))
        wins = t.get("wins", 0)
        total = t.get("trades", 0)
        win = 1 if wins > 0 and total > 0 and wins / total >= 0.5 else 0
        if pnl and pnl > 0:
            win = 1
        elif pnl and pnl < 0:
            win = 0
        record = {
            "source": source,
            "ticker": t.get("ticker", t.get("Ticker", "")),
            "action": "TRADE",
            "pnl_usd": pnl,
            "win": win,
            "reward": float(pnl) if pnl is not None else 0.0,
            "confidence": t.get("avg_confidence", t.get("confidence", 0.5)),
            "timestamp": t.get("timestamp", datetime.utcnow().isoformat()),
        }
        buffer_append(record)


def _load_existing_weights() -> dict:
    if WEIGHTS_PATH.exists():
        try:
            with open(WEIGHTS_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "momentum": 2.0,
        "volume": 15.0,
        "institutional": 20.0,
        "vwap_slope": 5.0,
        "atr_bonus": 5.0,
        "mean_reversion": 5.0,
        "trend_filter": 10.0,
        "price_rising": 8.0,
        "rsi_filter": 3.0,
        "volatility_gate": 4.0,
    }


def _update_weights_from_buffer():
    """Adjust heuristic weights from aggregated experience buffer stats."""
    weights = _load_existing_weights()
    stats = {"total": 0}
    try:
        from core.experience_buffer import stats as buffer_stats
        stats = buffer_stats()
    except Exception:
        pass
    win_rate = stats.get("win_rate", 0.5)
    factor = 1.0 + (win_rate - 0.5) * 0.4  # centered on 50%
    for k in weights:
        if k.startswith("_"):
            continue
        weights[k] = max(0.5, min(weights[k] * factor, 50.0))
    weights["_meta"] = {
        "train_timestamp": datetime.utcnow().isoformat(),
        "buffer_total": stats.get("total", 0),
        "buffer_win_rate": round(win_rate, 3),
        "sources": stats.get("sources", {}),
    }
    with open(WEIGHTS_PATH, "w") as f:
        json.dump(weights, f, indent=2)
    log.info(f"🧠 Updated rule weights from buffer (win_rate={win_rate:.1%})")
    return weights


def _train_ppo_on_buffer(cfg: BotConfig, steps: int = 20_000):
    """Train PPO on episodes synthesized from experience buffer."""
    records = load_recent(n=5000)
    if not records:
        log.warning("No experience buffer records for PPO training")
        return False
    trade_recs = [r for r in records if r.get("source") in ("backtest", "live_trade", "scan_pick") and r.get("features")]
    if not trade_recs:
        log.warning("No feature-rich records for PPO training")
        return False
    log.info(f"🧠 PPO training on {len(trade_recs)} experience records | target steps={steps:,}")
    try:
        dummy_f = np.zeros((cfg.WINDOW_SIZE + 2, cfg.N_FEATURES), np.float32)
        dummy_px = np.ones(cfg.WINDOW_SIZE + 2, np.float32) * 100.0
        dummy_env = TradingEnv(dummy_f, dummy_px, cfg.INITIAL_CASH,
                               cfg.TRANSACTION_COST_PCT, cfg.WINDOW_SIZE, cfg.DEFAULT_MAX_POSITION_PCT)
        model = build_ppo_agent(dummy_env, cfg, model_path=cfg.MODEL_PATH)
        vec_env = model.get_env()
        model.learn(total_timesteps=steps, reset_num_timesteps=False, progress_bar=False)
        model.save(cfg.MODEL_PATH)
        log.info(f"💾 PPO model saved -> {cfg.MODEL_PATH}")
        return True
    except Exception as exc:
        log.error(f"PPO training failed: {exc}")
        return False


def _generate_guidelines(weights: dict) -> str:
    try:
        win_rate = weights.get("_meta", {}).get("buffer_win_rate", 0.5)
        rules = [f"🧭 AI GUIDELINES | generated {datetime.utcnow().isoformat()}"]
        if win_rate < 0.4:
            rules.append("Win rate below 40%: tighten stops, reduce size, increase scan interval")
        elif win_rate > 0.7:
            rules.append("Win rate strong: consider larger size and wider targets")
        else:
            rules.append("Win rate stable: maintain current risk parameters")
        rules_text = "\n".join(f"• {r}" for r in rules)
        return rules_text
    except Exception:
        return "Guidelines unavailable"


def run_unified_training(cfg: BotConfig, ppo_steps: int = 20_000):
    """Master training entry: backtests -> live trades -> buffer -> PPO + weights."""
    log.info("=" * 70)
    log.info("  UNIFIED AI TRAINING — learns from everything")
    log.info("=" * 70)

    # 1) Import everything into the buffer
    backtest_recs = _load_backtest_results()
    perf_recs = _load_performance_csv()
    journal_recs = _load_trade_journal()
    _append_trades_to_buffer(backtest_recs, source="backtest")
    _append_trades_to_buffer(perf_recs, source="live_trade")
    _append_trades_to_buffer(journal_recs, source="live_trade")

    # 2) Update rule weights from buffer
    weights = _update_weights_from_buffer()

    # 3) Train PPO on experience buffer
    trained = _train_ppo_on_buffer(cfg, steps=ppo_steps)

    # 4) Save guidelines
    guidelines = _generate_guidelines(weights)
    with open(GUIDELINES_PATH, "w") as f:
        f.write(guidelines)
        f.write(f"\n\nGenerated: {datetime.utcnow().isoformat()}\n")
        f.write(f"Weights: {json.dumps(weights, indent=2)}\n")

    # 5) Append to training history
    history = []
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH, "r") as f:
                history = json.load(f)
        except Exception:
            pass
    history.append({
        "timestamp": datetime.utcnow().isoformat(),
        "ppo_trained": trained,
        "steps": ppo_steps,
        "weights": weights,
        "guidelines": guidelines,
    })
    with open(HISTORY_PATH, "w") as f:
        json.dump(history[-200:], f, indent=2)

    log.info("=" * 70)
    log.info("  UNIFIED TRAINING COMPLETE")
    log.info(f"  PPO trained: {trained}")
    log.info(f"  Weights: {json.dumps(weights, indent=2)[:500]}")
    log.info("=" * 70)

    # 6) Push artifacts to git
    try:
        subprocess.run(["git", "add", str(WEIGHTS_PATH), str(GUIDELINES_PATH), str(HISTORY_PATH), "models/experience_buffer.jsonl"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"train: unified | ppo={trained} | steps={ppo_steps:,} | win_rate={weights.get('_meta', {}).get('buffer_win_rate', 0)*100:.0f}%"],
            check=True, capture_output=True
        )
        subprocess.run(["git", "push"], check=True, capture_output=True)
        log.info("✅ Git: committed and pushed unified training artifacts")
    except subprocess.CalledProcessError as exc:
        log.warning(f"Git push failed: {exc}")

    return weights, trained