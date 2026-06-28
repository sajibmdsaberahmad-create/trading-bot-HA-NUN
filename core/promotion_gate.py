#!/usr/bin/env python3
"""
core/promotion_gate.py — Walk-forward gate before promoting replay PPO to live.

Only copies models/ppo_trader_replay.zip → models/ppo_trader.zip when the
latest replay session metrics pass minimum thresholds.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

STATE_PATH = Path("models/ppo_promotion_state.json")
REPLAY_MODEL = Path("models/ppo_trader_replay.zip")
LIVE_MODEL = Path("models/ppo_trader.zip")
TRADE_JOURNAL = Path("models/trade_journal.json")


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    return bool(getattr(cfg, "PPO_PROMOTION_GATE", True))


def _load_trades(runner: Any = None) -> List[Dict[str, Any]]:
    if runner is not None:
        tj = getattr(runner, "trade_journal", None)
        if tj:
            return list(tj)
    if TRADE_JOURNAL.is_file():
        try:
            data = json.loads(TRADE_JOURNAL.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return list(data.get("trades") or data.get("journal") or [])
        except Exception:
            pass
    return []


def session_metrics(trades: List[Dict[str, Any]], *, lookback: int = 0) -> Dict[str, Any]:
    chunk = trades[-lookback:] if lookback > 0 else list(trades)
    if not chunk:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "pnl_usd": 0.0}
    wins = sum(1 for t in chunk if float(t.get("pnl_usd", 0)) > 0)
    losses = len(chunk) - wins
    pnl = sum(float(t.get("pnl_usd", 0)) for t in chunk)
    return {
        "trades": len(chunk),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(chunk) if chunk else 0.0,
        "pnl_usd": round(pnl, 2),
    }


def evaluate_promotion(
    trades: List[Dict[str, Any]],
    cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    lookback = int(os.getenv("PROMOTION_LOOKBACK_TRADES", "0"))
    min_trades = int(os.getenv("PROMOTION_MIN_TRADES", "12"))
    min_wr = float(os.getenv("PROMOTION_MIN_WIN_RATE", "0.40"))
    min_pnl = float(os.getenv("PROMOTION_MIN_PNL_USD", "-75"))
    max_worst_pct = float(os.getenv("PROMOTION_MAX_WORST_TRADE_PCT", "3.5"))

    metrics = session_metrics(trades, lookback=lookback)
    reasons: List[str] = []
    if metrics["trades"] < min_trades:
        reasons.append(f"trades {metrics['trades']} < {min_trades}")
    if metrics["win_rate"] < min_wr:
        reasons.append(f"win_rate {metrics['win_rate']:.0%} < {min_wr:.0%}")
    if metrics["pnl_usd"] < min_pnl:
        reasons.append(f"pnl ${metrics['pnl_usd']:+.2f} < ${min_pnl:+.2f}")

    worst_pct = 0.0
    for t in (trades[-lookback:] if lookback > 0 else trades):
        try:
            worst_pct = min(worst_pct, float(t.get("pnl_pct", 0)))
        except (TypeError, ValueError):
            pass
    metrics["worst_trade_pct"] = round(worst_pct, 3)
    if worst_pct < -max_worst_pct:
        reasons.append(f"worst_trade {worst_pct:.2f}% < -{max_worst_pct:.2f}%")

    return {
        "pass": not reasons,
        "metrics": metrics,
        "reasons": reasons,
        "thresholds": {
            "min_trades": min_trades,
            "min_win_rate": min_wr,
            "min_pnl_usd": min_pnl,
            "max_worst_trade_pct": max_worst_pct,
            "lookback": lookback,
        },
    }


def try_promote_ppo_replay(
    cfg: Optional[BotConfig] = None,
    *,
    runner: Any = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Copy replay PPO to live if gate passes (or gate disabled / force)."""
    cfg = cfg or BotConfig()
    result: Dict[str, Any] = {"ok": False, "promoted": False}

    if not REPLAY_MODEL.is_file():
        result["reason"] = "no_replay_model"
        return result

    trades = _load_trades(runner)
    eval_result = evaluate_promotion(trades, cfg)
    result["evaluation"] = eval_result

    gate_on = _enabled(cfg) and not force
    if gate_on and not eval_result["pass"]:
        result["ok"] = True
        result["reason"] = "gate_blocked"
        result["blocked_reasons"] = eval_result["reasons"]
        log.info(
            f"⏸ PPO promotion blocked: {', '.join(eval_result['reasons'])} "
            f"({eval_result['metrics']['trades']} trades, "
            f"wr={eval_result['metrics']['win_rate']:.0%}, "
            f"pnl=${eval_result['metrics']['pnl_usd']:+.2f})"
        )
        _save_state(result, promoted=False)
        return result

    LIVE_MODEL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPLAY_MODEL, LIVE_MODEL)
    result["ok"] = True
    result["promoted"] = True
    result["reason"] = "force" if force else ("gate_pass" if gate_on else "gate_disabled")
    log.info(
        f"✓ PPO promoted replay→live ({result['reason']}) "
        f"wr={eval_result['metrics']['win_rate']:.0%} "
        f"pnl=${eval_result['metrics']['pnl_usd']:+.2f}"
    )
    _save_state(result, promoted=True)
    return result


def _save_state(result: Dict[str, Any], *, promoted: bool) -> None:
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "promoted": promoted,
        "evaluation": result.get("evaluation"),
        "reason": result.get("reason"),
    }
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(row, indent=2), encoding="utf-8")
    except Exception:
        pass
