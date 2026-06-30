#!/usr/bin/env python3
"""Extracted from ai_commander — commander exit."""

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


class CommanderExitMixin:
    """Mixin — composed into AICommander."""

    def _ring_halim_exit(
        self,
        ticker: str,
        fingerprint: str,
        *,
        price: float,
        pnl_pct: float,
        peak_pct: float = 0.0,
        stop: float = 0.0,
        target: float = 0.0,
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
        task: str = "exit_decision",
    ) -> None:
        try:
            self._halim_exit.ring(
                ticker,
                fingerprint,
                price=price,
                pnl_pct=pnl_pct,
                peak_pct=peak_pct,
                stop=stop,
                target=target,
                ppo_exit=ppo_exit,
                ppo_conf=ppo_conf,
                ppo_reason=ppo_reason,
                task=task,
            )
        except Exception as exc:
            log.debug(f"Halim exit ring: {exc}")
    def _blend_halim_exit(
        self,
        decision: Dict[str, Any],
        *,
        ticker: str,
        fingerprint: str,
        ppo_exit: bool,
        ppo_conf: float,
        min_conf: float,
        advisory_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            from core.halim_exit_line import merge_halim_exit_advisory
            halim_live = self._halim_exit.consume(ticker, fingerprint)
            result = merge_halim_exit_advisory(
                decision,
                halim_live,
                ppo_exit=ppo_exit,
                ppo_conf=ppo_conf,
                min_conf=min_conf,
                cfg=self.cfg,
            )
            if result.get("halim_exit") is not None:
                ctx = advisory_ctx or decision
                try:
                    from core.halim_outcome_gold import register_exit_advisory
                    register_exit_advisory(
                        ticker,
                        halim_exit=bool(result["halim_exit"]),
                        halim_conf=float(result.get("halim_conf", 0)),
                        halim_reason=str(result.get("halim_reason", "")),
                        pnl_pct=float(ctx.get("pnl_pct", 0)),
                        peak_pct=float(ctx.get("peak_pct", ctx.get("pnl_pct", 0))),
                        ppo_exit=ppo_exit,
                        ppo_conf=ppo_conf,
                        task=str(ctx.get("task", "exit_decision")),
                        pipeline=str(result.get("pipeline", "")),
                        cfg=self.cfg,
                    )
                except Exception:
                    pass
            return result
        except Exception as exc:
            log.debug(f"Halim exit blend: {exc}")
            return decision
    def _apply_halim_exit_to_manage(
        self,
        result: Dict[str, Any],
        *,
        ticker: str,
        fingerprint: str,
        ppo_exit: bool,
        ppo_conf: float,
        min_conf: float,
        advisory_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Map position_manage EXIT/HOLD through Halim exit advisory."""
        action = str(result.get("action", "HOLD")).upper()
        exit_base = {
            "exit": action == "EXIT",
            "confidence": float(result.get("confidence", ppo_conf) or ppo_conf),
            "reason": str(result.get("reason", ""))[:200],
            "pipeline": str(result.get("pipeline", "")),
        }
        ctx = advisory_ctx or {
            "pnl_pct": float(result.get("pnl_pct", 0)),
            "peak_pct": float(result.get("peak_pct", result.get("pnl_pct", 0))),
            "task": "position_manage",
        }
        blended = self._blend_halim_exit(
            exit_base,
            ticker=ticker,
            fingerprint=fingerprint,
            ppo_exit=ppo_exit or action == "EXIT",
            ppo_conf=ppo_conf,
            min_conf=min_conf,
            advisory_ctx=ctx,
        )
        out = dict(result)
        if not blended.get("exit") and action == "EXIT":
            out["action"] = "HOLD"
            out["reason"] = str(blended.get("reason", out.get("reason", "")))[:120]
            out["pipeline"] = str(blended.get("pipeline", out.get("pipeline", "")))
        elif blended.get("exit") and action != "EXIT":
            out["action"] = "EXIT"
            out["reason"] = str(blended.get("reason", out.get("reason", "")))[:120]
            out["pipeline"] = str(blended.get("pipeline", out.get("pipeline", "")))
        out["confidence"] = float(blended.get("confidence", out.get("confidence", ppo_conf)))
        for key in ("halim_exit", "halim_conf", "halim_agree", "halim_reason"):
            if key in blended:
                out[key] = blended[key]
        return out
    def _resolve_manage_prices(
        self,
        result: Dict[str, Any],
        ctx: Dict[str, Any],
        df: Optional[pd.DataFrame] = None,
        *,
        mechanical_stop: Optional[float] = None,
        mechanical_target: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Map council action → ATR stop/TP — never trust LLM price literals."""
        action = str(result.get("action", "HOLD")).upper()
        if action in ("HOLD", "EXIT"):
            return result
        entry = float(ctx.get("entry", 0) or 0)
        price = float(ctx.get("price", 0) or 0)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        if df is not None and len(df) >= 5:
            atr = float(compute_atr(df, period=5))
        else:
            atr = price * float(getattr(self.cfg, "SCALP_MIN_STOP_PCT", 0.004))
        if action in ("TIGHTEN_STOP", "WIDEN_STOP"):
            new_stop = adjust_managed_stop(self.cfg, action, entry, price, stop, atr)
            if new_stop is None and action == "TIGHTEN_STOP" and mechanical_stop:
                new_stop = float(mechanical_stop)
            if new_stop is not None and new_stop > 0:
                result["stop"] = new_stop
            else:
                result["action"] = "HOLD"
                result["reason"] = f"{result.get('reason', '')} | no ATR stop change"[:120]
        elif action == "RAISE_TP":
            new_tp = adjust_managed_target(self.cfg, action, entry, price, target, atr)
            if new_tp is None and mechanical_target:
                new_tp = float(mechanical_target)
            if new_tp is not None and new_tp > target:
                result["target"] = new_tp
            else:
                result["action"] = "HOLD"
                result["reason"] = f"{result.get('reason', '')} | no ATR TP extension"[:120]
        return result
    def decide_stagnation(
        self,
        ctx: Dict[str, Any],
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
    ) -> Dict[str, Any]:
        """Ollama + PPO decide whether a flat/losing position is dead."""
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        stagnant_sec = float(ctx.get("stagnant_sec", 0) or 0)
        frozen_sec = float(ctx.get("price_frozen_sec", stagnant_sec) or stagnant_sec)
        stagnation_sec = float(getattr(self.cfg, "STAGNATION_EXIT_SEC", 90.0))
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = stagnation_fingerprint(ticker, price, pnl_pct, stagnant_sec)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        peak_pct = float(ctx.get("peak_pct", pnl_pct) or pnl_pct)
        self._ring_halim_exit(
            ticker, fp,
            price=price, pnl_pct=pnl_pct, peak_pct=peak_pct,
            stop=stop, target=target,
            ppo_exit=ppo_exit, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            task="stagnation_check",
        )
        prompt = (
            "You are HANOON live pilot — full-time profit hunter. Profit is your only main goal.\n"
            "This OPEN position may be DEAD — no progress, bleeding time and opportunity cost.\n"
            f"{json.dumps(ctx, default=str)[:1000]}\n"
            f"Stagnant {stagnant_sec:.0f}s (limit {stagnation_sec:.0f}s) | "
            f"Price frozen {frozen_sec:.0f}s\n"
            f"PPO exit signal: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:80]}\n"
            "Use math AND gut feel: is momentum alive or is this a zombie trade?\n"
            'JSON: {"exit":true/false,"confidence":0-1,"gut_feel":0-1,'
            '"intuition":"gut read","force_snapshot":true/false,'
            '"pulse_verbose":true/false,"reason":"brief","journal":"pilot log"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "stagnation_check", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "stagnation_check", full, fp)
            live = self._live_line.consume(ticker, "stagnation_check", fp)
            merged = merge_stagnation_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_exit, ppo_conf, ppo_reason, min_conf,
                stagnant_sec, stagnation_sec,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "exit": False,
                    "fingerprint": fp,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "stagnant_sec": stagnant_sec,
                    "stagnation_sec": stagnation_sec,
                    "ctx": dict(ctx),
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                    "pulse_verbose": bool(merged.get("pulse_verbose", False)),
                }
            result = {
                "exit": bool(merged.get("exit")),
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ""))[:200],
                "journal": str(merged.get("journal", ""))[:300],
                "force_snapshot": bool(merged.get("force_snapshot", False)),
                "pulse_verbose": bool(merged.get("pulse_verbose", False)),
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
                "council_agreement": merged.get("council_agreement"),
            }
            result = self._blend_halim_exit(
                result,
                ticker=ticker,
                fingerprint=fp,
                ppo_exit=ppo_exit,
                ppo_conf=ppo_conf,
                min_conf=min_conf,
                advisory_ctx={**ctx, "task": "stagnation_check"},
            )
        else:
            out = self.think_json(prompt, task="stagnation_check")
            should_exit = bool(out.get("exit", ppo_exit))
            if ppo_exit and ppo_conf >= min_conf:
                should_exit = True
            result = {
                "exit": should_exit,
                "confidence": float(out.get("confidence", ppo_conf)),
                "reason": str(out.get("reason", ppo_reason))[:200],
                "journal": str(out.get("journal", ""))[:300],
                "force_snapshot": bool(out.get("force_snapshot", False)),
                "pulse_verbose": bool(out.get("pulse_verbose", stagnant_sec >= stagnation_sec * 0.5)),
                "pipeline": "ollama_sync",
                "pending": False,
            }
        if result["journal"]:
            self.journal("STAGNATION", result["journal"], {**ctx, **result})
        if not result.get("pending"):
            self._record_council_learning(ticker, result, "stagnation_check", ppo_exit, ppo_conf)
        return result
    def poll_stagnation_council(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        status, parsed = self._poll_live_status(
            ticker, "stagnation_check", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_stagnation_decision(
            parsed, status,
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            float(state.get("stagnant_sec", 0)),
            float(state.get("stagnation_sec", 90)),
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "exit": False,
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
                "pulse_verbose": bool(merged.get("pulse_verbose", False)),
            }
        result = {
            "exit": bool(merged.get("exit")),
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:200],
            "journal": str(merged.get("journal", ""))[:300],
            "force_snapshot": bool(merged.get("force_snapshot", False)),
            "pulse_verbose": bool(merged.get("pulse_verbose", False)),
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
            "council_agreement": merged.get("council_agreement"),
        }
        self._record_council_learning(
            ticker, result, "stagnation_check",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result
    def prefetch_stagnation(self, ctx: Dict[str, Any]) -> None:
        """Stagnation hotline — skipped in nanny mode (local rules handle it)."""
        from core.council_nanny import should_ring_council
        ok, _ = should_ring_council(self.cfg, "stagnation_check", in_position=True)
        if not ok:
            return
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        stagnant_sec = float(ctx.get("stagnant_sec", 0) or 0)
        fp = stagnation_fingerprint(ticker, price, pnl_pct, stagnant_sec)
        stagnation_sec = float(getattr(self.cfg, "STAGNATION_EXIT_SEC", 90.0))
        prompt = (
            f"Prefetch stagnation check {ticker} @ ${price:.4f} P&L {pnl_pct:+.2f}% "
            f"stagnant {stagnant_sec:.0f}s / {stagnation_sec:.0f}s\n"
            f"{json.dumps(ctx, default=str)[:600]}"
        )
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(
            "stagnation_check", {"request": prompt[:2000]}, self.cfg, mood, conf, lessons,
        )
        self._live_line.ring(ticker, "stagnation_check", full, fp)
    def decide_position_manage(
        self,
        ctx: Dict[str, Any],
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
        mechanical_stop: Optional[float] = None,
        mechanical_target: Optional[float] = None,
    ) -> Dict[str, Any]:
        """AI council manages open position: trail stop, profit-take, exit."""
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        peak_pct = float(ctx.get("peak_pct", pnl_pct) or pnl_pct)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = position_fingerprint(ticker, price, pnl_pct, stop, target)
        self._ring_halim_exit(
            ticker, fp,
            price=price, pnl_pct=pnl_pct, peak_pct=peak_pct,
            stop=stop, target=target,
            ppo_exit=ppo_exit, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            task="position_manage",
        )
        prompt = (
            "You are HANOON live pilot — AI FULL POWER profit management.\n"
            "Fast does NOT mean small profit: RIDE winners when momentum is real — trail stop, "
            "raise TP, follow gut. EXIT only when council+PPO agree peak is in or fakeout hits.\n"
            "Close watch always; maximize profit per trade, never scalp green prematurely.\n"
            f"{json.dumps(ctx, default=str)[:900]}\n"
            f"PPO manage signal: exit={ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:80]}\n"
            f"Mechanical trail stop={mechanical_stop} target={mechanical_target}\n"
            "Collaborate with PPO: trail stop on profit, widen on noise, raise TP on momentum, EXIT when dead.\n"
            "You are the STRATEGIST — choose action only. Do NOT output stop or target prices.\n"
            'JSON: {"action":"HOLD|WIDEN_STOP|TIGHTEN_STOP|RAISE_TP|EXIT",'
            '"confidence":0-1,"gut_feel":0-1,'
            '"intuition":"gut read","reason":"brief","journal":"log line"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "position_manage", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "position_manage", full, fp, in_position=True)
            live = self._live_line.consume(ticker, "position_manage", fp)
            merged = merge_position_manage_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_exit, ppo_conf, ppo_reason, min_conf,
                pnl_pct=pnl_pct,
                peak_pct=peak_pct,
                current_stop=stop,
                current_target=target,
                mechanical_stop=mechanical_stop,
                mechanical_target=mechanical_target,
                cfg=self.cfg,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "action": "HOLD",
                    "fingerprint": fp,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "ctx": dict(ctx),
                    "mechanical_stop": mechanical_stop,
                    "mechanical_target": mechanical_target,
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                }
            action = str(merged.get("action", "HOLD")).upper()
            result = {
                "action": action,
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ""))[:120],
                "journal": str(merged.get("journal", ""))[:200],
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
                "council_agreement": merged.get("council_agreement"),
            }
            result = self._resolve_manage_prices(
                result, ctx, bar_df=None,
                mechanical_stop=mechanical_stop,
                mechanical_target=mechanical_target,
                cfg=self.cfg,
            )
            result = self._apply_halim_exit_to_manage(
                result,
                ticker=ticker,
                fingerprint=fp,
                ppo_exit=ppo_exit,
                ppo_conf=ppo_conf,
                min_conf=min_conf,
                advisory_ctx={**ctx, "task": "position_manage"},
            )
        else:
            out = self.think_json(prompt, task="position_manage")
            action = str(out.get("action", "HOLD")).upper()
            if action not in ("HOLD", "WIDEN_STOP", "TIGHTEN_STOP", "RAISE_TP", "EXIT"):
                action = "HOLD"
            result = {
                "action": action,
                "confidence": float(out.get("confidence", 0.5)),
                "reason": str(out.get("reason", ""))[:120],
                "journal": str(out.get("journal", ""))[:200],
                "pending": False,
            }
            result = self._resolve_manage_prices(
                result, ctx,
                mechanical_stop=mechanical_stop,
                mechanical_target=mechanical_target,
                cfg=self.cfg,
            )
        if result["journal"]:
            self.journal("POSITION", result["journal"], {**ctx, **result})
        if not result.get("pending"):
            self._record_council_learning(
                ticker, result, "position_manage", ppo_exit, ppo_conf,
            )
        return result
    def poll_position_council(
        self, state: Dict[str, Any], df: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        ctx = state.get("ctx") or {}
        status, parsed = self._poll_live_status(
            ticker, "position_manage", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_position_manage_decision(
            parsed, status,
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            pnl_pct=float(ctx.get("pnl_pct", 0)),
            peak_pct=float(ctx.get("peak_pct", ctx.get("pnl_pct", 0))),
            current_stop=float(ctx.get("stop", 0)),
            current_target=float(ctx.get("target", 0)),
            mechanical_stop=state.get("mechanical_stop"),
            mechanical_target=state.get("mechanical_target"),
            cfg=self.cfg,
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "action": "HOLD",
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
            }
        action = str(merged.get("action", "HOLD")).upper()
        result = {
            "action": action,
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:120],
            "journal": str(merged.get("journal", ""))[:200],
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
            "council_agreement": merged.get("council_agreement"),
        }
        result = self._resolve_manage_prices(
            result, ctx, df,
            mechanical_stop=state.get("mechanical_stop"),
            mechanical_target=state.get("mechanical_target"),
        )
        result = self._apply_halim_exit_to_manage(
            result,
            ticker=ticker,
            fingerprint=fp,
            ppo_exit=bool(state.get("ppo_exit", False)),
            ppo_conf=float(state.get("ppo_conf", 0.5)),
            min_conf=float(state.get("min_conf", 0.55)),
            advisory_ctx={**ctx, "task": "position_manage"},
        )
        self._record_council_learning(
            ticker, result, "position_manage",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result
    def prefetch_position_manage(self, ctx: Dict[str, Any]) -> None:
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        fp = position_fingerprint(ticker, price, pnl_pct, stop, target)
        prompt = (
            f"Prefetch position manage {ticker} @ ${price:.4f} P&L {pnl_pct:+.2f}% "
            f"stop={stop:.4f} target={target:.4f}\n"
            f"{json.dumps(ctx, default=str)[:600]}\n"
            "Strategist only — action JSON, no stop/target prices."
        )
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt(
            "position_manage", {"request": prompt[:2000]}, self.cfg, mood, conf, lessons,
        )
        self._live_line.ring(ticker, "position_manage", full, fp, in_position=True)
    def decide_exit(self, ctx: Dict[str, Any], obs: Optional[np.ndarray] = None,
                    bar_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        ppo_exit, ppo_conf, ppo_reason = (False, 0.5, "")
        if obs is not None:
            action, conf, reason = self.ppo_action(obs, bar_df)
            ppo_exit = action == 2 and conf >= float(self.cfg.CONFIDENCE_THRESHOLD)
            ppo_conf, ppo_reason = conf, reason

        prompt = (
            f"FULL-TIME PROFIT MISSION: should we EXIT {ctx.get('ticker')} now to lock or cut?\n"
            f"{json.dumps(ctx, default=str)[:700]}\n"
            f"PPO exit signal: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:60]}\n"
            "Profit is the only main goal — exit into strength or cut losers to hunt again.\n"
            'JSON: {"exit":true/false,"confidence":0-1,"gut_feel":0-1,'
            '"reason":"why","journal":"exit log"}'
        )
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", ctx.get("current_px", 0)) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = exit_fingerprint(ticker, price, pnl_pct)
        stop = float(ctx.get("stop", 0) or 0)
        target = float(ctx.get("target", 0) or 0)
        peak_pct = float(ctx.get("peak_pct", pnl_pct) or pnl_pct)
        self._ring_halim_exit(
            ticker, fp,
            price=price, pnl_pct=pnl_pct, peak_pct=peak_pct,
            stop=stop, target=target,
            ppo_exit=ppo_exit, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            task="exit_decision",
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "exit_decision", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "exit_decision", full, fp, in_position=True)
            live = self._live_line.consume(ticker, "exit_decision", fp)
            merged = merge_exit_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_exit, ppo_conf, ppo_reason, min_conf,
                pnl_pct=pnl_pct,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "exit": False,
                    "fingerprint": fp,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "ctx": dict(ctx),
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                }
            should_exit = bool(merged.get("exit"))
            result = {
                "exit": should_exit,
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ppo_reason))[:200],
                "journal": str(merged.get("journal", ""))[:200],
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
                "council_agreement": merged.get("council_agreement"),
            }
            result = self._blend_halim_exit(
                result,
                ticker=ticker,
                fingerprint=fp,
                ppo_exit=ppo_exit,
                ppo_conf=ppo_conf,
                min_conf=min_conf,
                advisory_ctx={**ctx, "task": "exit_decision"},
            )
        else:
            out = self.think_json(prompt, ttl=1.0, task="exit_decision")
            should_exit = bool(out.get("exit", ppo_exit))
            if ppo_exit and self.full_control:
                should_exit = True
            result = {
                "exit": should_exit,
                "confidence": float(out.get("confidence", ppo_conf)),
                "reason": str(out.get("reason", ppo_reason)),
                "journal": str(out.get("journal", ""))[:200],
                "pending": False,
            }
        if result.get("exit") and not result.get("pending"):
            self.journal("EXIT_DECISION", result["journal"] or result["reason"], {**ctx, **result})
            self._record_council_learning(
                ticker, result, "exit_decision", ppo_exit, ppo_conf,
            )
        return result
    def poll_exit_council(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        ctx = state.get("ctx") or {}
        status, parsed = self._poll_live_status(
            ticker, "exit_decision", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_exit_decision(
            parsed, status,
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            pnl_pct=float(ctx.get("pnl_pct", 0)),
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "exit": False,
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
            }
        result = {
            "exit": bool(merged.get("exit")),
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:200],
            "journal": str(merged.get("journal", ""))[:200],
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
            "council_agreement": merged.get("council_agreement"),
        }
        result = self._blend_halim_exit(
            result,
            ticker=ticker,
            fingerprint=fp,
            ppo_exit=bool(state.get("ppo_exit", False)),
            ppo_conf=float(state.get("ppo_conf", 0.5)),
            min_conf=float(state.get("min_conf", 0.55)),
            advisory_ctx={**ctx, "task": "exit_decision"},
        )
        self._record_council_learning(
            ticker, result, "exit_decision",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result
    def decide_risk_exit(
        self,
        ctx: Dict[str, Any],
        risk_signal: str,
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
    ) -> Dict[str, Any]:
        """Council exit for risk-engine signals (trail profit/stop, early loss)."""
        ticker = str(ctx.get("ticker", "?"))
        price = float(ctx.get("price", 0) or 0)
        pnl_pct = float(ctx.get("pnl_pct", 0) or 0)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        fp = risk_signal_fingerprint(ticker, price, risk_signal)
        prompt = (
            f"Risk engine signal for {ticker}: {risk_signal}\n"
            f"{json.dumps(ctx, default=str)[:700]}\n"
            f"PPO exit: {ppo_exit} conf={ppo_conf:.2f} {ppo_reason[:80]}\n"
            "Mechanical profit hunts (spike_top, trailing_profit, hard_take_profit) "
            "should EXIT unless clear continuation evidence — be opportunistic.\n"
            'JSON: {"exit":true/false,"confidence":0-1,"reason":"why"}'
        )
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            mood, conf, lessons = self._mood_context()
            full = enrich_prompt(
                "risk_exit", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons,
            )
            self._live_line.ring(ticker, "risk_exit", full, fp, in_position=True)
            live = self._live_line.consume(ticker, "risk_exit", fp)
            merged = merge_risk_signal_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                risk_signal, ppo_exit, ppo_conf, ppo_reason, min_conf, pnl_pct=pnl_pct,
            )
            if merged.get("pending"):
                return {
                    "pending": True,
                    "exit": False,
                    "fingerprint": fp,
                    "risk_signal": risk_signal,
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "ctx": dict(ctx),
                    "pipeline": merged.get("pipeline", ""),
                    "reason": merged.get("reason", ""),
                }
            return {
                "exit": bool(merged.get("exit")),
                "confidence": float(merged.get("confidence", ppo_conf)),
                "reason": str(merged.get("reason", ""))[:200],
                "pipeline": merged.get("pipeline", ""),
                "pending": False,
            }
        return {
            "exit": bool(risk_signal or ppo_exit),
            "confidence": ppo_conf,
            "reason": risk_signal or ppo_reason,
            "pending": False,
        }
    def poll_risk_exit_council(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        ctx = state.get("ctx") or {}
        status, parsed = self._poll_live_status(
            ticker, "risk_exit", fp, float(state.get("started_at", time.time())),
        )
        merged = merge_risk_signal_decision(
            parsed, status,
            str(state.get("risk_signal", "")),
            bool(state.get("ppo_exit", False)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.55)),
            pnl_pct=float(ctx.get("pnl_pct", 0)),
        )
        if merged.get("pending"):
            return {"pending": True, "exit": False, "reason": merged.get("reason", "")}
        result = {
            "exit": bool(merged.get("exit")),
            "confidence": float(merged.get("confidence", state.get("ppo_conf", 0.5))),
            "reason": str(merged.get("reason", ""))[:200],
            "pipeline": merged.get("pipeline", ""),
            "pending": False,
        }
        self._record_council_learning(
            ticker, result, "risk_exit",
            bool(state.get("ppo_exit", False)), float(state.get("ppo_conf", 0.5)),
        )
        return result
