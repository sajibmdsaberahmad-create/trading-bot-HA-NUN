#!/usr/bin/env python3
"""Extracted from ai_commander — commander learning."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.ai_commander_mixin_imports import *  # noqa: F403
from core.notify import log


class CommanderLearningMixin:
    """Mixin — composed into AICommander."""

    def _ring_entry_council_for_learning(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        *,
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        account: Dict[str, Any],
        market_ctx: Optional[Dict[str, Any]] = None,
        is_penny: bool = False,
        df: Optional[pd.DataFrame] = None,
        pipeline: str = "",
    ) -> str:
        """Fire council async for learning — skipped in nanny mode unless strong-spike fill."""
        fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return fp
        from core.council_nanny import should_ring_council
        ok, reason = should_ring_council(
            self.cfg, "entry_decision", for_learning=True,
            spike_ratio=spike_ratio, scan_score=scan_score,
            pipeline=pipeline,
        )
        if not ok:
            log.debug(f"Council learning ring skipped {ticker}: {reason}")
            return fp
        chart_line = ""
        if df is not None and len(df) >= 20:
            chart_line = self._chart_context_line(ticker, current_px, spike_ratio, scan_score)
        prompt = self._entry_council_prompt(
            ticker, current_px, spike_ratio, scan_score,
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            account=account, market_ctx=market_ctx, is_penny=is_penny, chart_line=chart_line,
        )
        mood, conf_m, lessons = self._mood_context()
        full = enrich_prompt(
            "entry_decision", {"request": prompt[:2500]}, self.cfg, mood, conf_m, lessons,
        )
        self._live_line.ring(
            ticker, "entry_decision", full, fp,
            spike_ratio=spike_ratio, scan_score=scan_score,
        )
        return fp
    def _schedule_deferred_entry(
        self,
        *,
        ticker: str,
        fingerprint: str,
        decision: Dict[str, Any],
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        market_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not decision.get("enter") or not deferred_learning_enabled(self.cfg):
            return
        pipeline = str(decision.get("pipeline", ""))
        from core.council_nanny import learning_ring_for_pipeline
        if not learning_ring_for_pipeline(self.cfg, pipeline):
            return
        self._deferred.schedule(
            ticker=ticker,
            task="entry_decision",
            fingerprint=fingerprint,
            executed=decision,
            ppo_signal=ppo_action,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            market_ctx=market_ctx,
        )
    def ring_post_fill_learning(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        decision: Dict[str, Any],
        *,
        account: Optional[Dict[str, Any]] = None,
        market_ctx: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,
    ) -> None:
        """One async council ring after strong-spike fill — feeds distillation without RPM burn."""
        pipeline = str(decision.get("pipeline", ""))
        from core.council_nanny import learning_ring_for_pipeline
        if not learning_ring_for_pipeline(self.cfg, pipeline):
            return
        ppo_action = 1 if decision.get("enter") else 0
        ppo_conf = float(decision.get("confidence", 0.5) or 0.5)
        ppo_reason = str(decision.get("reason", ""))[:200]
        acct = account or {}
        fp = self._ring_entry_council_for_learning(
            ticker, current_px, spike_ratio, scan_score,
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            account=acct, market_ctx=market_ctx, df=df,
            pipeline=pipeline,
        )
        if not fp:
            return
        self._schedule_deferred_entry(
            ticker=ticker, fingerprint=fp, decision=decision,
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            market_ctx=market_ctx,
        )
        log.info(f"  📚 Distill ring queued {ticker} ({pipeline}) — council labels async")
    def ring_exit_for_deferred_learning(
        self,
        ctx: Dict[str, Any],
        *,
        ppo_exit: bool,
        ppo_conf: float,
        ppo_reason: str,
        executed_exit: bool,
        pipeline: str,
    ) -> None:
        """Ring exit council after mechanical/PPO profit lock — log when Ollama answers."""
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", ctx.get("current_px", 0)) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        fp = exit_fingerprint(ticker, price, pnl_pct)
        prompt = (
            f"Should we EXIT position {ticker} now?\n"
            f"{json.dumps(ctx, default=str)[:700]}\n"
            f"PPO exit signal: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:60]}\n"
            'JSON: {"exit":true/false,"confidence":0-1,"gut_feel":0-1,'
            '"reason":"why","journal":"exit log"}'
        )
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(
            "exit_decision", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
        )
        self._live_line.ring(ticker, "exit_decision", full, fp, in_position=True)
        self._deferred.schedule(
            ticker=ticker,
            task="exit_decision",
            fingerprint=fp,
            executed={
                "exit": executed_exit,
                "pipeline": pipeline,
                "reason": ctx.get("reason", pipeline),
            },
            ppo_signal=ppo_exit,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            market_ctx=ctx,
        )
    def _record_council_learning(
        self,
        ticker: str,
        decision: Dict[str, Any],
        task: str,
        ppo_signal: Any,
        ppo_conf: float,
    ):
        """Log PPO-led + council outcomes for incremental learning."""
        try:
            from core.experience_buffer import append as buffer_append
            pipeline = str(decision.get("pipeline", ""))
            ppo_primary = pipeline.startswith("ppo:") or "spike_fast" in pipeline
            weight = float(getattr(self.cfg, "PPO_LEARNING_WEIGHT", 1.5)) if ppo_primary else 1.0
            buffer_append({
                "source": "ppo_led" if ppo_primary else "ai_council",
                "task": task,
                "ticker": ticker,
                "ppo_signal": ppo_signal,
                "ppo_conf": round(ppo_conf, 4),
                "ppo_primary": ppo_primary,
                "final_enter": decision.get("enter"),
                "final_exit": decision.get("exit"),
                "final_action": decision.get("action"),
                "confidence": float(decision.get("confidence", 0)),
                "pipeline": pipeline,
                "council_agreement": decision.get("council_agreement"),
                "reason": str(decision.get("reason", ""))[:200],
                "training_weight": weight,
                "ollama_deferred": ppo_primary or pipeline.startswith("council:scanner"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        try:
            from core.halim_action_learn import record_action
            cap = "enter_skip" if "student" in str(decision.get("pipeline", "")) else "decision_text"
            record_action(
                cap, task,
                input_text=f"{ticker} ppo={ppo_signal} conf={ppo_conf:.2f}",
                output_text=str(decision.get("reason", decision))[:800],
                outcome="ok",
                source=str(decision.get("pipeline", "decision"))[:60],
                meta={"enter": decision.get("enter"), "exit": decision.get("exit")},
                cfg=self.cfg,
            )
        except Exception:
            pass
        try:
            from core.halim_ppo_coevolution import (
                extract_coevolution_halim_signals,
                record_coevolution,
            )
            halim_src, halim_sig, halim_conf, halim_reason = extract_coevolution_halim_signals(
                decision, task,
            )
            pipeline = str(decision.get("pipeline", ""))
            record_coevolution(
                self.cfg,
                ticker=ticker,
                task=task,
                ppo_signal=ppo_signal,
                ppo_conf=ppo_conf,
                ppo_reason=str(decision.get("ppo_reason", ""))[:200],
                halim_source=halim_src,
                halim_signal=halim_sig,
                halim_conf=halim_conf,
                halim_reason=halim_reason,
                executed=decision,
                pipeline=pipeline,
            )
        except Exception:
            pass
