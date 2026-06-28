#!/usr/bin/env python3
"""
core/replay_training.py — Replay uses full IB historical farm (CSV) to train everyone.

Same learning flywheel as live: PPO, Halim gold, co-evolution, proxy, incremental —
fed by replay_live experience buffer + intraday CSV depth (not a tiny subset).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.replay_scalper_runner import ReplayScalperRunner

REPLAY_TRAIN_SOURCES = frozenset({
    "replay_live", "replay_sim", "ppo_entry", "ppo_led", "ai_council",
    "halim_ppo_coevolution", "teacher_ppo", "live_trade", "live_entry",
})


def replay_training_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("REPLAY_TRAINING_ENABLED", "true").lower() in ("1", "true", "yes")


def ib_farm_stats(root: Optional[Path] = None) -> Dict[str, Any]:
    """Summarize IB-downloaded intraday CSV farm — bar depth per ticker."""
    from core.replay_data import resolve_replay_dir, list_replay_tickers

    root = root or resolve_replay_dir()
    if root is None:
        return {"ok": False, "tickers": 0}
    intraday = root / "intraday"
    if not intraday.is_dir():
        return {"ok": False, "tickers": 0}
    per_ticker: Dict[str, int] = {}
    total = 0
    for p in sorted(intraday.glob("*_1min.csv")):
        sym = p.stem.replace("_1min", "").upper()
        try:
            n = sum(1 for _ in open(p, encoding="utf-8", errors="ignore")) - 1
            n = max(0, n)
        except Exception:
            n = 0
        per_ticker[sym] = n
        total += n
    tickers = list(per_ticker.keys()) or list_replay_tickers(root)
    if not per_ticker and tickers:
        return {"ok": True, "tickers": len(tickers), "total_bars": 0, "per_ticker": {}}
    vals = list(per_ticker.values()) or [0]
    return {
        "ok": True,
        "tickers": len(per_ticker),
        "total_bars": total,
        "min_bars": min(vals),
        "max_bars": max(vals),
        "avg_bars": int(total / max(len(per_ticker), 1)),
        "per_ticker": per_ticker,
        "root": str(root),
    }


def log_ib_farm_banner(cfg: Optional[BotConfig] = None) -> None:
    st = ib_farm_stats()
    if not st.get("ok"):
        log.warning(
            "  IB replay farm: no intraday CSVs — run: "
            "PYTHONPATH=. python scripts/download_ib_replay_data.py --days 60"
        )
        return
    log.info(
        f"  IB replay farm: {st['tickers']} tickers · {st['total_bars']:,} bars "
        f"(min={st.get('min_bars', 0):,} max={st.get('max_bars', 0):,})"
    )
    min_bars = int(st.get("min_bars", 0))
    if min_bars < 2000:
        log.info(
            "  Tip: deepen farm — PYTHONPATH=. python scripts/download_ib_replay_data.py "
            "--days 60 --refresh-partial"
        )


def _replay_buffer_records(limit: int = 800) -> List[Dict[str, Any]]:
    try:
        from core.experience_buffer import load_recent
        rows = load_recent(n=limit)
    except Exception:
        return []
    out = []
    for r in rows:
        src = str(r.get("source", ""))
        if src in REPLAY_TRAIN_SOURCES or src.startswith("replay"):
            out.append(r)
    return out


def run_replay_training_cycle(
    cfg: Optional[BotConfig] = None,
    *,
    runner: Optional["ReplayScalperRunner"] = None,
    trigger: str = "replay_off_hours",
) -> Dict[str, Any]:
    """
    Full replay training — uses IB CSV farm depth + session replay_live buffer.
    Never blocks trading loop (call from off-hours / teardown).
    """
    cfg = cfg or BotConfig()
    if not replay_training_enabled(cfg):
        return {"skipped": True, "reason": "disabled"}

    result: Dict[str, Any] = {"trigger": trigger, "steps": {}}
    log.info(f"🎬 REPLAY TRAINING ({trigger}) — IB farm + PPO + Halim + co-evolution…")
    log_ib_farm_banner(cfg)

    try:
        from core.halim_gold_pipeline import run_halim_gold_pipeline
        result["steps"]["halim_gold"] = run_halim_gold_pipeline(
            cfg,
            trigger=trigger,
            prepare_sft=os.getenv("REPLAY_PREPARE_SFT", "true").lower() in ("1", "true", "yes"),
            package_colab=os.getenv("HALIM_AUTO_PACKAGE_COLAB", "true").lower() in ("1", "true", "yes"),
            min_sft_pairs=int(os.getenv("HALIM_REPLAY_MIN_SFT_PAIRS", os.getenv("HALIM_TODDLER_MIN_PAIRS", "2500"))),
        )
    except Exception as exc:
        result["steps"]["halim_gold"] = {"ok": False, "error": str(exc)[:120]}

    try:
        from core.halim_ppo_coevolution import run_coevolution_cycle
        result["steps"]["coevolution"] = run_coevolution_cycle(
            cfg, model=getattr(runner, "model", None) if runner else None, trigger=trigger,
        )
    except Exception as exc:
        result["steps"]["coevolution"] = {"ok": False, "error": str(exc)[:120]}

    fresh = _replay_buffer_records()
    result["buffer_records"] = len(fresh)

    if getattr(cfg, "INCREMENTAL_TRAINING_ENABLED", False) and fresh:
        steps = int(os.getenv("REPLAY_PPO_INCREMENTAL_STEPS", "2048"))
        if steps > 0:
            try:
                from core.online_trainer import run_incremental_training
                ok = run_incremental_training(cfg, fresh_records=fresh, ppo_steps=steps)
                result["steps"]["incremental_ppo"] = {"ok": ok, "records": len(fresh)}
            except Exception as exc:
                result["steps"]["incremental_ppo"] = {"ok": False, "error": str(exc)[:120]}
        else:
            result["steps"]["incremental_ppo"] = {"ok": False, "skipped": "REPLAY_PPO_INCREMENTAL_STEPS=0"}

    if os.getenv("REPLAY_TRAIN_PROXY", "true").lower() in ("1", "true", "yes"):
        try:
            from core.hybrid_distiller import train_teacher_proxy
            result["steps"]["teacher_proxy"] = train_teacher_proxy(cfg)
        except Exception as exc:
            result["steps"]["teacher_proxy"] = {"ok": False, "error": str(exc)[:120]}

    try:
        from core.ppo_teacher_training import maybe_run_ppo_teacher_training
        result["steps"]["ppo_teacher"] = maybe_run_ppo_teacher_training(
            cfg,
            model=getattr(runner, "model", None) if runner else None,
            trigger=f"replay_{trigger}",
            force=False,
            autopilot=getattr(runner, "autopilot", None) if runner else None,
            consciousness=getattr(runner, "consciousness", None) if runner else None,
        )
    except Exception as exc:
        result["steps"]["ppo_teacher"] = {"ok": False, "error": str(exc)[:120]}

    if runner is not None:
        try:
            runner._daily_self_train()
            result["steps"]["self_train"] = {"ok": True}
        except Exception as exc:
            result["steps"]["self_train"] = {"ok": False, "error": str(exc)[:120]}

    log.info(
        f"🎬 REPLAY TRAINING done — buffer={len(fresh)} records · "
        f"steps={list(result['steps'].keys())}"
    )
    return result
