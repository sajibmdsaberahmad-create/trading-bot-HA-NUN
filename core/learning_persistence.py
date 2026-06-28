#!/usr/bin/env python3
"""
core/learning_persistence.py — Protect learning artifacts from sudden shutdown.

Periodic fsync + light snapshots (PPO weights, cognitive state, Halim gold).
Crash marker on session start → recovery flush on next launch.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

RUNTIME_DIR = Path("runtime")
SESSION_MARKER = RUNTIME_DIR / "session.active"
SNAPSHOT_LOG = Path("models/learning_snapshots.jsonl")

_lock = threading.Lock()
_guard: Optional["LearningPersistenceGuard"] = None
_snapshot_count = 0

# Append-only / critical learning files — fsync to disk on each snapshot
CRITICAL_ARTIFACTS: Tuple[str, ...] = (
    "models/experience_buffer.jsonl",
    "models/commander_learning.jsonl",
    "models/ai_decision_log.jsonl",
    "models/fill_ledger.jsonl",
    "models/profit_hunt_ledger.jsonl",
    "models/ppo_entry_ledger.jsonl",
    "models/post_mortem_audit.jsonl",
    "models/owned_brain_journal.jsonl",
    "models/ppo_teacher_sessions.jsonl",
    "models/council_training_dataset.jsonl",
    "models/scalper_weights.json",
    "models/cognitive_state.json",
    "models/consciousness.json",
    "models/copilot_state.json",
    "models/owned_brain_state.json",
    "models/pilot_experience.json",
    "halim/data/actions/action_log.jsonl",
    "halim/data/coevolution/correction_log.jsonl",
    "halim/data/training/action_gold.jsonl",
    "halim/data/training/coevolution_gold.jsonl",
    "halim/data/training/dialogue_gold.jsonl",
)


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    return os.getenv("LEARNING_PERSISTENCE_ENABLED", "true").lower() in ("1", "true", "yes")


def _interval_sec(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    return float(
        os.getenv(
            "LEARNING_SNAPSHOT_INTERVAL_SEC",
            str(getattr(cfg, "LEARNING_SNAPSHOT_INTERVAL_SEC", 300.0)),
        )
    )


def fsync_path(path: Path) -> bool:
    """Force one file to disk (best-effort)."""
    if not path.is_file():
        return False
    try:
        with open(path, "rb") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except Exception:
        return False


def fsync_critical_artifacts() -> int:
    """Fsync known learning files — survives power loss of last ~snapshot interval."""
    n = 0
    for rel in CRITICAL_ARTIFACTS:
        if fsync_path(Path(rel)):
            n += 1
    return n


def _append_snapshot_log(row: Dict[str, Any]) -> None:
    try:
        SNAPSHOT_LOG.parent.mkdir(parents=True, exist_ok=True)
        row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        with open(SNAPSHOT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def snapshot_learning(
    cfg: Optional[BotConfig] = None,
    *,
    trigger: str = "periodic",
    model: Any = None,
    runner: Any = None,
    push_git: bool = False,
    halim_export: bool = False,
) -> Dict[str, Any]:
    """
    Light checkpoint — no full evolution (fast, safe mid-session).
    Full evolution remains on graceful shutdown only.
    """
    global _snapshot_count
    cfg = cfg or BotConfig()
    result: Dict[str, Any] = {"trigger": trigger, "ok": True, "steps": {}}

    if not _enabled(cfg):
        return {"skipped": True, "reason": "disabled"}

    try:
        result["steps"]["fsync"] = fsync_critical_artifacts()
    except Exception as exc:
        result["steps"]["fsync"] = 0
        result["steps"]["fsync_error"] = str(exc)[:80]

    # Cognitive / autopilot JSON state
    try:
        if runner is not None:
            ap = getattr(runner, "autopilot", None)
            core = getattr(ap, "core", None) if ap else None
            if core and hasattr(core, "_persist_state"):
                core._persist_state(push_git=False)
                result["steps"]["cognitive"] = True
    except Exception as exc:
        result["steps"]["cognitive"] = str(exc)[:60]

    # PPO weights — skip mid-session snapshots (teardown/flush saves)
    if model is not None and os.getenv("LEARNING_SNAPSHOT_SAVE_PPO", "true").lower() in (
        "1", "true", "yes",
    ):
        try:
            from core.learning_coordinator import should_queue_only_learning
            skip_ppo_snap = should_queue_only_learning(cfg)
        except Exception:
            skip_ppo_snap = False
        if not skip_ppo_snap:
            try:
                path = getattr(cfg, "MODEL_PATH", "ppo_trader.zip")
                model.save(path)
                fsync_path(Path(str(path)))
                fsync_path(Path(str(path) + ".zip"))  # SB3 may use either
                result["steps"]["ppo_saved"] = path
            except Exception as exc:
                result["steps"]["ppo_saved"] = False
                result["steps"]["ppo_error"] = str(exc)[:80]

    # Halim action gold — off-hours / explicit only (avoid RTH memory spikes)
    _snapshot_count += 1
    try:
        from core.learning_coordinator import should_defer_heavy_learning, memory_pressure_high
        defer = should_defer_heavy_learning(cfg) or memory_pressure_high(cfg)
    except Exception:
        defer = False
    do_halim = halim_export or (not defer and _snapshot_count % 3 == 0)
    if do_halim:
        try:
            from core.halim_gold_pipeline import export_halim_gold
            result["steps"]["halim_gold"] = export_halim_gold(include_learn_cache=True)
            replay = os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
            if replay and _snapshot_count % 6 == 0:
                from core.halim_gold_pipeline import run_halim_gold_pipeline
                result["steps"]["colab_package"] = run_halim_gold_pipeline(
                    cfg,
                    trigger=f"snapshot_{trigger}",
                    prepare_sft=True,
                    package_colab=True,
                )
        except Exception as exc:
            result["steps"]["halim_gold"] = {"ok": False, "error": str(exc)[:80]}

    if push_git:
        try:
            from core.git_sync import push_learning_checkpoint_async
            push_learning_checkpoint_async(f"snapshot_{trigger}", full_sync=False)
            result["steps"]["git_queued"] = True
        except Exception as exc:
            result["steps"]["git_queued"] = str(exc)[:60]

    _append_snapshot_log({
        "event": "learning_snapshot",
        "trigger": trigger,
        "fsync_files": result["steps"].get("fsync", 0),
        "ppo": bool(result["steps"].get("ppo_saved")),
    })
    return result


def mark_session_start(mode: str = "live", *, pid: Optional[int] = None) -> None:
    """Write crash-recovery marker (checked on next start)."""
    if not _enabled():
        return
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "pid": pid or os.getpid(),
        "mode": mode,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "replay": mode == "replay",
    }
    try:
        SESSION_MARKER.write_text(json.dumps(row, indent=2))
        with open(SESSION_MARKER, "rb") as fh:
            os.fsync(fh.fileno())
    except Exception as exc:
        log.debug(f"Session marker write: {exc}")


def mark_session_end() -> None:
    """Clear marker after graceful shutdown."""
    try:
        if SESSION_MARKER.is_file():
            SESSION_MARKER.unlink()
    except Exception:
        pass


def recover_previous_crash(cfg: Optional[BotConfig] = None) -> bool:
    """
    If last session ended abruptly, fsync + light export before trading.
    Called once at startup.
    """
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return False
    if not SESSION_MARKER.is_file():
        return False
    try:
        raw = json.loads(SESSION_MARKER.read_text())
    except Exception:
        raw = {}
    prev_pid = int(raw.get("pid", 0) or 0)
    if prev_pid and prev_pid == os.getpid():
        return False
    still_alive = False
    if prev_pid > 0:
        try:
            os.kill(prev_pid, 0)
            still_alive = True
        except OSError:
            still_alive = False
    if still_alive:
        return False

    mode = str(raw.get("mode", "live"))
    started = raw.get("started_at", "?")
    log.warning(
        f"⚠️  Previous {mode} session did not shut down cleanly (started {started}) — "
        "recovering learning artifacts…"
    )
    try:
        snapshot_learning(
            cfg,
            trigger="crash_recovery",
            halim_export=True,
            push_git=os.getenv("LEARNING_RECOVERY_GIT_PUSH", "true").lower() in ("1", "true", "yes"),
        )
    except Exception as exc:
        log.warning(f"Crash recovery snapshot: {exc}")
    try:
        from core.graceful_shutdown import flush_halim_data
        flush_halim_data(cfg, trigger="crash_recovery")
    except Exception as exc:
        log.debug(f"Crash recovery halim flush: {exc}")
    mark_session_end()
    log.info("✅ Crash recovery flush complete — session marker cleared")
    return True


class LearningPersistenceGuard:
    """Background periodic snapshots + atexit safety net."""

    def __init__(
        self,
        cfg: BotConfig,
        model_getter: Callable[[], Any],
        *,
        mode: str = "live",
        runner: Any = None,
    ):
        self.cfg = cfg
        self._model_getter = model_getter
        self.mode = mode
        self.runner = runner
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._atexit_registered = False

    def start(self) -> None:
        global _guard
        if not _enabled(self.cfg):
            return
        with _lock:
            if _guard is not None:
                return
            _guard = self
        mark_session_start(self.mode)
        if not self._atexit_registered:
            atexit.register(self._atexit_snapshot)
            self._atexit_registered = True
        self._thread = threading.Thread(
            target=self._loop,
            name="learning-persistence",
            daemon=True,
        )
        self._thread.start()
        log.info(
            f"💾 Learning persistence ON — snapshot every {_interval_sec(self.cfg):.0f}s "
            f"(crash-safe fsync + PPO save)"
        )

    def stop(self, *, trigger: str = "session_end") -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8.0)
        try:
            snapshot_learning(
                self.cfg,
                trigger=trigger,
                model=self._model_getter(),
                runner=self.runner,
                halim_export=True,
                push_git=False,
            )
        except Exception:
            pass
        mark_session_end()
        global _guard
        with _lock:
            _guard = None

    def snapshot_now(self, trigger: str = "manual") -> Dict[str, Any]:
        push = os.getenv("LEARNING_SNAPSHOT_GIT", "false").lower() in ("1", "true", "yes")
        return snapshot_learning(
            self.cfg,
            trigger=trigger,
            model=self._model_getter(),
            runner=self.runner,
            push_git=push,
        )

    def _atexit_snapshot(self) -> None:
        if self._stop.is_set():
            return
        try:
            snapshot_learning(
                self.cfg,
                trigger="atexit",
                model=self._model_getter(),
                runner=self.runner,
                halim_export=True,
                push_git=False,
            )
        except Exception:
            pass

    def _loop(self) -> None:
        iv = max(60.0, _interval_sec(self.cfg))
        git_every = float(os.getenv("LEARNING_SYNC_INTERVAL_SEC", "600"))
        last_git = time.time()
        while not self._stop.wait(timeout=iv):
            try:
                push_git = (time.time() - last_git) >= git_every
                snapshot_learning(
                    self.cfg,
                    trigger="periodic",
                    model=self._model_getter(),
                    runner=self.runner,
                    push_git=push_git,
                )
                if push_git:
                    last_git = time.time()
            except Exception as exc:
                log.debug(f"Learning snapshot: {exc}")


def start_learning_guard(
    cfg: BotConfig,
    model_getter: Callable[[], Any],
    *,
    mode: str = "live",
    runner: Any = None,
) -> Optional[LearningPersistenceGuard]:
    recover_previous_crash(cfg)
    guard = LearningPersistenceGuard(cfg, model_getter, mode=mode, runner=runner)
    guard.start()
    return guard


def emergency_snapshot(cfg: Optional[BotConfig] = None, model: Any = None, runner: Any = None) -> None:
    """Sync call from signal handler — must be fast."""
    try:
        snapshot_learning(cfg or BotConfig(), trigger="signal", model=model, runner=runner)
    except Exception:
        pass
