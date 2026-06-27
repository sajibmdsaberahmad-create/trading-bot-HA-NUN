#!/usr/bin/env python3
"""
core/halim_ppo_dialogue.py — Full generative two-way PPO ↔ Halim dialogue on every trade.

PPO reflex speaks through Halim's voice; Halim mind responds. Every exchange is
generative, journaled for mutual training, optionally broadcast on entry/exit.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

DIALOGUE_LOG = Path("halim/data/coevolution/dialogue.jsonl")
DIALOGUE_GOLD = Path("halim/data/training/dialogue_gold.jsonl")

_throttle: Dict[str, float] = {}
_lock = threading.Lock()


def _dialogue_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    if os.getenv("HALIM_PPO_DIALOGUE", "true").lower() not in ("1", "true", "yes"):
        return False
    try:
        from core.halim_ppo_coevolution import _enabled
        return _enabled(cfg)
    except Exception:
        return True


def _telegram_dialogue(cfg: BotConfig, task: str) -> bool:
    if os.getenv("HALIM_PPO_DIALOGUE_TELEGRAM", "true").lower() not in ("1", "true", "yes"):
        return False
    return task in ("entry_decision", "exit_decision", "trade_close")


def _should_run_dialogue(
    task: str,
    ticker: str,
    comparison: Dict[str, Any],
    cfg: BotConfig,
) -> bool:
    if not _dialogue_enabled(cfg):
        return False
    if task in ("entry_decision", "exit_decision", "trade_close"):
        return True
    if comparison.get("ppo_halim_agree") is False:
        return True
    if comparison.get("correction_for") not in (None, "none"):
        return True
    throttle = float(os.getenv("HALIM_PPO_DIALOGUE_THROTTLE_SEC", "25"))
    key = f"{ticker.upper()}|{task}"
    now = time.time()
    with _lock:
        if now - _throttle.get(key, 0) < throttle:
            return False
        _throttle[key] = now
    return task in (
        "position_manage", "stagnation_check", "risk_exit", "entry_decision", "exit_decision",
    )


def _append(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")


def _export_dialogue_gold(
    *,
    ticker: str,
    task: str,
    dialogue: str,
    data: Dict[str, Any],
    phase: str = "pre_action",
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    base_input = json.dumps(data, default=str)[:1400]
    _append(DIALOGUE_GOLD, {
        "capability": "decision_text",
        "instruction": f"PPO ↔ Halim {phase} dialogue — {task}",
        "input": base_input,
        "output": dialogue[:1400],
        "source": f"dialogue_{phase}",
        "ticker": ticker.upper(),
        "task": task,
        "timestamp": ts,
    })
    _append(DIALOGUE_GOLD, {
        "capability": "trade_reflex",
        "instruction": f"PPO reflex voice — learn Halim reconciliation ({task})",
        "input": base_input,
        "output": dialogue[:1400],
        "source": "dialogue_ppo",
        "ticker": ticker.upper(),
        "task": task,
        "timestamp": ts,
    })


def generate_ppo_halim_dialogue(
    *,
    ticker: str,
    task: str,
    ppo_signal: Any,
    ppo_conf: float,
    ppo_reason: str,
    halim_source: str,
    halim_signal: Any,
    halim_conf: float,
    halim_reason: str,
    comparison: Dict[str, Any],
    executed: Optional[Dict[str, Any]] = None,
    pipeline: str = "",
    extra: Optional[Dict[str, Any]] = None,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
    phase: str = "pre_action",
) -> Optional[str]:
    """
    Generative two-voice dialogue — PPO reflex + Halim mind. Never static templates.
    """
    cfg = cfg or BotConfig()
    data = {
        "phase": phase,
        "ticker": ticker.upper(),
        "task": task,
        "pipeline": pipeline,
        "ppo": {
            "signal": ppo_signal,
            "conf": round(float(ppo_conf), 4),
            "reason": str(ppo_reason or "")[:200],
        },
        "halim": {
            "source": halim_source,
            "signal": halim_signal,
            "conf": round(float(halim_conf), 4),
            "reason": str(halim_reason or "")[:200],
        },
        "comparison": comparison,
        "executed": executed,
        **(extra or {}),
    }
    intent = "trade_outcome_dialogue" if phase == "post_trade" else "ppo_halim_dialogue"
    extra_block = f"TRADE DECISION DATA:\n{json.dumps(data, default=str)}"
    try:
        from core.halim_companion import companion_speak, live_snapshot
        snap = live_snapshot(runner, cfg)
        extra_block = (
            f"LIVE SNAPSHOT: {json.dumps(snap, default=str)}\n{extra_block}"
        )
        r = companion_speak(
            "",
            cfg=cfg,
            runner=runner,
            extra=extra_block,
            intent=intent,
            purpose="decision_text",
        )
        text = (r.get("text") or "").strip()
        if len(text) < 16:
            return None
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "ticker": ticker.upper(),
            "task": task,
            "dialogue": text[:2000],
            "source": r.get("source", "companion"),
            "comparison": comparison,
            "agree": comparison.get("ppo_halim_agree"),
        }
        _append(DIALOGUE_LOG, row)
        _export_dialogue_gold(
            ticker=ticker, task=task, dialogue=text, data=data, phase=phase,
        )
        try:
            from core.halim_action_learn import record_action
            record_action(
                "decision_text",
                f"dialogue_{task}",
                input_text=extra_block[:1000],
                output_text=text[:1400],
                outcome="agree" if comparison.get("ppo_halim_agree") else "dialogue",
                source=f"ppo_halim_dialogue:{r.get('source', '?')}",
                cfg=cfg,
            )
        except Exception:
            pass
        log.info(
            f"  💬 PPO↔Halim {ticker}/{task} ({phase}): "
            f"agree={comparison.get('ppo_halim_agree')} · {text[:100]}…"
        )
        return text
    except Exception as exc:
        log.debug(f"PPO↔Halim dialogue: {exc}")
        return None


def schedule_ppo_halim_dialogue(
    *,
    ticker: str,
    task: str,
    ppo_signal: Any,
    ppo_conf: float,
    ppo_reason: str,
    halim_source: str,
    halim_signal: Any,
    halim_conf: float,
    halim_reason: str,
    comparison: Dict[str, Any],
    executed: Optional[Dict[str, Any]] = None,
    pipeline: str = "",
    extra: Optional[Dict[str, Any]] = None,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
    phase: str = "pre_action",
) -> None:
    """Non-blocking dialogue — never delays trading."""
    cfg = cfg or BotConfig()
    if not _should_run_dialogue(task, ticker, comparison, cfg):
        return

    def _run() -> None:
        text = generate_ppo_halim_dialogue(
            ticker=ticker,
            task=task,
            ppo_signal=ppo_signal,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            halim_source=halim_source,
            halim_signal=halim_signal,
            halim_conf=halim_conf,
            halim_reason=halim_reason,
            comparison=comparison,
            executed=executed,
            pipeline=pipeline,
            extra=extra,
            cfg=cfg,
            runner=runner,
            phase=phase,
        )
        if text and _telegram_dialogue(cfg, task):
            try:
                from core.telegram_broadcast import broadcast_precomposed
                header = "🎯 ENTRY" if task == "entry_decision" else (
                    "🚪 EXIT" if task == "exit_decision" else "💬 TRADE"
                )
                broadcast_precomposed(cfg, f"{header} {ticker.upper()}\n{text[:3200]}")
            except Exception as exc:
                log.debug(f"Dialogue telegram: {exc}")

    threading.Thread(
        target=_run,
        name=f"ppo-halim-dlg-{ticker[:4]}-{task[:8]}",
        daemon=True,
    ).start()


def schedule_trade_outcome_dialogue(
    *,
    ticker: str,
    pnl: float,
    win: bool,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
    recent_comparison: Optional[Dict[str, Any]] = None,
) -> None:
    """Post-close generative wrap-up — labels who was right, both learn."""
    cfg = cfg or BotConfig()
    if not _dialogue_enabled(cfg):
        return

    def _run() -> None:
        extra = {
            "outcome_pnl": round(float(pnl), 2),
            "win": win,
            "recent_comparison": recent_comparison or {},
        }
        cmp = recent_comparison or {}
        generate_ppo_halim_dialogue(
            ticker=ticker,
            task="trade_close",
            ppo_signal=cmp.get("ppo_signal"),
            ppo_conf=float(cmp.get("ppo_conf", 0.5) or 0.5),
            ppo_reason="",
            halim_source="outcome",
            halim_signal=cmp.get("halim_signal"),
            halim_conf=float(cmp.get("halim_conf", 0.5) or 0.5),
            halim_reason="",
            comparison=cmp if cmp else {"outcome": "win" if win else "loss"},
            extra=extra,
            cfg=cfg,
            runner=runner,
            phase="post_trade",
        )

    threading.Thread(
        target=_run,
        name=f"ppo-halim-outcome-{ticker[:4]}",
        daemon=True,
    ).start()
