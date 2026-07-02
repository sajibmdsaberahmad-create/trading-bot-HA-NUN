#!/usr/bin/env python3
"""
core/deferred_council_learning.py — Log late council responses after PPO-led execution.

Profit hunting executes on PPO + mechanical signals without waiting for the council.
When the council answer arrives (seconds later), we still record it for learning
so PPO and the strategist improve from every hunt.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.live_ai_pipeline import LiveAILine


def deferred_learning_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "DEFERRED_COUNCIL_LEARNING", True))


class DeferredCouncilLearner:
  """Track post-execute council rings and log council when it catches up."""

  def __init__(self, cfg: BotConfig, live_line: "LiveAILine"):
      self.cfg = cfg
      self._live = live_line
      self._pending: Deque[Dict[str, Any]] = deque(maxlen=300)
      self._logged_seq: set = set()

  def schedule(
      self,
      *,
      ticker: str,
      task: str,
      fingerprint: str,
      executed: Dict[str, Any],
      ppo_signal: Any,
      ppo_conf: float,
      ppo_reason: str = "",
      market_ctx: Optional[Dict[str, Any]] = None,
  ) -> None:
      if not deferred_learning_enabled(self.cfg):
          return
      self._pending.append({
          "ticker": ticker.upper(),
          "task": task,
          "fingerprint": fingerprint,
          "executed": dict(executed),
          "ppo_signal": ppo_signal,
          "ppo_conf": round(float(ppo_conf), 4),
          "ppo_reason": str(ppo_reason or "")[:200],
          "market_ctx": dict(market_ctx or {}),
          "scheduled_at": time.time(),
      })

  def service(self) -> int:
      """Poll late council answers — returns count logged this pass."""
      if not deferred_learning_enabled(self.cfg) or not self._pending:
          return 0
      max_age = float(getattr(self.cfg, "DEFERRED_COUNCIL_MAX_AGE_SEC", 120.0))
      now = time.time()
      remaining: Deque[Dict[str, Any]] = deque(maxlen=300)
      logged = 0

      for job in list(self._pending):
          age = now - float(job.get("scheduled_at", now))
          if age > max_age:
              continue
          ticker = str(job["ticker"])
          task = str(job["task"])
          peek = self._live.peek(ticker, task)
          status = str(peek.get("status", "missing"))
          if status == "in_flight":
              remaining.append(job)
              continue
          if status not in ("ready",) or not peek.get("parsed"):
              remaining.append(job)
              continue

          seq_key = f"{ticker}:{task}:{peek.get('fingerprint', '')}:{peek.get('latency_ms', 0)}"
          if seq_key in self._logged_seq:
              continue

          self._log_late_response(job, peek)
          self._logged_seq.add(seq_key)
          logged += 1
          if len(self._logged_seq) > 500:
              self._logged_seq = set(list(self._logged_seq)[-250:])

      self._pending = remaining
      return logged

  def _log_late_response(self, job: Dict[str, Any], peek: Dict[str, Any]) -> None:
      parsed = peek.get("parsed") or {}
      executed = job.get("executed") or {}
      task = str(job.get("task", ""))
      ticker = str(job.get("ticker", "?"))
      ppo_conf = float(job.get("ppo_conf", 0.5))
      ppo_signal = job.get("ppo_signal")
      latency_ms = float(peek.get("latency_ms", 0) or 0)

      if task == "entry_decision":
          executed_enter = bool(executed.get("enter", True))
          ollama_enter = bool(parsed.get("enter", False))
          agreement = executed_enter == ollama_enter
          ppo_buy = int(ppo_signal or 0) == 1
          ppo_agrees = (ppo_buy and executed_enter) or (not ppo_buy and not executed_enter)
      elif task == "exit_decision":
          executed_enter = bool(executed.get("exit", True))
          ollama_enter = bool(parsed.get("exit", False))
          agreement = executed_enter == ollama_enter
          ppo_agrees = bool(ppo_signal) == executed_enter
      else:
          executed_enter = executed.get("action") or executed.get("enter") or executed.get("exit")
          ollama_enter = parsed.get("action") or parsed.get("enter") or parsed.get("exit")
          agreement = str(executed_enter) == str(ollama_enter)
          ppo_agrees = agreement

      ollama_conf = float(parsed.get("confidence", 0) or 0)
      pipeline = str(executed.get("pipeline", "ppo_led"))
      weight = float(getattr(self.cfg, "PPO_LEARNING_WEIGHT", 1.5))
      if ppo_agrees:
          weight *= 1.1
      if agreement:
          reward_hint = 0.15
      elif ppo_agrees and not agreement:
            reward_hint = 0.25  # PPO was right, council late/wrong — reinforce PPO
      else:
          reward_hint = -0.05

      row = {
          "source": "deferred_council",
          "task": task,
          "ticker": ticker,
          "executed_before_ai": True,
          "executed_pipeline": pipeline,
          "executed_action": executed,
          "ollama_parsed": parsed,
          "ollama_raw": (peek.get("raw") or "")[:500],
          "ollama_confidence": ollama_conf,
          "ollama_agrees_with_execute": agreement,
          "ppo_signal": ppo_signal,
          "ppo_conf": ppo_conf,
          "ppo_reason": job.get("ppo_reason", ""),
          "ppo_agrees_with_execute": ppo_agrees,
          "late_latency_ms": latency_ms,
          "training_weight": weight,
          "reward_hint": reward_hint,
          "fingerprint": job.get("fingerprint", ""),
          "market_ctx": job.get("market_ctx", {}),
          "timestamp": datetime.now(timezone.utc).isoformat(),
      }

      try:
          from core.experience_buffer import append as buffer_append
          buffer_append(row)
      except Exception:
          pass

      try:
          from core.ai_learning_policy import record_failure_for_learning
          record_failure_for_learning(
              self.cfg,
              ticker=ticker,
              reason=(
                  f"LATE {task}: executed={executed_enter} council={ollama_enter} "
                  f"PPO={ppo_conf:.0%} agree={agreement} ({latency_ms:.0f}ms)"
              )[:200],
              event="deferred_council",
              extra={
                  "task": task,
                  "agreement": agreement,
                  "ppo_agrees": ppo_agrees,
                  "latency_ms": latency_ms,
                  "pipeline": pipeline,
              },
          )
      except Exception:
          pass

      log.info(
          f"  📚 LATE COUNCIL {ticker}/{task}: "
          f"executed={'Y' if executed_enter else 'N'} "
          f"council={'Y' if ollama_enter else 'N'} | "
          f"PPO {ppo_conf:.0%} | agree={agreement} | {latency_ms:.0f}ms late"
      )

      try:
          from core.ppo_entry_learning import on_council_response
          on_council_response(
              self.cfg,
              ticker=ticker,
              task=task,
              executed=executed,
              ollama_parsed=parsed,
              ppo_signal=ppo_signal,
              ppo_conf=ppo_conf,
              ppo_reason=str(job.get("ppo_reason", "")),
              latency_ms=latency_ms,
          )
      except Exception as exc:
          log.debug(f"PPO council eval: {exc}")

      try:
          from core.halim_ppo_coevolution import record_coevolution
          halim_sig = parsed.get("enter") if task == "entry_decision" else parsed.get("exit")
          record_coevolution(
              self.cfg,
              ticker=ticker,
              task=task,
              ppo_signal=ppo_signal,
              ppo_conf=ppo_conf,
              ppo_reason=str(job.get("ppo_reason", "")),
              halim_source="deferred",
              halim_signal=halim_sig,
              halim_conf=ollama_conf,
              halim_reason=str(parsed.get("reason", ""))[:300],
              executed=executed,
              pipeline=pipeline,
              extra={"late_latency_ms": latency_ms, "agreement": agreement},
          )
      except Exception:
          pass
