#!/usr/bin/env python3
"""
core/halim_runtime.py — Halim co-runtime with HANOON algo (same clock, trading first).

Halim lives on the same schedule as the scalper: RTH = profit hunting focus;
off-hours = learn, evolve, optional user tasks. Secondary abilities only when
operator requests or market is closed.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.market_hours import get_market_state, can_trade_now
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

JOURNAL_PATH = Path("models/halim_runtime.jsonl")
STATE_PATH = Path("models/halim_runtime_state.json")

_runtime: Optional["HalimRuntime"] = None


class HalimRuntime:
    """Halim mind running alongside HANOON body — trading is always priority 1."""

    MODES = ("trade_focus", "off_hours", "user_task")

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._runner: Optional["ScalperRunner"] = None
        self._last_tick = 0.0
        self._last_learn = 0.0
        self._last_dev = 0.0
        self._last_evolve = 0.0
        self._last_export = 0.0
        self._last_serve_watch = 0.0
        self._last_device_focus = 0.0
        self._mode = "trade_focus"
        self._user_task_pending = os.getenv("HALIM_USER_TASK", "").strip()
        self._tick_sec = float(os.getenv("HALIM_RUNTIME_TICK_SEC", "30"))
        self._serve_watch_sec = float(os.getenv("HALIM_SERVE_WATCHDOG_SEC", "30"))
        self._learn_interval = float(os.getenv("HALIM_OFF_HOURS_LEARN_SEC", "3600"))
        self._dev_interval = float(os.getenv("HALIM_OFF_HOURS_DEV_SEC", "7200"))
        self._export_interval = float(os.getenv("HALIM_OFF_HOURS_EXPORT_SEC", "7200"))
        self._auto_lm_interval = float(os.getenv("HALIM_AUTO_LM_CHECK_SEC", "10800"))

    def _save_state(self, extra: Optional[Dict[str, Any]] = None) -> None:
        row = {
            "mode": self._mode,
            "market_state": get_market_state(self.cfg),
            "can_trade": can_trade_now(self.cfg)[0],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **(extra or {}),
        }
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(row, indent=2))

    def _journal(self, event: str, detail: Dict[str, Any]) -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "mode": self._mode,
            **detail,
        }
        try:
            JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def resolve_mode(self) -> str:
        if self._user_task_pending:
            return "user_task"
        can_trade, _ = can_trade_now(self.cfg)
        market = get_market_state(self.cfg)
        if can_trade or market in ("open", "pre_market", "after_hours"):
            return "trade_focus"
        return "off_hours"

    def init(self) -> None:
        from core.halim_guardrails import (
            apply_operator_frontier_settings,
            ensure_constitution,
            log_guardrail_banner,
        )
        from core.halim_frontier_policy import (
            apply_frontier_policy_to_constitution,
            log_frontier_policy_banner,
        )
        from core.halim_identity import ensure_identity, log_halim_banner, apply_halim_native_mode

        apply_halim_native_mode(self.cfg)
        ensure_identity(self.cfg)
        ensure_constitution()
        apply_operator_frontier_settings(self.cfg)
        apply_frontier_policy_to_constitution(self.cfg)
        log_halim_banner(self.cfg)
        log_frontier_policy_banner(self.cfg)
        try:
            from core.halim_inference import log_inference_banner
            log_inference_banner(self.cfg)
        except Exception:
            pass
        try:
            from core.halim_capabilities import log_capability_banner
            log_capability_banner(self.cfg)
        except Exception:
            pass
        try:
            from core.halim_active import enforce_active_halim
            enforce_active_halim(context="halim_runtime")
        except Exception:
            pass
        try:
            from core.halim_ppo_coevolution import log_coevolution_banner
            log_coevolution_banner(self.cfg)
        except Exception:
            pass
        try:
            from core.halim_unlock import log_unlock_banner
            log_unlock_banner(self.cfg)
        except Exception:
            pass
        self._mode = self.resolve_mode()
        self._save_state({"initialized": True})
        log.info(
            f"🧠 Halim runtime — co-located with HANOON algo · mode={self._mode} · "
            f"primary=profit hunting"
        )
        try:
            from core.device_trading_focus import enforce_device_trading_focus, market_focus_active
            if market_focus_active(self.cfg):
                enforce_device_trading_focus(self.cfg, force=True)
        except Exception:
            pass

    def attach_runner(self, runner: Optional["ScalperRunner"]) -> None:
        self._runner = runner

    def _device_trading_focus(self) -> None:
        """Kill IDE RAM hogs + learn loop during market/trading focus."""
        if os.getenv("DEVICE_TRADING_FOCUS", "true").lower() not in ("1", "true", "yes"):
            return
        now = time.time()
        interval = float(os.getenv("HALIM_DEVICE_FOCUS_SEC", "90"))
        if now - self._last_device_focus < interval:
            return
        self._last_device_focus = now
        try:
            from core.device_trading_focus import enforce_device_trading_focus, market_focus_active
            if not market_focus_active(self.cfg):
                return
            r = enforce_device_trading_focus(self.cfg, force=True)
            if r.get("killed") or r.get("removed_extensions"):
                self._journal("device_trading_focus", r)
        except Exception as exc:
            log.debug(f"Device trading focus: {exc}")

    def _watchdog_serve(self) -> None:
        """Restart Halim serve if :8765 health fails (MLX can exit under load)."""
        if os.getenv("HALIM_SERVE_WATCHDOG", "true").lower() not in ("1", "true", "yes"):
            return
        now = time.time()
        if now - self._last_serve_watch < self._serve_watch_sec:
            return
        self._last_serve_watch = now
        try:
            from halim.client import health
            if health(timeout=2.0):
                return
        except Exception:
            pass
        root = Path(__file__).resolve().parents[1]
        log.warning("Halim serve down — watchdog restarting…")
        self._journal("halim_serve_restart", {"source": "halim_runtime"})
        try:
            import subprocess
            subprocess.run(
                [str(root / "scripts/ensure_halim_active.sh"), "--serve-only", "--restart"],
                cwd=str(root), capture_output=True, text=True, timeout=180,
            )
        except Exception as exc:
            log.debug(f"Halim serve watchdog: {exc}")

    def tick(self, runner: Optional["ScalperRunner"] = None) -> None:
        """Called each main-loop iteration (throttled). Trading always wins."""
        now = time.time()
        if now - self._last_tick < self._tick_sec:
            return
        self._last_tick = now

        self._watchdog_serve()
        self._device_trading_focus()

        try:
            from core.halim_guardrails import kill_switch_active
            if kill_switch_active():
                return
        except Exception:
            pass

        prev_mode = self._mode
        self._mode = self.resolve_mode()
        if self._mode != prev_mode:
            self._journal("mode_change", {"from": prev_mode, "to": self._mode})
            log.info(f"🧠 Halim mode → {self._mode}")

        can_trade, market_state = can_trade_now(self.cfg)

        if self._mode == "trade_focus":
            self._watchdog_serve()  # extra pass — trading hours need Halim alive
            self._save_state({"focus": "profit_hunting", "market_state": market_state})
            return

        if self._mode == "user_task":
            self._run_user_task(runner)
            return

        # off_hours — learn, evolve, develop (never blocks trading loop)
        if now - self._last_learn >= self._learn_interval:
            self._last_learn = now
            self._off_hours_learn()

        if now - self._last_dev >= self._dev_interval:
            self._last_dev = now
            self._off_hours_develop(runner)

        if now - self._last_export >= self._export_interval:
            self._last_export = now
            self._off_hours_export_gold()

        if now - self._last_evolve >= self._auto_lm_interval:
            self._last_evolve = now
            self._off_hours_auto_lm_check()

    def _off_hours_learn(self) -> None:
        if os.getenv("HALIM_WEB_LEARN", "true").lower() not in ("1", "true", "yes"):
            return
        topics = os.getenv("HALIM_LEARN_TOPICS", "").split(",")
        if not any(t.strip() for t in topics):
            from core.halim_learn_catalog import build_learn_topic_pool
            topics = build_learn_topic_pool()
        else:
            topics = [t.strip() for t in topics if t.strip()]
        topic = topics[int(time.time()) % max(1, len(topics))].strip()
        if not topic:
            return
        try:
            from core.halim_learn_browse import _fetch_one
            r = _fetch_one(topic, self.cfg)
            if r.get("ok"):
                self._journal("off_hours_learn", {"topic": topic, "chars": r.get("text_chars", 0)})
        except Exception as exc:
            log.debug(f"Halim off-hours learn: {exc}")

    def _off_hours_export_gold(self) -> None:
        try:
            from core.halim_action_learn import export_action_gold
            r = export_action_gold(include_learn_cache=True)
            if r.get("added", 0):
                self._journal("export_action_gold", r)
        except Exception as exc:
            log.debug(f"Halim export action gold: {exc}")

    def _off_hours_auto_lm_check(self) -> None:
        """Export + maybe schedule LM retrain (non-blocking)."""
        if os.getenv("HALIM_AUTO_LM_RETRAIN", "true").lower() not in ("1", "true", "yes"):
            return
        try:
            from core.halim_action_learn import export_action_gold
            from core.halim_auto_lm import schedule_auto_retrain
            r = export_action_gold(include_learn_cache=True)
            sched = schedule_auto_retrain(r, self.cfg, trigger="off_hours")
            if sched.get("scheduled"):
                self._journal("auto_lm_scheduled", sched)
        except Exception as exc:
            log.debug(f"Halim auto-LM check: {exc}")

    def _off_hours_develop(self, runner: Optional["ScalperRunner"]) -> None:
        if os.getenv("HALIM_OFF_HOURS_DEV", "true").lower() not in ("1", "true", "yes"):
            return
        try:
            from core.halim_developer import run_halim_developer_cycle
            run_halim_developer_cycle(self.cfg, runner=runner, trigger="off_hours")
            self._journal("off_hours_dev", {"trigger": "scheduled"})
        except Exception as exc:
            log.debug(f"Halim off-hours dev: {exc}")

    def _run_user_task(self, runner: Optional["ScalperRunner"]) -> None:
        task = self._user_task_pending
        if not task:
            self._user_task_pending = ""
            return
        self._journal("user_task_start", {"task": task[:200]})
        log.info(f"🧠 Halim user task: {task[:120]}")
        try:
            if task.startswith("wiki:"):
                from core.halim_web_learn import fetch_wikipedia_summary
                fetch_wikipedia_summary(task[5:], self.cfg)
            elif task.startswith("http"):
                from core.halim_web_learn import fetch_learn_page
                fetch_learn_page(task, self.cfg)
            elif task == "develop":
                from core.halim_developer import run_halim_developer_cycle
                run_halim_developer_cycle(self.cfg, runner=runner, trigger="user_task")
            else:
                from core.halim_web_learn import fetch_wikipedia_summary
                fetch_wikipedia_summary(task, self.cfg)
        except Exception as exc:
            log.warning(f"Halim user task failed: {exc}")
        finally:
            self._user_task_pending = ""
            os.environ.pop("HALIM_USER_TASK", None)
            self._journal("user_task_done", {"task": task[:200]})

    def queue_user_task(self, task: str) -> None:
        self._user_task_pending = task.strip()
        os.environ["HALIM_USER_TASK"] = self._user_task_pending


def init_halim_runtime(cfg: BotConfig) -> HalimRuntime:
    global _runtime
    _runtime = HalimRuntime(cfg)
    _runtime.init()
    return _runtime


def get_halim_runtime() -> Optional[HalimRuntime]:
    return _runtime
