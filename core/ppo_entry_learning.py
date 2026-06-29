#!/usr/bin/env python3
"""
core/ppo_entry_learning.py — PPO self-evaluation on every entry + all council responses.

Every entry fill records PPO state, features, spike/micro context, and council
snapshots. When late Ollama answers arrive, rewards are reshaped and PPO gets a
micro-update so it improves from analysis — not only from closed trades.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from core.config import BotConfig
from core.notify import log
from core.reward_shaping import shaped_reward

if TYPE_CHECKING:
    pass

LEDGER_PATH = Path("models/ppo_entry_ledger.jsonl")
_pending: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
_model_ref: Any = None
_micro_queue: List[Dict[str, Any]] = []
_micro_lock = threading.Lock()
_micro_timer: Optional[threading.Timer] = None
_micro_running = False


def set_ppo_model(model: Any) -> None:
    global _model_ref
    _model_ref = model


def get_ppo_model() -> Any:
    return _model_ref


def _feat_vector(raw: Any, cfg: BotConfig) -> Optional[np.ndarray]:
    if not raw:
        return None
    n_feat = int(getattr(cfg, "N_FEATURES", 18))
    arr = np.array(raw, dtype=np.float32).flatten()
    if arr.size == n_feat:
        return arr
    if arr.size >= n_feat:
        return arr[-n_feat:]
    return None


def ppo_learn_every_entry(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "PPO_LEARN_EVERY_ENTRY", True))


def _append_ledger(row: Dict[str, Any]) -> None:
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _entry_reward_at_fill(
    cfg: BotConfig,
    *,
    ppo_action: int,
    ppo_conf: float,
    entered: bool,
    spike_ratio: float,
    scan_score: float,
    slippage_pct: float = 0.0,
) -> float:
    """Interim reward at entry — refined when council + exit arrive."""
    base = 0.0
    if entered and ppo_action == 1:
        base = 0.12 + min(ppo_conf, 0.85) * 0.15
        if spike_ratio >= 1.15:
            base += 0.08
        if scan_score >= 60:
            base += min(scan_score / 500.0, 0.12)
    elif entered and ppo_action != 1:
        # PPO said HOLD but spike/scanner overrode — penalize, do not reinforce
        base = float(os.getenv("PPO_OVERRIDE_ENTRY_REWARD", "-0.15"))
    elif not entered and ppo_action == 1:
        base = -0.08
    return shaped_reward(
        cfg, base, event="ppo_entry",
        spike_ratio=spike_ratio, slippage_pct=slippage_pct,
    )


def _council_adjusted_reward(
    cfg: BotConfig,
    prev: float,
    *,
    ppo_agrees: bool,
    ollama_agrees: bool,
    ppo_conf: float,
    ollama_conf: float,
) -> float:
    r = float(prev)
    if ppo_agrees and not ollama_agrees:
        r += 0.18 * float(getattr(cfg, "PPO_LEARNING_WEIGHT", 1.5)) * 0.1
    elif ppo_agrees and ollama_agrees:
        r += 0.10
    elif not ppo_agrees and ollama_agrees:
        r -= 0.12
    elif not ppo_agrees and not ollama_agrees:
        r -= 0.04
    r += (ppo_conf - 0.5) * 0.08
    r += (ollama_conf - 0.5) * 0.04
    return round(r, 4)


def record_ppo_entry(
    cfg: BotConfig,
    *,
    ticker: str,
    entry_price: float,
    shares: int,
    features: Optional[List[float]],
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    ppo_reason: str = "",
    council_decision: Optional[Dict[str, Any]] = None,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    micro_forecast: Optional[Dict[str, Any]] = None,
    slippage_pct: float = 0.0,
    regime: str = "",
) -> str:
    """
    Register entry for PPO evaluation — returns entry_id for council linkage.
    """
    entry_id = f"{ticker.upper()}|{int(time.time() * 1000)}|{uuid.uuid4().hex[:8]}"
    entered = True
    reward = _entry_reward_at_fill(
        cfg,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        entered=entered,
        spike_ratio=spike_ratio,
        scan_score=scan_score,
        slippage_pct=slippage_pct,
    )
    council = council_decision or {}
    row = {
        "source": "ppo_entry",
        "event": "entry_fill",
        "entry_id": entry_id,
        "ticker": ticker.upper(),
        "action": "BUY",
        "entry_price": round(float(entry_price), 6),
        "shares": int(shares),
        "ppo_action": int(ppo_action),
        "ppo_conf": round(float(ppo_conf), 4),
        "ppo_reason": str(ppo_reason or "")[:200],
        "pipeline": str(council.get("pipeline", "")),
        "council_decision": council,
        "spike_ratio": float(spike_ratio),
        "scan_score": float(scan_score),
        "micro_forecast": micro_forecast or {},
        "slippage_pct": round(float(slippage_pct), 6),
        "regime": regime,
        "features": features or [],
        "reward": reward,
        "reward_stage": "entry_fill",
        "ollama_attached": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from core.experience_buffer import append as buffer_append
        buffer_append(row)
    except Exception:
        pass
    _append_ledger(row)
    with _lock:
        _pending[entry_id] = dict(row)
        if len(_pending) > 200:
            oldest = sorted(_pending.keys())[:50]
            for k in oldest:
                _pending.pop(k, None)
    log.debug(
        f"  🧠 PPO entry eval {ticker}: action={ppo_action} conf={ppo_conf:.0%} "
        f"reward={reward:+.3f} id={entry_id[-12:]}"
    )
    return entry_id


def attach_council_to_entry(
    cfg: BotConfig,
    *,
    ticker: str,
    task: str,
    executed: Dict[str, Any],
    ollama_parsed: Dict[str, Any],
    ppo_signal: Any,
    ppo_conf: float,
    ppo_reason: str = "",
    latency_ms: float = 0.0,
    entry_id: Optional[str] = None,
) -> Optional[str]:
    """Merge late Ollama / council analysis into the PPO entry evaluation."""
    ticker_u = ticker.upper()
    entry_rec = None
    with _lock:
        if entry_id and entry_id in _pending:
            entry_rec = _pending.get(entry_id)
        else:
            for eid, rec in reversed(list(_pending.items())):
                if rec.get("ticker") == ticker_u and not rec.get("ollama_attached"):
                    entry_rec = rec
                    entry_id = eid
                    break

    prev_reward = float((entry_rec or {}).get("reward", 0.0))
    if task == "entry_decision":
        executed_enter = bool(executed.get("enter", True))
        ollama_enter = bool(ollama_parsed.get("enter", False))
        ppo_buy = int(ppo_signal or 0) == 1
        ppo_agrees = (ppo_buy and executed_enter) or (not ppo_buy and not executed_enter)
        ollama_agrees = executed_enter == ollama_enter
    elif task == "exit_decision":
        executed_enter = bool(executed.get("exit", True))
        ollama_enter = bool(ollama_parsed.get("exit", False))
        ppo_agrees = bool(ppo_signal) == executed_enter
        ollama_agrees = executed_enter == ollama_enter
    else:
        ppo_agrees = ollama_agrees = False

    ollama_conf = float(ollama_parsed.get("confidence", 0) or 0)
    reward = _council_adjusted_reward(
        cfg, prev_reward,
        ppo_agrees=ppo_agrees,
        ollama_agrees=ollama_agrees,
        ppo_conf=float(ppo_conf),
        ollama_conf=ollama_conf,
    )

    row = {
        "source": "ppo_entry_eval",
        "event": "council_attached",
        "entry_id": entry_id or f"{ticker_u}|orphan",
        "ticker": ticker_u,
        "task": task,
        "executed": executed,
        "ollama_parsed": ollama_parsed,
        "ppo_signal": ppo_signal,
        "ppo_conf": round(float(ppo_conf), 4),
        "ppo_reason": str(ppo_reason or "")[:200],
        "ppo_agrees_with_execute": ppo_agrees,
        "ollama_agrees_with_execute": ollama_agrees,
        "late_latency_ms": float(latency_ms),
        "reward": reward,
        "reward_delta": round(reward - prev_reward, 4),
        "reward_stage": "council_attached",
        "features": (entry_rec or {}).get("features", []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from core.experience_buffer import append as buffer_append
        buffer_append(row)
    except Exception:
        pass
    _append_ledger(row)

    if entry_rec and entry_id:
        entry_rec["ollama_attached"] = True
        entry_rec["reward"] = reward
        entry_rec["ollama_parsed"] = ollama_parsed
        entry_rec["council_attached_at"] = row["timestamp"]
        with _lock:
            _pending[entry_id] = entry_rec

    log.info(
        f"  🧠 PPO eval {ticker}/{task}: reward {prev_reward:+.3f}→{reward:+.3f} | "
        f"PPO agree={ppo_agrees} Ollama agree={ollama_agrees}"
    )
    return entry_id


def ppo_entry_micro_async() -> bool:
    return os.getenv("PPO_ENTRY_MICRO_ASYNC", "false").lower() in ("1", "true", "yes")


def ppo_entry_micro_debounce_sec() -> float:
    raw = os.getenv("PPO_ENTRY_MICRO_DEBOUNCE_SEC", "0")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _run_queued_micro_improve(cfg: BotConfig, model: Any) -> None:
    global _micro_running
    with _micro_lock:
        if _micro_running:
            return
        batch = list(_micro_queue)
        _micro_queue.clear()
        _micro_running = True
    try:
        if batch:
            from core.ppo_reward_trainer import defer_reward_records
            defer_reward_records(batch)
            try:
                from core.learning_coordinator import (
                    is_replay_session,
                    schedule_async_live_ppo_if_due,
                )
                if not is_replay_session():
                    schedule_async_live_ppo_if_due(cfg, model)
            except Exception:
                pass
    finally:
        with _micro_lock:
            _micro_running = False
            pending = bool(_micro_queue)
        if pending:
            _fire_micro_timer(cfg, model)


def _schedule_micro_improve(cfg: BotConfig, model: Any, records: List[Dict[str, Any]]) -> None:
    """Queue records; live fires debounced async micro-PPO — never sync SB3 on hot path."""
    global _micro_timer
    steps = int(getattr(cfg, "PPO_ENTRY_MICRO_STEPS", 512))
    if steps <= 0 or model is None or not records:
        return
    try:
        from core.learning_coordinator import should_queue_only_learning
        from core.ppo_reward_trainer import defer_reward_records
        defer_reward_records(records)
        if should_queue_only_learning(cfg):
            return
    except Exception:
        pass
    max_q = int(os.getenv("PPO_MICRO_QUEUE_MAX", "48"))
    with _micro_lock:
        _micro_queue.extend(records)
        if len(_micro_queue) > max_q:
            del _micro_queue[:-max_q]
    if not ppo_entry_micro_async():
        return
    debounce = ppo_entry_micro_debounce_sec()
    delay = debounce if debounce > 0 else 120.0
    with _micro_lock:
        if _micro_timer is not None:
            return
        _micro_timer = threading.Timer(
            delay,
            lambda: _fire_micro_timer(cfg, model),
        )
        _micro_timer.daemon = True
        _micro_timer.start()


def _fire_micro_timer(cfg: BotConfig, model: Any) -> None:
    global _micro_timer
    with _micro_lock:
        _micro_timer = None
    threading.Thread(
        target=_run_queued_micro_improve,
        args=(cfg, model),
        name="ppo-micro-improve",
        daemon=True,
    ).start()


def flush_pending_ppo_micro_learn(cfg: Optional[BotConfig] = None, model: Any = None) -> bool:
    """Drain queued entry micro-learns at session end (reward-linked PPO)."""
    global _micro_timer
    cfg = cfg or BotConfig()
    model = model or get_ppo_model()
    with _micro_lock:
        if _micro_timer is not None:
            _micro_timer.cancel()
            _micro_timer = None
        batch = list(_micro_queue)
        _micro_queue.clear()
    if model is None:
        return False
    try:
        from core.ppo_reward_trainer import run_reward_linked_ppo_train
        return run_reward_linked_ppo_train(
            cfg, model=model, extra_records=batch or None, force=True,
        )
    except Exception as exc:
        log.debug(f"PPO micro flush: {exc}")
        return False


def ppo_micro_improve(
    cfg: BotConfig,
    model: Any,
    records: List[Dict[str, Any]],
) -> bool:
    """Queue-only during session; full reward PPO only off-hours / teardown."""
    if not records:
        return False
    try:
        from core.learning_coordinator import should_queue_only_learning
        from core.ppo_reward_trainer import defer_reward_records, run_reward_linked_ppo_train

        defer_reward_records(records)
        if should_queue_only_learning(cfg):
            return False

        steps = int(getattr(cfg, "PPO_ENTRY_MICRO_STEPS", 512))
        if model is None:
            model = get_ppo_model()
        if model is None:
            return False
        return run_reward_linked_ppo_train(
            cfg, model=model, steps=steps, extra_records=records, force=False,
        )
    except Exception as exc:
        log.debug(f"PPO micro-improve: {exc}")
        return False


def evaluate_and_improve_ppo(
    cfg: BotConfig,
    model: Any = None,
    records: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Capture entry records + queue async micro-PPO — no sync weight/PPO on hot path."""
    if not ppo_learn_every_entry(cfg):
        return False
    recs = records or []
    if not recs:
        return False

    try:
        from core.learning_coordinator import should_queue_only_learning
        from core.ppo_reward_trainer import defer_reward_records
        defer_reward_records(recs)
        if should_queue_only_learning(cfg):
            return True
    except Exception:
        pass

    improved = False
    if model is not None:
        steps = int(getattr(cfg, "PPO_ENTRY_MICRO_STEPS", 512))
        if steps > 0:
            _schedule_micro_improve(cfg, model, recs)
            improved = True
    return improved


def on_entry_fill(
    cfg: BotConfig,
    *,
    ticker: str,
    entry_price: float,
    shares: int,
    features: Optional[List[float]],
    ai_commander: Any = None,
    council_decision: Optional[Dict[str, Any]] = None,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    micro_forecast: Optional[Dict[str, Any]] = None,
    slippage_pct: float = 0.0,
    regime: str = "",
    model: Any = None,
    obs: Any = None,
) -> str:
    """Full hook: record entry, snapshot PPO, trigger improve."""
    ppo_action, ppo_conf, ppo_reason = 0, 0.5, ""
    if ai_commander is not None:
        if obs is not None:
            ppo_action, ppo_conf, ppo_reason = ai_commander.ppo_action(
                obs, for_entry=True,
            )
        elif council_decision:
            ppo_action = int(council_decision.get("ppo_action", 0))
            ppo_conf = float(council_decision.get("ppo_conf", 0.5))
            ppo_reason = str(council_decision.get("ppo_reason", ""))

    entry_id = record_ppo_entry(
        cfg,
        ticker=ticker,
        entry_price=entry_price,
        shares=shares,
        features=features,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        ppo_reason=ppo_reason,
        council_decision=council_decision,
        spike_ratio=spike_ratio,
        scan_score=scan_score,
        micro_forecast=micro_forecast,
        slippage_pct=slippage_pct,
        regime=regime,
    )
    with _lock:
        if entry_id in _pending:
            if obs is not None:
                _pending[entry_id]["obs"] = (
                    obs.tolist() if hasattr(obs, "tolist") else list(obs)
                )
    rec = {
        "source": "ppo_entry",
        "entry_id": entry_id,
        "ticker": ticker,
        "features": features or [],
        "obs": (
            obs.tolist() if obs is not None and hasattr(obs, "tolist") else obs
        ),
        "reward": _entry_reward_at_fill(
            cfg, ppo_action=ppo_action, ppo_conf=ppo_conf, entered=True,
            spike_ratio=spike_ratio, scan_score=scan_score, slippage_pct=slippage_pct,
        ),
        "entry_price": entry_price,
    }
    evaluate_and_improve_ppo(cfg, model=model or get_ppo_model(), records=[rec])
    return entry_id


def on_council_response(
    cfg: BotConfig,
    *,
    ticker: str,
    task: str,
    executed: Dict[str, Any],
    ollama_parsed: Dict[str, Any],
    ppo_signal: Any,
    ppo_conf: float,
    ppo_reason: str = "",
    latency_ms: float = 0.0,
    model: Any = None,
) -> None:
    """Hook when deferred / late council answer arrives."""
    entry_id = attach_council_to_entry(
        cfg,
        ticker=ticker,
        task=task,
        executed=executed,
        ollama_parsed=ollama_parsed,
        ppo_signal=ppo_signal,
        ppo_conf=ppo_conf,
        ppo_reason=ppo_reason,
        latency_ms=latency_ms,
    )
    with _lock:
        rec = _pending.get(entry_id or "", {})
    evaluate_and_improve_ppo(
        cfg, model=model or get_ppo_model(), records=[rec] if rec else [],
    )
