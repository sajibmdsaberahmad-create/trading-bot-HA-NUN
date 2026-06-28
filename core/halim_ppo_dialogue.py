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
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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
    if os.getenv("HALIM_DIALOGUE_DURING_TRADING", "true").lower() not in ("1", "true", "yes"):
        try:
            from core.trading_focus_guard import is_trading_session_active
            if is_trading_session_active():
                return False
        except Exception:
            pass
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


def _format_ppo_voice(ev: Dict[str, Any], cmp: Dict[str, Any]) -> str:
    sig = cmp.get("ppo_signal")
    action = {True: "ENTER", False: "SKIP", None: "HOLD"}.get(sig, str(sig))
    conf = cmp.get("ppo_conf", 0)
    reason = str(ev.get("ppo_reason") or "").strip()
    line = f"PPO reflex: {action} (conf={conf:.0%})"
    if reason:
        line += f" — {reason[:160]}"
    return line


def _format_halim_voice(ev: Dict[str, Any], cmp: Dict[str, Any]) -> str:
    sig = cmp.get("halim_signal")
    action = {True: "ENTER", False: "SKIP", None: "HOLD"}.get(sig, str(sig))
    conf = cmp.get("halim_conf", 0)
    reason = str(ev.get("halim_reason") or "").strip()
    src = ev.get("halim_source", "halim")
    line = f"Halim ({src}): {action} (conf={conf:.0%})"
    if reason:
        line += f" — {reason[:200]}"
    return line


def _synthesize_dialogue(ev: Dict[str, Any]) -> Optional[str]:
    cmp = ev.get("comparison") or {}
    task = str(ev.get("task") or "")
    if task in ("None", ""):
        return None
    ppo_line = _format_ppo_voice(ev, cmp)
    halim_line = _format_halim_voice(ev, cmp)
    if len(ppo_line) < 12 or len(halim_line) < 12:
        return None
    agree = cmp.get("ppo_halim_agree")
    verdict = ""
    if ev.get("outcome") is not None:
        verdict = f"\nOutcome: {ev.get('outcome')}"
        if ev.get("outcome_pnl") is not None:
            verdict += f" (${ev.get('outcome_pnl')})"
    elif cmp.get("correction_for") not in (None, "none"):
        verdict = f"\nCorrection needed: {cmp.get('correction_for')}"
    return f"{ppo_line}\n{halim_line}\nAgree: {agree}{verdict}"


def export_dialogue_gold(*, max_records: int = 20_000) -> Dict[str, Any]:
    """
    Rewrite dialogue training gold — from generative dialogue log + co-evolution synthesis.
    Runs even when LM was blocked during trading (synthesizes PPO↔Halim voice pairs).
    """
    from core.halim_ppo_coevolution import COEVOLUTION_LOG

    DIALOGUE_GOLD.parent.mkdir(parents=True, exist_ok=True)
    seen_dialogue: set = set()
    out_rows: List[Dict[str, Any]] = []

    def _add_row(
        *,
        ticker: str,
        task: str,
        dialogue: str,
        data: Dict[str, Any],
        phase: str,
        source: str,
        timestamp: str,
    ) -> None:
        dkey = dialogue[:400]
        if dkey in seen_dialogue or len(dialogue) < 20:
            return
        seen_dialogue.add(dkey)
        base_input = json.dumps(data, default=str)[:1400]
        out_rows.append({
            "capability": "decision_text",
            "instruction": f"PPO ↔ Halim {phase} dialogue — {task}",
            "input": base_input,
            "output": dialogue[:1400],
            "source": source,
            "ticker": ticker.upper(),
            "task": task,
            "timestamp": timestamp,
        })
        out_rows.append({
            "capability": "trade_reflex",
            "instruction": f"PPO reflex voice — learn Halim reconciliation ({task})",
            "input": base_input,
            "output": dialogue[:1400],
            "source": "dialogue_ppo",
            "ticker": ticker.upper(),
            "task": task,
            "timestamp": timestamp,
        })

    if DIALOGUE_LOG.is_file():
        with open(DIALOGUE_LOG, encoding="utf-8") as fh:
            for line in fh.readlines()[-max_records:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                dialogue = str(row.get("dialogue") or "").strip()
                if not dialogue:
                    continue
                _add_row(
                    ticker=str(row.get("ticker") or ""),
                    task=str(row.get("task") or "entry_decision"),
                    dialogue=dialogue,
                    data={"comparison": row.get("comparison"), "source": row.get("source")},
                    phase=str(row.get("phase") or "pre_action"),
                    source=f"dialogue_{row.get('phase', 'pre_action')}",
                    timestamp=str(row.get("timestamp") or ""),
                )

    if COEVOLUTION_LOG.is_file():
        seen_events: set = set()
        with open(COEVOLUTION_LOG, encoding="utf-8") as fh:
            for line in fh.readlines()[-max_records:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ekey = f"{ev.get('timestamp')}|{ev.get('ticker')}|{ev.get('task')}"
                if ekey in seen_events:
                    continue
                seen_events.add(ekey)
                cmp = ev.get("comparison") or {}
                if cmp.get("ppo_halim_agree") is not True and ev.get("outcome") is None:
                    if cmp.get("correction_for") in (None, "none"):
                        continue
                dialogue = _synthesize_dialogue(ev)
                if not dialogue:
                    continue
                _add_row(
                    ticker=str(ev.get("ticker") or ""),
                    task=str(ev.get("task") or "entry_decision"),
                    dialogue=dialogue,
                    data={
                        "ppo": cmp,
                        "pipeline": ev.get("pipeline"),
                        "halim_source": ev.get("halim_source"),
                        "outcome": ev.get("outcome"),
                    },
                    phase="synthesized",
                    source="dialogue_synthesized",
                    timestamp=str(ev.get("timestamp") or ""),
                )

    tmp = DIALOGUE_GOLD.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in out_rows:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    tmp.replace(DIALOGUE_GOLD)

    return {
        "ok": True,
        "pairs": len(out_rows),
        "dialogue_lines": len(seen_dialogue),
        "rewritten": True,
    }
