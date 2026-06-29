#!/usr/bin/env python3
"""
core/learning_coordinator.py — Smart gate for heavy learning (memory + CPU).

One heavy job at a time, coalesced triggers, defer during RTH unless memory OK.
Light telemetry (buffer append, journals) stays synchronous on the hot path.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

_lock = threading.Lock()
_in_flight = False
_pending: Optional[Dict[str, Any]] = None
_last_heavy_ts = 0.0
_last_ppo_save_ts = 0.0
_last_weight_ts = 0.0
_last_async_ppo_ts = 0.0
_async_ppo_running = False
_trades_since_coalesce = 0
_append_counter = 0


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def memory_pressure_high(cfg: Optional[BotConfig] = None) -> bool:
    """True when system RAM is tight — skip heavy learning."""
    try:
        import psutil
        pct = psutil.virtual_memory().percent
        limit = float(os.getenv("LEARNING_MEMORY_MAX_PCT", "80"))
        return pct >= limit
    except ImportError:
        return False
    except Exception:
        return False


def should_defer_heavy_learning(cfg: Optional[BotConfig] = None) -> bool:
    """During RTH/pre-market defer PPO train / distill / gold export bursts."""
    if not _env_bool("LEARNING_DEFER_DURING_RTH", "true"):
        return False
    if memory_pressure_high(cfg):
        return True
    try:
        from core.market_hours import get_market_state
        cfg = cfg or BotConfig()
        return get_market_state(cfg) in ("open", "pre_market")
    except Exception:
        return False


def is_replay_session() -> bool:
    return os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")


def should_queue_only_learning(cfg: Optional[BotConfig] = None) -> bool:
    """
    Hot path: capture records only — no SB3 learn, no weight recompute bursts.
    Replay / explicit flag / memory pressure only. Live RTH uses bounded async learning
    via maybe_live_light_learning() and defers heavy batch via should_defer_heavy_learning().
    """
    if _env_bool("LEARNING_QUEUE_ONLY", "false"):
        return True
    if is_replay_session():
        return True
    if memory_pressure_high(cfg):
        return True
    return False


def heavy_learning_min_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    base = float(os.getenv("LEARNING_HEAVY_MIN_INTERVAL_SEC", "900"))
    if should_defer_heavy_learning(cfg):
        return max(base, float(os.getenv("LEARNING_DEFERRED_MIN_INTERVAL_SEC", "1800")))
    return base


def heavy_learning_steps_scale(cfg: Optional[BotConfig] = None) -> float:
    """Scale PPO micro-steps down during deferred / memory-tight periods."""
    if memory_pressure_high(cfg):
        return float(os.getenv("LEARNING_MEMORY_STEP_SCALE", "0.25"))
    if should_defer_heavy_learning(cfg):
        return float(os.getenv("LEARNING_DEFERRED_STEP_SCALE", "0.35"))
    return 1.0


def should_save_ppo_now(cfg: Optional[BotConfig] = None, *, force: bool = False) -> bool:
    global _last_ppo_save_ts
    if force or not _env_bool("LEARNING_DEBOUNCE_PPO_SAVE", "true"):
        return True
    min_gap = float(os.getenv("LEARNING_PPO_SAVE_MIN_SEC", "420"))
    if should_defer_heavy_learning(cfg):
        min_gap = max(min_gap, float(os.getenv("LEARNING_PPO_SAVE_DEFERRED_SEC", "900")))
    now = time.time()
    if now - _last_ppo_save_ts >= min_gap:
        _last_ppo_save_ts = now
        return True
    return False


def note_ppo_saved() -> None:
    global _last_ppo_save_ts
    _last_ppo_save_ts = time.time()


def maybe_trim_experience_buffer() -> None:
    """
    Hot-path hook after buffer append — does NOT trim during RTH.
    Disk trim runs off-hours via trim_experience_buffer_off_hours().
    """
    global _append_counter
    _append_counter += 1


def trim_experience_buffer_off_hours(cfg: Optional[BotConfig] = None) -> None:
    """Bounded jsonl retention — only when market closed and not memory-tight."""
    if _env_bool("LEARNING_BUFFER_TRIM_ENABLED", "true") is False:
        return
    if should_defer_heavy_learning(cfg):
        return
    if memory_pressure_high(cfg):
        return
    try:
        from core.local_cleanup import _trim_jsonl
        _trim_jsonl(
            "models/experience_buffer.jsonl",
            int(os.getenv("EXPERIENCE_BUFFER_MAX_LINES", "5000")),
        )
    except Exception as exc:
        log.debug(f"Experience buffer trim: {exc}")


def live_micro_ppo_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """Background async micro-PPO during live session — on by default for live."""
    if should_queue_only_learning(cfg):
        return False
    return _env_bool("LEARNING_LIVE_MICRO_PPO", "true")


def allow_live_micro_ppo(cfg: Optional[BotConfig] = None) -> bool:
    """Inline live micro-train when RAM is OK — disabled during queue-only sessions."""
    if should_queue_only_learning(cfg):
        return False
    if not live_micro_ppo_enabled(cfg):
        return False
    return not memory_pressure_high(cfg)


def live_micro_ppo_steps(cfg: Optional[BotConfig] = None, requested: Optional[int] = None) -> int:
    """Small step budget for rare inline PPO — keeps hot path bounded."""
    cfg = cfg or BotConfig()
    base = requested or int(getattr(cfg, "PPO_ENTRY_MICRO_STEPS", 512))
    cap = int(os.getenv("PPO_LIVE_MICRO_STEPS_MAX", "64"))
    floor = int(os.getenv("PPO_LIVE_MICRO_STEPS_MIN", "32"))
    scale = float(os.getenv("LEARNING_LIVE_STEP_SCALE", "0.15"))
    return min(cap, max(floor, int(base * scale)))


def learning_mode_label(cfg: Optional[BotConfig] = None) -> str:
    """Human-readable learning mode for startup banners."""
    cfg = cfg or BotConfig()
    if is_replay_session():
        teardown = os.getenv("REPLAY_TRAINING_ENABLED", "true")
        return f"queue-only capture | teardown train={teardown}"
    if should_queue_only_learning(cfg):
        return "queue-only capture"
    if memory_pressure_high(cfg):
        return "capture only (memory tight)"
    parts = ["bounded live learning"]
    if live_micro_ppo_enabled(cfg):
        parts.append("async micro-PPO")
    if should_defer_heavy_learning(cfg):
        parts.append("heavy deferred until off-hours")
    else:
        parts.append("full batch OK")
    return " | ".join(parts)


def maybe_update_weights_debounced(cfg: Optional[BotConfig] = None) -> bool:
    """Live-only debounced weight nudge from experience buffer."""
    global _last_weight_ts
    cfg = cfg or BotConfig()
    if is_replay_session() or should_queue_only_learning(cfg):
        return False
    every_n = int(os.getenv("LEARNING_LIVE_WEIGHT_EVERY_N_TRADES", "3"))
    if every_n <= 0:
        return False
    if _trades_since_coalesce % every_n != 0:
        return False
    min_sec = float(os.getenv("LEARNING_LIVE_WEIGHT_MIN_SEC", "180"))
    now = time.time()
    if now - _last_weight_ts < min_sec:
        return False
    try:
        from core.online_trainer import _update_weights_from_buffer
        _update_weights_from_buffer()
        _last_weight_ts = now
        return True
    except Exception as exc:
        log.debug(f"Live weight nudge: {exc}")
        return False


def schedule_async_live_ppo_if_due(
    cfg: Optional[BotConfig] = None,
    model: Any = None,
) -> bool:
    """Fire one background micro-PPO pass when interval + RAM allow (live only)."""
    global _async_ppo_running, _last_async_ppo_ts
    cfg = cfg or BotConfig()
    if not allow_live_micro_ppo(cfg):
        return False
    min_sec = float(os.getenv("LEARNING_ASYNC_PPO_INTERVAL_SEC", "600"))
    if time.time() - _last_async_ppo_ts < min_sec:
        return False
    with _lock:
        if _async_ppo_running:
            return False
        _async_ppo_running = True

    def _run() -> None:
        global _async_ppo_running, _last_async_ppo_ts
        try:
            if not allow_live_micro_ppo(cfg):
                return
            m = model
            if m is None:
                from core.ppo_entry_learning import get_ppo_model
                m = get_ppo_model()
            if m is None:
                return
            from core.ppo_reward_trainer import run_reward_linked_ppo_train
            steps = live_micro_ppo_steps(cfg)
            if run_reward_linked_ppo_train(cfg, model=m, steps=steps, force=False):
                _last_async_ppo_ts = time.time()
                log.info(f"🧠 Live async micro-PPO done ({steps} steps, background)")
        except Exception as exc:
            log.debug(f"Live async micro-PPO: {exc}")
        finally:
            with _lock:
                _async_ppo_running = False

    try:
        from core.async_utils import get_background_worker
        get_background_worker()._executor.submit(_run)
    except Exception:
        threading.Thread(target=_run, name="live-async-ppo", daemon=True).start()
    return True


def maybe_live_light_learning(
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
) -> None:
    """Live session: debounced weights + optional async micro-PPO — never sync SB3."""
    cfg = cfg or BotConfig()
    if is_replay_session() or should_queue_only_learning(cfg):
        return
    maybe_update_weights_debounced(cfg)
    model = getattr(runner, "model", None) if runner else None
    schedule_async_live_ppo_if_due(cfg, model)


def _can_run_heavy_now(cfg: BotConfig) -> bool:
    if memory_pressure_high(cfg):
        return False
    gap = heavy_learning_min_interval_sec(cfg)
    return (time.time() - _last_heavy_ts) >= gap


def _run_heavy_batch(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"],
    *,
    force: bool = False,
) -> None:
    global _in_flight, _last_heavy_ts, _pending, _trades_since_coalesce
    model = getattr(runner, "model", None) if runner else None
    if model is None:
        try:
            from core.ppo_entry_learning import get_ppo_model
            model = get_ppo_model()
        except Exception:
            model = None
    autopilot = getattr(runner, "autopilot", None) if runner else None
    consciousness = getattr(runner, "consciousness", None) if runner else None
    trades_today = int(getattr(runner, "trades_today", 0) or 0) if runner else 0

    try:
        from core.ppo_reward_trainer import run_reward_linked_ppo_train
        if force:
            run_reward_linked_ppo_train(cfg, model=model, force=True)
        elif not should_queue_only_learning(cfg) and not should_defer_heavy_learning(cfg):
            run_reward_linked_ppo_train(cfg, model=model, force=False)
        else:
            log.debug("Coordinated reward PPO queued — defer / queue-only (teardown/flush trains)")
    except Exception as exc:
        log.debug(f"Coordinated reward PPO: {exc}")

    if (should_queue_only_learning(cfg) or should_defer_heavy_learning(cfg)) and not force:
        log.debug("Coordinated heavy batch skipped — queue-only or deferred RTH")
        _last_heavy_ts = time.time()
        _trades_since_coalesce = 0
        with _lock:
            _pending = None
        return

    try:
        from core.ppo_teacher_training import maybe_run_ppo_teacher_training
        maybe_run_ppo_teacher_training(
            cfg,
            model=model,
            trigger="coordinated_close",
            autopilot=autopilot,
            consciousness=consciousness,
        )
    except Exception as exc:
        log.debug(f"Coordinated PPO teacher: {exc}")

    try:
        from core.pilot_mode import maybe_incremental_train
        maybe_incremental_train(cfg, trades_today, consciousness, autopilot)
    except Exception as exc:
        log.debug(f"Coordinated incremental train: {exc}")

    try:
        from core.hybrid_distiller import maybe_run_hybrid_distillation
        maybe_run_hybrid_distillation(cfg)
    except Exception as exc:
        log.debug(f"Coordinated hybrid distill: {exc}")

    if runner is not None:
        try:
            runner._daily_self_train()
        except Exception as exc:
            log.debug(f"Coordinated self train: {exc}")

    try:
        trim_experience_buffer_off_hours(cfg)
    except Exception as exc:
        log.debug(f"Coordinated buffer trim: {exc}")

    _last_heavy_ts = time.time()
    _trades_since_coalesce = 0
    with _lock:
        _pending = None


def _worker(cfg: BotConfig, runner: Optional["ScalperRunner"]) -> None:
    global _in_flight
    try:
        if not _can_run_heavy_now(cfg):
            log.debug("Heavy learning deferred — min interval or memory pressure")
            return
        log.info("🧠 Learning coordinator — running batched heavy updates (single pass)")
        _run_heavy_batch(cfg, runner)
    finally:
        with _lock:
            _in_flight = False


def schedule_post_close_learning(cfg: BotConfig, runner: Optional["ScalperRunner"]) -> None:
    """
    Call once per trade close. Coalesces teacher + incremental + hybrid + weights
    into one background pass (or queues for flush_pending_learning).
    """
    global _in_flight, _pending, _trades_since_coalesce
    _trades_since_coalesce += 1

    if not is_replay_session():
        maybe_live_light_learning(cfg, runner)

    if should_queue_only_learning(cfg):
        with _lock:
            _pending = {"cfg": cfg, "runner": runner, "queued_at": time.time()}
        return

    if should_defer_heavy_learning(cfg):
        with _lock:
            _pending = {"cfg": cfg, "runner": runner, "queued_at": time.time()}
        return

    min_trades = int(os.getenv("LEARNING_HEAVY_EVERY_N_TRADES", "4"))
    if _trades_since_coalesce < min_trades and _can_run_heavy_now(cfg) is False:
        return
    if _trades_since_coalesce < min_trades:
        return

    with _lock:
        if _in_flight:
            _pending = {"cfg": cfg, "runner": runner, "queued_at": time.time()}
            return
        _in_flight = True

    try:
        from core.async_utils import get_background_worker
        get_background_worker()._executor.submit(_worker, cfg, runner)
    except Exception:
        try:
            _worker(cfg, runner)
        except Exception as exc:
            log.debug(f"Heavy learning worker: {exc}")
            with _lock:
                _in_flight = False


def flush_pending_learning(
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
    *,
    force: bool = False,
) -> None:
    """Session end / off-hours — drain queued heavy learning once."""
    global _in_flight, _pending
    cfg = cfg or BotConfig()
    if memory_pressure_high(cfg) and not force:
        log.info("🧠 Learning flush skipped — memory pressure")
        return
    with _lock:
        if _pending and runner is None:
            runner = _pending.get("runner")
        if _in_flight:
            return
        _in_flight = True
    try:
        if force or not should_defer_heavy_learning(cfg) or _pending:
            _run_heavy_batch(cfg, runner, force=force)
        else:
            log.debug("Learning flush deferred — still in RTH")
    finally:
        with _lock:
            _in_flight = False


def run_if_allowed(
    kind: str,
    fn: Callable[[], Any],
    cfg: Optional[BotConfig] = None,
    *,
    heavy: bool = True,
) -> Any:
    """Optional wrapper for one-off heavy calls."""
    cfg = cfg or BotConfig()
    if heavy and (should_defer_heavy_learning(cfg) or memory_pressure_high(cfg)):
        log.debug(f"Learning {kind} deferred")
        return None
    if heavy and not _can_run_heavy_now(cfg):
        return None
    return fn()
