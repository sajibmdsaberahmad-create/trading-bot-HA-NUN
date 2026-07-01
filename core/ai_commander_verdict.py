#!/usr/bin/env python3
"""Extracted from ai_commander — commander verdict."""

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


class CommanderVerdictMixin:
    """Mixin — composed into AICommander."""

    def _stamp_council_signals(
        self, out: Dict[str, Any], council_parsed: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Preserve independent council vote for coevolution (not the merged execute bit)."""
        if not council_parsed:
            return out
        row = dict(out)
        if "enter" in council_parsed:
            row["council_enter"] = bool(council_parsed.get("enter"))
            row["council_conf"] = float(council_parsed.get("confidence", 0) or 0)
            row["council_reason"] = str(council_parsed.get("reason", ""))[:200]
        elif "exit" in council_parsed:
            row["council_exit"] = bool(council_parsed.get("exit"))
            row["council_conf"] = float(council_parsed.get("confidence", 0) or 0)
            row["council_reason"] = str(council_parsed.get("reason", ""))[:200]
        return row
    def _entry_verdict(self, payload: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
        from core.halim_ppo_coevolution import merge_coevolution_stamps
        return merge_coevolution_stamps(payload, out)
    def _emit_spike_verdict(
        self,
        decision: Dict[str, Any],
        *,
        ticker: str,
        spike_ratio: float,
        scan_score: float,
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str = "",
        gate_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Phase D: log every finalized entry deliberation (skip pending)."""
        if decision.get("pending"):
            return decision
        dec = {**decision}
        if hasattr(self, "_halim_entry"):
            try:
                from core.halim_ppo_coevolution import enrich_decision_halim_peek
                dec = enrich_decision_halim_peek(
                    dec, self._halim_entry.peek(ticker), task="entry_decision",
                )
            except Exception:
                pass
        dec.setdefault("ppo_action", ppo_action)
        dec.setdefault("ppo_conf", ppo_conf)
        if ppo_reason:
            dec.setdefault("ppo_reason", ppo_reason)
        halim_status = ""
        if hasattr(self, "_halim_entry"):
            try:
                halim_status = str(self._halim_entry.peek(ticker).get("status", ""))
            except Exception:
                pass
        try:
            self._record_council_learning(
                ticker, dec, "entry_decision", ppo_action, ppo_conf,
            )
        except Exception:
            pass
        try:
            from core.smart_stack import log_spike_verdict
            gate_context = gate_context or getattr(self, "_decide_entry_gate_ctx", None)
            log_spike_verdict(
                self.cfg,
                ticker=ticker,
                spike_ratio=spike_ratio,
                scan_score=scan_score,
                ppo_action=ppo_action,
                ppo_conf=ppo_conf,
                decision=decision,
                gate_context=gate_context,
                halim_status=halim_status,
            )
        except Exception:
            pass
        try:
            from core.halim_ppo_coevolution import (
                extract_coevolution_halim_signals,
                record_coevolution,
            )

            halim_src, halim_sig, halim_conf, halim_reason = extract_coevolution_halim_signals(
                dec, "entry_decision",
            )
            record_coevolution(
                self.cfg,
                ticker=ticker,
                task="entry_decision",
                ppo_signal=ppo_action == 1,
                ppo_conf=ppo_conf,
                ppo_reason=ppo_reason,
                halim_source=halim_src,
                halim_signal=halim_sig,
                halim_conf=halim_conf,
                halim_reason=halim_reason,
                executed=dec,
                pipeline=str(dec.get("pipeline", "")),
                extra={
                    "spike_ratio": spike_ratio,
                    "scan_score": scan_score,
                    "halim_status": halim_status,
                },
            )
        except Exception:
            pass
        return decision
    def _finalize_entry_decision(
        self,
        out: Dict[str, Any],
        *,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        min_conf: float,
        deploy_cap: float,
        max_risk: float,
        use_fixed_risk: bool,
        is_penny: bool,
        avg_vol: float,
        df: Optional[pd.DataFrame] = None,
        equity: float = 0.0,
        cash: float = 0.0,
        gate_context: Optional[Dict[str, Any]] = None,
        micro: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        micro = micro or getattr(self, "_finalize_entry_micro", None) or {}
        eq = getattr(self, "_entry_quality_snapshot", None) or out.get("entry_quality")
        if isinstance(eq, dict):
            out = dict(out)
            out["quality_enter"] = bool(eq.get("enter_ok"))
            out["quality_conf"] = float(eq.get("profit_probability", 0) or 0)
            out["quality_reason"] = str(eq.get("reason", eq.get("setup_type", "")))[:200]
            out["profit_probability"] = float(eq.get("profit_probability", 0) or 0)
            out["fakeout_risk"] = float(eq.get("fakeout_risk", 0) or 0)
        out = self._blend_halim_entry(
            out,
            ticker=ticker,
            fingerprint=fp,
            ppo_buy=ppo_action == 1,
            ppo_conf=ppo_conf,
            min_conf=min_conf,
            advisory_ctx={
                "spike_ratio": spike_ratio,
                "scan_score": scan_score,
            },
        )
        enter = bool(out.get("enter"))
        confidence = float(out.get("confidence", ppo_conf))
        if out.get("gut_feel") is not None:
            gut_feel = float(out.get("gut_feel", 0.5) or 0.5)
            enter, gut_note = apply_gut_override(enter, gut_feel, ppo_action, ppo_conf, min_conf)
            if gut_note:
                out["reason"] = f"{out.get('reason', '')} | {gut_note}".strip(" |")

        try:
            from core.smart_stack import strict_profit_prob_enabled, ai_sure_entry_enabled
            strict_prob = strict_profit_prob_enabled(self.cfg)
            ai_sure = ai_sure_entry_enabled(self.cfg)
        except Exception:
            strict_prob = False
            ai_sure = False

        if (
            not enter
            and not strict_prob
            and not ai_sure
            and not is_ai_unlimited(self.cfg)
            and not is_ai_council_mode(self.cfg)
        ):
            try:
                from core.war_entry_gates import war_gates_active
                war_on = war_gates_active(self.cfg)
            except Exception:
                war_on = False
            if not war_on:
                if spike_ratio >= 1.5 and scan_score >= 35:
                    enter = True
                    confidence = max(confidence, 0.55)
                    out["reason"] = (
                        f"Momentum entry: spike={spike_ratio:.1f}x score={scan_score:.0f} | "
                        f"{out.get('reason', ppo_reason or '')}"
                    )[:200]
                elif ppo_action == 1 and ppo_conf >= min_conf:
                    enter = True
                    confidence = max(confidence, ppo_conf)
                    out["reason"] = f"PPO buy signal: {ppo_reason or 'ensemble confirmed'}"

        if enter:
            try:
                from core.smart_stack import apply_smart_war_entry, smart_war_posture_enabled
                from core.capital_discipline import effective_min_profit_probability
                if smart_war_posture_enabled(self.cfg):
                    vetoed = apply_smart_war_entry(
                        self.cfg,
                        {**out, "enter": enter, "confidence": confidence},
                        ppo_action=ppo_action,
                        ppo_conf=ppo_conf,
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                        min_conf=min_conf,
                        min_prob=effective_min_profit_probability(self.cfg),
                    )
                else:
                    from core.war_entry_gates import apply_war_entry_veto
                    vetoed = apply_war_entry_veto(
                        self.cfg,
                        {**out, "enter": enter, "confidence": confidence},
                        ppo_action=ppo_action,
                        ppo_conf=ppo_conf,
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                    )
                if not vetoed.get("enter"):
                    return self._emit_spike_verdict(
                        self._entry_verdict(
                            {
                            "enter": False,
                            "confidence": float(vetoed.get("confidence", confidence)),
                            "shares": 0,
                            "stop": 0.0,
                            "target": 0.0,
                            "risk_usd": 0.0,
                            "reason": str(vetoed.get("reason", "war entry veto"))[:200],
                            "journal": str(out.get("journal", ""))[:300],
                            "pipeline": str(vetoed.get("pipeline", "war:entry_veto")),
                            "pending": False,
                            },
                            out,
                        ),
                        ticker=ticker,
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                        ppo_action=ppo_action,
                        ppo_conf=ppo_conf,
                        ppo_reason=ppo_reason,
                        gate_context=gate_context,
                    )
                out = vetoed
                enter = bool(vetoed.get("enter"))
                confidence = float(vetoed.get("confidence", confidence))
            except Exception:
                pass

        try:
            from core.entry_quality import apply_profit_prob_veto, apply_ai_sure_veto
            vetoed_prob = apply_profit_prob_veto(self.cfg, {**out, "enter": enter, "confidence": confidence}, eq)
            out = vetoed_prob
            enter = bool(vetoed_prob.get("enter"))
            confidence = float(vetoed_prob.get("confidence", confidence))
            vetoed_sure = apply_ai_sure_veto(
                self.cfg, out,
                eq,
                ppo_action=ppo_action,
                ppo_conf=ppo_conf,
                scan_score=scan_score,
                spike_ratio=spike_ratio,
                ticker=ticker,
            )
            out = vetoed_sure
            enter = bool(vetoed_sure.get("enter"))
            confidence = float(vetoed_sure.get("confidence", confidence))
        except Exception:
            pass

        if enter:
            try:
                from core.green_trade_doctrine import (
                    green_entry_mandatory,
                    green_verdict_recheck_enabled,
                    require_green_entry,
                )
                if (
                    green_entry_mandatory(self.cfg)
                    and green_verdict_recheck_enabled(self.cfg)
                    and df is not None
                    and len(df) > 0
                ):
                    block = require_green_entry(
                        self.cfg,
                        ticker=ticker,
                        df=df,
                        current_px=current_px,
                        micro=micro or {},
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                        ppo_action=ppo_action,
                        ppo_conf=ppo_conf,
                        decision=out,
                    )
                    if block:
                        enter = False
                        out = {
                            **out,
                            "enter": False,
                            "reason": block,
                            "pipeline": "green_doctrine:veto",
                        }
            except Exception:
                pass

        if not enter:
            return self._emit_spike_verdict(
                self._entry_verdict(
                    {
                    "enter": False,
                    "confidence": confidence,
                    "shares": 0,
                    "stop": 0.0,
                    "target": 0.0,
                    "risk_usd": 0.0,
                    "reason": str(out.get("reason", ppo_reason or "AI skip")),
                    "journal": str(out.get("journal", ""))[:300],
                    "pipeline": str(out.get("pipeline", "")),
                    "pending": False,
                    },
                    out,
                ),
                ticker=ticker,
                spike_ratio=spike_ratio,
                scan_score=scan_score,
                ppo_action=ppo_action,
                ppo_conf=ppo_conf,
                ppo_reason=ppo_reason,
                gate_context=gate_context,
            )

        bracket = self._build_entry_bracket(
            current_px, df,
            equity=equity,
            cash=cash,
            deploy_cap=deploy_cap,
            is_penny=is_penny,
            avg_vol=avg_vol,
        )
        if not bracket.ok:
            reason = f"bracket rejected: {bracket.reason}"
            log.warning(f"  🛑 {ticker} {reason}")
            snap = self.ollama_audit_snapshot(ticker)
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=bracket.reason,
                entry=current_px, stop=bracket.stop, target=bracket.target,
                shares=bracket.shares, council_decision=out,
                ollama_raw=snap.get("raw", ""), ollama_parsed=snap.get("parsed"),
                spike_ratio=spike_ratio, pipeline="atr_reject",
            )
            return self._emit_spike_verdict(
                self._entry_verdict(
                    {
                    "enter": False,
                    "confidence": confidence,
                    "shares": 0,
                    "stop": 0.0,
                    "target": 0.0,
                    "risk_usd": 0.0,
                    "reason": reason,
                    "journal": str(out.get("journal", reason))[:300],
                    "pipeline": "atr_reject",
                    "pending": False,
                    },
                    out,
                ),
                ticker=ticker,
                spike_ratio=spike_ratio,
                scan_score=scan_score,
                ppo_action=ppo_action,
                ppo_conf=ppo_conf,
                ppo_reason=ppo_reason,
                gate_context=gate_context,
            )

        reason = str(out.get("reason", ppo_reason or "AI entry"))
        journal_note = str(out.get("journal", reason))[:300]
        decision = self._entry_verdict(
            {
            "enter": True,
            "confidence": confidence,
            "shares": bracket.shares,
            "stop": bracket.stop,
            "target": bracket.target,
            "risk_usd": bracket.risk_usd,
            "reward_risk": bracket.reward_risk,
            "reason": f"{reason} | ATR R:R {bracket.reward_risk:.1f}"[:200],
            "journal": journal_note,
            "pipeline": str(out.get("pipeline", "council+atr_math")),
            "pending": False,
            "council_agreement": out.get("council_agreement"),
            "ticker": ticker,
            "entry": current_px,
            },
            out,
        )
        ok, decision, err = validate_decision_bracket(self.cfg, decision, fallback_entry=current_px)
        if not ok:
            snap = self.ollama_audit_snapshot(ticker)
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=err,
                entry=current_px, stop=decision.get("stop", 0),
                target=decision.get("target", 0), shares=int(decision.get("shares", 0)),
                council_decision=out,
                ollama_raw=snap.get("raw", ""), ollama_parsed=snap.get("parsed"),
                spike_ratio=spike_ratio, pipeline="bracket_validator",
            )
            return self._emit_spike_verdict(
                self._entry_verdict(
                    {
                    "enter": False,
                    "confidence": confidence,
                    "shares": 0,
                    "stop": 0.0,
                    "target": 0.0,
                    "risk_usd": 0.0,
                    "reason": err,
                    "journal": journal_note,
                    "pipeline": "bracket_validator",
                    "pending": False,
                    },
                    out,
                ),
                ticker=ticker,
                spike_ratio=spike_ratio,
                scan_score=scan_score,
                ppo_action=ppo_action,
                ppo_conf=ppo_conf,
                ppo_reason=ppo_reason,
                gate_context=gate_context,
            )
        pipeline = str(decision.get("pipeline", ""))
        from core.council_nanny import is_strong_spike_pipeline
        if (
            pipeline.startswith(("ppo:", "council:scanner_fast", "council:scanner_timeout"))
            and not is_strong_spike_pipeline(pipeline)
        ):
            fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
            self._schedule_deferred_entry(
                ticker=ticker, fingerprint=fp, decision=decision,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            )
            if deferred_learning_enabled(self.cfg) and pipeline.startswith("council:"):
                self._ring_entry_council_for_learning(
                    ticker, current_px, spike_ratio, scan_score,
                    ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    account={
                        "equity": equity, "cash": cash, "nav": equity,
                        "open_positions": 0,
                        "max_positions": effective_max_concurrent_positions(self.cfg),
                        "held_tickers": [], "deployed_usd": 0,
                    },
                    is_penny=is_penny, df=df,
                    pipeline=pipeline,
                )
        self.journal("ENTRY_DECISION", journal_note, decision)
        self.ai_log("ENTRY_DECISION", {**decision, "ticker": ticker, "price": current_px})
        return self._emit_spike_verdict(
            decision,
            ticker=ticker,
            spike_ratio=spike_ratio,
            scan_score=scan_score,
            ppo_action=ppo_action,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            gate_context=gate_context,
        )
