#!/usr/bin/env python3
"""
core/ai_runtime_observer.py — Real-time 5W reasoning on algo events.

Watches cancellations, fills, IB errors, bracket rejects, memory pressure.
Asks why / when / how / what, generates fixes, applies guardrailed mutations,
and feeds PPO experience buffer — no permanent blocking.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.experience_buffer import append as buffer_append
from core.notify import log
from core.reward_shaping import shaped_reward

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

INSIGHTS_PATH = Path("models/ai_runtime_insights.jsonl")
_lock = threading.Lock()
_observer: Optional["AIRuntimeObserver"] = None


def get_runtime_observer(cfg: BotConfig) -> "AIRuntimeObserver":
    global _observer
    if _observer is None:
        _observer = AIRuntimeObserver(cfg)
    return _observer


class AIRuntimeObserver:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._runner: Optional[ScalperRunner] = None
        self._last_ts: Dict[str, float] = {}
        self._stats = {"events": 0, "reasoned": 0, "applied": 0}

    def attach(self, runner: "ScalperRunner") -> None:
        self._runner = runner

    def _min_gap(self) -> float:
        return float(getattr(self.cfg, "AI_RUNTIME_EVENT_MIN_SEC", 25.0))

    def observe(self, event: str, **context: Any) -> None:
        if not getattr(self.cfg, "AI_RUNTIME_OBSERVER_ENABLED", True):
            return
        ticker = str(context.get("ticker", "") or "")
        key = f"{event}:{ticker}" if ticker else event
        now = time.time()
        with _lock:
            if now - self._last_ts.get(key, 0.0) < self._min_gap():
                return
            self._last_ts[key] = now
            self._stats["events"] += 1

        self._append_insight(event, context)
        self._record_experience(event, context)

        if getattr(self.cfg, "AI_RUNTIME_REASONING_ENABLED", True):
            threading.Thread(
                target=self._reason_and_improve,
                args=(event, dict(context)),
                name=f"ai-observe-{event}",
                daemon=True,
            ).start()

    def _append_insight(self, event: str, context: Dict[str, Any]) -> None:
        INSIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **{k: v for k, v in context.items() if k not in ("ollama_raw",)},
        }
        try:
            with _lock:
                with open(INSIGHTS_PATH, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
        except Exception as exc:
            log.debug(f"Runtime insight write: {exc}")

    def _record_experience(self, event: str, context: Dict[str, Any]) -> None:
        try:
            reward = shaped_reward(
                self.cfg,
                float(context.get("pnl_usd", 0) or 0),
                event=event,
                bracket_rejected=event == "bracket_reject",
                spike_ratio=float(context.get("spike_ratio", 1.0) or 1.0),
            )
            buffer_append({
                "source": "runtime_observer",
                "action": event.upper(),
                "ticker": context.get("ticker", ""),
                "reason": str(context.get("reason", ""))[:300],
                "reward": reward,
                "win": bool(context.get("won", False)),
                "confidence": float(context.get("confidence", 0) or 0),
                "market_state": context.get("market_state", ""),
            })
        except Exception:
            pass

    def _think(self, prompt: str) -> str:
        runner = self._runner
        if runner and getattr(runner, "ai_commander", None):
            ac = runner.ai_commander
            if getattr(ac, "autopilot", None) and getattr(ac.autopilot, "core", None):
                ollama = getattr(ac.autopilot.core, "ollama", None)
                if ollama and getattr(ollama, "decide_call", None):
                    text = ollama.decide_call(prompt)
                    if text:
                        return text
            if hasattr(ac, "compose_telegram"):
                return ac.compose_telegram(prompt) or ""
        return ""

    def _reason_and_improve(self, event: str, context: Dict[str, Any]) -> None:
        runner = self._runner
        try:
            from core.commander_learning import (
                build_learning_context,
                generate_runtime_event_plan,
                apply_commander_plan,
            )
            from core.market_hours import get_market_state

            ctx = build_learning_context(
                self.cfg, runner,
                trigger=self._format_trigger(event, context),
            )
            ctx["runtime_event"] = event
            ctx["runtime_detail"] = context
            ctx["market_state"] = get_market_state(self.cfg)

            plan = generate_runtime_event_plan(self.cfg, ctx, self._think)
            if not plan or not plan.get("summary"):
                return

            with _lock:
                self._stats["reasoned"] += 1

            log.info(
                f"🧠 RUNTIME [{event}] {context.get('ticker', '')}: "
                f"{(plan.get('summary') or '')[:120]}"
            )

            if not getattr(self.cfg, "AI_RUNTIME_AUTO_APPLY", True):
                return

            autopilot = getattr(runner, "autopilot", None) if runner else None
            consciousness = getattr(runner, "consciousness", None) if runner else None
            applied = apply_commander_plan(
                self.cfg, plan,
                autopilot=autopilot,
                consciousness=consciousness,
                source=f"runtime_{event}",
            )
            n = len(applied.get("applied") or [])
            if n:
                with _lock:
                    self._stats["applied"] += n
                log.info(f"🧬 RUNTIME applied {n} fix(es) after {event}")
        except Exception as exc:
            log.debug(f"Runtime observer {event}: {exc}")

    @staticmethod
    def _format_trigger(event: str, context: Dict[str, Any]) -> str:
        ticker = context.get("ticker", "")
        reason = context.get("reason", "")
        lines = [
            f"REAL-TIME EVENT: {event}",
            f"Ticker: {ticker}",
            f"Detail: {reason}",
        ]
        for k in ("ib_code", "pipeline", "pnl_usd", "pnl_pct", "market_state", "parent_status"):
            if context.get(k) is not None:
                lines.append(f"{k}: {context[k]}")
        lines.append(
            "Analyze with WHY / WHEN / HOW / WHAT. Propose algorithmic fixes so this does not repeat."
        )
        return "\n".join(lines)[:2000]
