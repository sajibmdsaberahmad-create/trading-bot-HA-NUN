#!/usr/bin/env python3
"""
core/halim_ppo_coevolution.py — PPO ↔ Halim mutual learning.

Both students learn from each other and from all other sources (council, trades, web).
They evolve together: agreements reinforce; disagreements become correction gold.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

COEVOLUTION_LOG = Path("halim/data/coevolution/correction_log.jsonl")
COEVOLUTION_GOLD = Path("halim/data/training/coevolution_gold.jsonl")
STATE_PATH = Path("models/halim_ppo_coevolution_state.json")
PENDING_OUTCOMES: List[Dict[str, Any]] = []


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("HALIM_PPO_COEVOLUTION", "true").lower() in ("1", "true", "yes")


def _append(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _bool_signal(val: Any, *, task: str) -> Optional[bool]:
    if task in ("entry_decision",):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return int(val) == 1
        return bool(val)
    if task in ("exit_decision", "stagnation_check", "position_manage", "risk_exit"):
        return bool(val) if val is not None else None
    if isinstance(val, dict):
        if "enter" in val:
            return bool(val.get("enter"))
        if "exit" in val:
            return bool(val.get("exit"))
    return None


def compare_ppo_halim(
    *,
    task: str,
    ppo_signal: Any,
    ppo_conf: float,
    halim_signal: Any,
    halim_conf: float = 0.5,
    executed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compare PPO vs Halim (proxy/council/LM) — who agrees with what was executed."""
    ppo_b = _bool_signal(ppo_signal, task=task)
    halim_b = _bool_signal(halim_signal, task=task)
    if halim_b is None and isinstance(halim_signal, dict):
        halim_b = _bool_signal(halim_signal, task=task)

    exec_b = None
    if executed:
        if task == "entry_decision":
            exec_b = bool(executed.get("enter", False))
        elif task in ("exit_decision", "stagnation_check", "risk_exit"):
            exec_b = bool(executed.get("exit", executed.get("action") == "EXIT"))
        else:
            exec_b = _bool_signal(executed, task=task)

    ppo_halim_agree = (ppo_b == halim_b) if ppo_b is not None and halim_b is not None else None
    ppo_exec_agree = (ppo_b == exec_b) if ppo_b is not None and exec_b is not None else None
    halim_exec_agree = (halim_b == exec_b) if halim_b is not None and exec_b is not None else None

    correction_for = "none"
    if ppo_halim_agree is False:
        if ppo_exec_agree is True and halim_exec_agree is False:
            correction_for = "halim"
        elif halim_exec_agree is True and ppo_exec_agree is False:
            correction_for = "ppo"

    return {
        "ppo_signal": ppo_b,
        "halim_signal": halim_b,
        "executed": exec_b,
        "ppo_halim_agree": ppo_halim_agree,
        "ppo_exec_agree": ppo_exec_agree,
        "halim_exec_agree": halim_exec_agree,
        "correction_for": correction_for,
        "ppo_conf": round(float(ppo_conf), 4),
        "halim_conf": round(float(halim_conf), 4),
    }


def record_coevolution(
    cfg: Optional[BotConfig] = None,
    *,
    ticker: str,
    task: str,
    ppo_signal: Any,
    ppo_conf: float,
    ppo_reason: str = "",
    halim_source: str = "halim",
    halim_signal: Any = None,
    halim_conf: float = 0.5,
    halim_reason: str = "",
    executed: Optional[Dict[str, Any]] = None,
    pipeline: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Journal one PPO↔Halim interaction. Feeds both students via buffer + action gold.
    halim_source: proxy | council | deferred | halim_lm | halim_server
    """
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return None

    cmp = compare_ppo_halim(
        task=task,
        ppo_signal=ppo_signal,
        ppo_conf=ppo_conf,
        halim_signal=halim_signal if halim_signal is not None else executed,
        halim_conf=halim_conf,
        executed=executed,
    )

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker.upper(),
        "task": task,
        "pipeline": pipeline,
        "halim_source": halim_source,
        "ppo_reason": str(ppo_reason or "")[:200],
        "halim_reason": str(halim_reason or "")[:300],
        "comparison": cmp,
        "outcome": None,
        "outcome_pnl": None,
        **(extra or {}),
    }
    _append(COEVOLUTION_LOG, row)

    # Experience buffer — PPO micro-train reads this
    weight = 1.0
    if cmp.get("correction_for") == "ppo":
        weight = float(getattr(cfg, "PPO_LEARNING_WEIGHT", 1.5)) * 1.2
    elif cmp.get("correction_for") == "halim":
        weight = 1.15
    elif cmp.get("ppo_halim_agree"):
        weight = 1.05

    try:
        from core.experience_buffer import append as buffer_append
        buffer_append({
            "source": "halim_ppo_coevolution",
            "ticker": ticker.upper(),
            "task": task,
            "ppo_signal": ppo_signal,
            "ppo_conf": cmp["ppo_conf"],
            "halim_source": halim_source,
            "halim_signal": halim_signal,
            "halim_conf": cmp["halim_conf"],
            "comparison": cmp,
            "training_weight": weight,
            "pipeline": pipeline,
            "timestamp": row["timestamp"],
        })
    except Exception:
        pass

    # Halim action gold — Halim LM learns from PPO context
    try:
        from core.halim_action_learn import record_action
        cap = "enter_skip" if halim_source == "proxy" else "decision_text"
        agree = cmp.get("ppo_halim_agree")
        outcome_label = "agree" if agree else "correct_each_other"
        record_action(
            cap,
            f"coevolution_{task}",
            input_text=(
                f"{ticker} PPO={ppo_signal} conf={ppo_conf:.2f} | "
                f"Halim({halim_source})={halim_signal} | exec={cmp.get('executed')}"
            )[:800],
            output_text=(
                f"{'AGREE' if agree else 'CORRECT'}: {halim_reason or ppo_reason}"[:800]
            ),
            outcome=outcome_label,
            source=f"coevolution:{halim_source}",
            meta={"comparison": cmp, "correction_for": cmp.get("correction_for")},
            cfg=cfg,
        )
    except Exception:
        pass

    if cmp.get("correction_for") != "none":
        log.debug(
            f"  🔄 PPO↔Halim {ticker}/{task}: correction→{cmp['correction_for']} "
            f"(source={halim_source})"
        )
        reflect_ok = os.getenv("HALIM_PPO_GENERATIVE_REFLECT", "true").lower() in ("1", "true", "yes")
        if reflect_ok and os.getenv("HALIM_DIALOGUE_DURING_TRADING", "true").lower() not in ("1", "true", "yes"):
            try:
                from core.trading_focus_guard import is_trading_session_active
                if is_trading_session_active():
                    reflect_ok = False
            except Exception:
                pass
        if reflect_ok:
            def _reflect() -> None:
                try:
                    from core.halim_companion import coevolution_generative_reflect
                    coevolution_generative_reflect(
                        ticker=ticker,
                        task=task,
                        comparison=cmp,
                        ppo_reason=ppo_reason,
                        halim_reason=halim_reason,
                        halim_source=halim_source,
                        cfg=cfg,
                    )
                except Exception as exc:
                    log.debug(f"Coevolution generative reflect: {exc}")
            threading.Thread(target=_reflect, name="coevo-reflect", daemon=True).start()

    global PENDING_OUTCOMES
    PENDING_OUTCOMES.append({"ticker": ticker.upper(), "task": task, "timestamp": row["timestamp"], "comparison": cmp})
    if len(PENDING_OUTCOMES) > 500:
        PENDING_OUTCOMES[:] = PENDING_OUTCOMES[-500:]

    # Full generative two-way dialogue on every trade decision (throttled for noise tasks)
    try:
        from core.halim_ppo_dialogue import schedule_ppo_halim_dialogue
        runner = None
        try:
            from core.halim_runtime import get_halim_runtime
            rt = get_halim_runtime()
            if rt:
                runner = getattr(rt, "_runner", None)
        except Exception:
            pass
        schedule_ppo_halim_dialogue(
            ticker=ticker,
            task=task,
            ppo_signal=ppo_signal,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            halim_source=halim_source,
            halim_signal=halim_signal,
            halim_conf=halim_conf,
            halim_reason=halim_reason,
            comparison=cmp,
            executed=executed,
            pipeline=pipeline,
            extra=extra,
            cfg=cfg,
            runner=runner,
            phase="pre_action",
        )
    except Exception as exc:
        log.debug(f"PPO↔Halim dialogue schedule: {exc}")

    return row


def attach_trade_outcome(
    ticker: str,
    *,
    pnl: float,
    win: bool,
    cfg: Optional[BotConfig] = None,
    trade_rec: Optional[Dict[str, Any]] = None,
) -> None:
    """After trade close — label who was right; generative outcome dialogue."""
    if not _enabled(cfg):
        return
    cfg = cfg or BotConfig()
    recent_cmp: Dict[str, Any] = {}
    global PENDING_OUTCOMES
    for item in reversed(PENDING_OUTCOMES):
        if item.get("ticker") == ticker.upper():
            recent_cmp = item.get("comparison") or {}
            break

    tr = trade_rec or {}
    outcome_row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "trade_outcome",
        "ticker": ticker.upper(),
        "outcome": "win" if win else "loss",
        "outcome_pnl": round(float(pnl), 2),
        "win": win,
        "comparison": recent_cmp,
    }
    if tr:
        outcome_row.update({
            "pnl_pct": tr.get("pnl_pct"),
            "peak_pct": tr.get("peak_pct"),
            "exit_reason": str(tr.get("exit_reason", ""))[:200],
            "hold_sec": tr.get("hold_sec"),
            "regime": tr.get("regime"),
        })
    _append(COEVOLUTION_LOG, outcome_row)

    # Label recent correction rows with trade outcome
    if recent_cmp:
        cf = recent_cmp.get("correction_for")
        if cf in ("ppo", "halim"):
            _append(COEVOLUTION_LOG, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "outcome_labels_correction",
                "ticker": ticker.upper(),
                "post_trade_correction_for": cf,
                "market_proved": "win" if win else "loss",
                "outcome_pnl": round(float(pnl), 2),
            })

    try:
        from core.halim_ppo_dialogue import schedule_trade_outcome_dialogue
        runner = None
        try:
            from core.halim_runtime import get_halim_runtime
            rt = get_halim_runtime()
            if rt:
                runner = getattr(rt, "_runner", None)
        except Exception:
            pass
        schedule_trade_outcome_dialogue(
            ticker=ticker,
            pnl=pnl,
            win=win,
            cfg=cfg,
            runner=runner,
            recent_comparison=recent_cmp,
        )
    except Exception as exc:
        log.debug(f"Trade outcome dialogue: {exc}")


def _coevolution_event_key(ev: Dict[str, Any]) -> str:
    return f"{ev.get('timestamp')}|{ev.get('ticker')}|{ev.get('task')}"


def _coevolution_row_key(row: Dict[str, Any]) -> str:
    return "|".join(
        str(row.get(k, ""))[:200]
        for k in ("capability", "instruction", "input", "output", "source")
    )


def _coevolution_quality_ok(ev: Dict[str, Any], cmp: Dict[str, Any]) -> bool:
    """Skip empty/noise rows that waste SFT capacity."""
    halim_reason = str(ev.get("halim_reason") or "").strip()
    ppo_reason = str(ev.get("ppo_reason") or "").strip()
    task = str(ev.get("task") or "")
    if task in ("None", ""):
        return False
    if len(halim_reason) < 4 and not ppo_reason and ev.get("outcome") is None:
        return False
    if cmp.get("ppo_signal") is None and cmp.get("halim_signal") is None:
        return False
    return True


def _rows_from_coevolution_event(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    cmp = ev.get("comparison") or {}
    if not _coevolution_quality_ok(ev, cmp):
        return []

    ticker = str(ev.get("ticker") or "").upper()
    task = str(ev.get("task") or "entry_decision")
    outcome = ev.get("outcome")
    outcome_pnl = ev.get("outcome_pnl")
    outcome_bit = ""
    if outcome is not None:
        outcome_bit = f" outcome={outcome}"
        if outcome_pnl is not None:
            outcome_bit += f" pnl={outcome_pnl}"

    user_input = (
        f"{ticker} {task}\n"
        f"PPO signal={cmp.get('ppo_signal')} conf={cmp.get('ppo_conf')} "
        f"reason={str(ev.get('ppo_reason') or '')[:120]}\n"
        f"Pipeline: {ev.get('pipeline', '')}"
    )
    halim_out = (
        f"Halim({ev.get('halim_source')}): {str(ev.get('halim_reason') or '')[:240]} | "
        f"agree={cmp.get('ppo_halim_agree')}{outcome_bit}"
    )

    rows: List[Dict[str, Any]] = [{
        "capability": "decision_text",
        "instruction": f"Trade {task} — reconcile PPO reflex with Halim mind.",
        "input": user_input,
        "output": halim_out,
        "source": "coevolution_halim",
        "timestamp": ev.get("timestamp"),
        "ticker": ticker,
        "task": task,
    }]

    correction_for = ev.get("post_trade_correction_for") or cmp.get("correction_for")
    if correction_for == "ppo" or (
        cmp.get("ppo_halim_agree") is False and cmp.get("halim_exec_agree") is True
    ):
        rows.append({
            "capability": "trade_reflex",
            "instruction": "PPO reflex — learn from Halim correction after trade context.",
            "input": user_input,
            "output": (
                f"Better action: {cmp.get('halim_signal')} — "
                f"{str(ev.get('halim_reason') or '')[:240]}{outcome_bit}"
            ),
            "source": "coevolution_ppo",
            "timestamp": ev.get("timestamp"),
            "ticker": ticker,
            "task": task,
        })
    return rows


def export_coevolution_gold(*, max_records: int = 20_000) -> Dict[str, Any]:
    """Rewrite deduped co-evolution gold from correction_log (idempotent)."""
    COEVOLUTION_GOLD.parent.mkdir(parents=True, exist_ok=True)
    if not COEVOLUTION_LOG.is_file():
        if COEVOLUTION_GOLD.is_file():
            COEVOLUTION_GOLD.unlink()
        return {"ok": True, "added": 0, "total": 0, "rewritten": True}

    seen_events: set = set()
    seen_rows: set = set()
    out_rows: List[Dict[str, Any]] = []

    with open(COEVOLUTION_LOG, encoding="utf-8") as fh:
        lines = fh.readlines()[-max_records:]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        ekey = _coevolution_event_key(ev)
        if ekey in seen_events:
            continue
        seen_events.add(ekey)

        for row in _rows_from_coevolution_event(ev):
            rkey = _coevolution_row_key(row)
            if rkey in seen_rows:
                continue
            seen_rows.add(rkey)
            out_rows.append(row)

    tmp = COEVOLUTION_GOLD.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in out_rows:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    tmp.replace(COEVOLUTION_GOLD)

    return {
        "ok": True,
        "added": len(out_rows),
        "total": len(out_rows),
        "events": len(seen_events),
        "rewritten": True,
    }


def coevolution_stats() -> Dict[str, Any]:
    agree = disagree = corrections_ppo = corrections_halim = 0
    if COEVOLUTION_LOG.is_file():
        try:
            with open(COEVOLUTION_LOG, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                        cmp = ev.get("comparison") or {}
                        if cmp.get("ppo_halim_agree") is True:
                            agree += 1
                        elif cmp.get("ppo_halim_agree") is False:
                            disagree += 1
                        cf = ev.get("post_trade_correction_for") or cmp.get("correction_for")
                        if cf == "ppo":
                            corrections_ppo += 1
                        elif cf == "halim":
                            corrections_halim += 1
                    except Exception:
                        continue
        except Exception:
            pass
    return {
        "agreements": agree,
        "disagreements": disagree,
        "corrections_for_ppo": corrections_ppo,
        "corrections_for_halim": corrections_halim,
        "log_path": str(COEVOLUTION_LOG),
    }


def run_coevolution_cycle(
    cfg: Optional[BotConfig] = None,
    *,
    model: Any = None,
    trigger: str = "session_end",
) -> Dict[str, Any]:
    """End-of-session mutual evolution — export gold, update state, log summary."""
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return {"skipped": True, "reason": "disabled"}

    log.info("  🔄 PPO ↔ Halim co-evolution — mutual learning cycle…")
    export = export_coevolution_gold()
    try:
        from core.halim_ppo_dialogue import export_dialogue_gold
        dialogue_export = export_dialogue_gold()
    except Exception as exc:
        dialogue_export = {"ok": False, "error": str(exc)[:80]}
    stats = coevolution_stats()

    try:
        from core.halim_action_learn import export_action_gold
        export_action_gold(include_learn_cache=True)
    except Exception:
        pass

    state = _load_state()
    state["last_cycle"] = datetime.now(timezone.utc).isoformat()
    state["trigger"] = trigger
    state["stats"] = stats
    state["export"] = export
    state["dialogue_export"] = dialogue_export
    state["cycles"] = int(state.get("cycles", 0)) + 1
    _save_state(state)

    try:
        from core.halim_registry import append_registry
        append_registry("ppo_halim_coevolution", {**stats, "export": export, "trigger": trigger})
    except Exception:
        pass

    log.info(
        f"  🔄 Co-evolution: agree={stats['agreements']} disagree={stats['disagreements']} "
        f"→ correct PPO={stats['corrections_for_ppo']} Halim={stats['corrections_for_halim']} "
        f"| gold+{export.get('added', 0)}"
    )
    return {"ok": True, "stats": stats, "export": export}


def log_coevolution_banner(cfg: Optional[BotConfig] = None) -> None:
    if not _enabled(cfg):
        return
    st = coevolution_stats()
    dlg = 0
    try:
        dlg_path = Path("halim/data/coevolution/dialogue.jsonl")
        if dlg_path.is_file():
            with open(dlg_path, encoding="utf-8") as fh:
                dlg = sum(1 for _ in fh)
    except Exception:
        pass
    if st["agreements"] + st["disagreements"] > 0:
        log.info(
            f"  PPO↔Halim co-evolution: {st['agreements']} agree, "
            f"{st['disagreements']} correct-each-other · {dlg} dialogues"
        )
    else:
        log.info(
            "  PPO↔Halim co-evolution: active — generative dialogue on every trade decision"
        )
