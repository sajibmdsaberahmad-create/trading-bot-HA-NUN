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

        ticker = str(context.get("ticker", "") or "")
        detail = str(context.get("reason", "") or context.get("pipeline", ""))[:80]
        log.info(f"👁 RUNTIME observe {event}" + (f" {ticker}" if ticker else "") + f": {detail}")

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
            plan = plan or {}
            heuristic = self._heuristic_mutations(event, context)
            if heuristic:
                plan.setdefault("mutations", []).extend(heuristic)
            if not plan.get("summary"):
                plan["summary"] = f"Heuristic learning for {event} on {context.get('ticker', '?')}"

            with _lock:
                self._stats["reasoned"] += 1

            log.info(
                f"🧠 RUNTIME [{event}] {context.get('ticker', '')}: "
                f"{(plan.get('summary') or '')[:120]}"
            )

            self._apply_runtime_heuristics(event, context)

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

    def _heuristic_mutations(self, event: str, context: Dict[str, Any]) -> list:
        """Rule-based mutations when Ollama is busy or returns empty."""
        muts: list = []
        pipe = str(context.get("pipeline", ""))
        if event == "council_timeout" or "timeout" in pipe:
            wait = float(getattr(self.cfg, "AI_COUNCIL_MAX_WAIT_SEC", 6.0))
            muts.append({
                "param": "ENTRY_OLLAMA_WAIT_SEC",
                "value": min(18.0, wait + 2.0),
                "reason": "Council timed out — extend Ollama wait",
            })
            if float(context.get("scan_score", 0) or 0) >= 75:
                muts.append({
                    "param": "CONFIDENCE_THRESHOLD",
                    "value": max(
                        0.45,
                        float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)) - 0.02,
                    ),
                    "reason": "Scanner timeout entries — slightly lower bar while council catches up",
                })
        if context.get("ib_code") == 2161:
            penny_cap = float(getattr(self.cfg, "PENNY_MAX_DEPLOY_USD", 350.0))
            muts.append({
                "param": "PENNY_MAX_DEPLOY_USD",
                "value": max(150.0, penny_cap * 0.85),
                "reason": "IB 2161 regulatory cap — reduce penny deploy",
            })
            muts.append({
                "param": "PENNY_LIMIT_BUFFER_PCT",
                "value": min(
                    0.015,
                    float(getattr(self.cfg, "PENNY_LIMIT_BUFFER_PCT", 0.005)) + 0.001,
                ),
                "reason": "IB 2161 — tighter limit buffer toward reference price",
            })
        return muts[:3]

    def _apply_runtime_heuristics(self, event: str, context: Dict[str, Any]) -> None:
        """Direct cfg tweaks for params outside learning bounds."""
        applied: list = []
        pipe = str(context.get("pipeline", ""))
        if event == "council_timeout" or "timeout" in pipe:
            old_wait = float(getattr(self.cfg, "AI_COUNCIL_MAX_WAIT_SEC", 6.0))
            new_wait = min(18.0, old_wait + 2.0)
            if new_wait > old_wait:
                self.cfg.AI_COUNCIL_MAX_WAIT_SEC = new_wait
                applied.append(f"AI_COUNCIL_MAX_WAIT_SEC {old_wait:.0f}→{new_wait:.0f}s")
            if getattr(self.cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False):
                self.cfg.LIVE_CHART_VISION_OPPORTUNISTIC = False
                applied.append("LIVE_CHART_VISION_OPPORTUNISTIC off (vision starves council)")
        if applied:
            log.info(f"🧬 RUNTIME heuristic: {', '.join(applied)}")

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
