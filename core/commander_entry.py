#!/usr/bin/env python3
"""Extracted from ai_commander — commander entry."""

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
from core.notify import log


class CommanderEntryMixin:
    """Mixin — composed into AICommander."""

    def _build_entry_bracket(
        self,
        current_px: float,
        df: Optional[pd.DataFrame],
        *,
        equity: float,
        cash: float,
        deploy_cap: float,
        is_penny: bool,
        avg_vol: float,
        atr: Optional[float] = None,
    ) -> Any:
        """ATR-only brackets — Ollama never supplies stop/TP/shares."""
        if atr is None or atr <= 0:
            if df is not None and len(df) >= 5:
                atr = compute_atr(df, period=5)
            else:
                atr = current_px * float(getattr(self.cfg, "SCALP_MIN_STOP_PCT", 0.004))
        momentum = 0.0
        if df is not None and len(df) >= 10:
            try:
                momentum = float(compute_momentum_score(df, lookback=10))
            except Exception:
                momentum = 0.0
        shares_hint = max(1, int(deploy_cap / current_px)) if deploy_cap > 0 and current_px > 0 else 0
        return compute_atr_bracket(
            self.cfg,
            current_px,
            float(atr),
            equity=equity,
            cash=cash,
            deploy_cap=deploy_cap,
            shares_hint=shares_hint,
            momentum_score=momentum,
            is_penny=is_penny,
            avg_vol=avg_vol,
            use_fixed_risk=bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False)),
            max_risk_usd=get_trade_risk_usd(self.cfg, equity),
        )
    def _ring_halim_entry(
        self,
        ticker: str,
        fingerprint: str,
        *,
        price: float,
        spike: float,
        scan: float,
        ppo_buy: bool,
        ppo_conf: float,
        ppo_reason: str = "",
    ) -> None:
        try:
            self._halim_entry.ring(
                ticker,
                fingerprint,
                price=price,
                spike=spike,
                scan=scan,
                ppo_buy=ppo_buy,
                ppo_conf=ppo_conf,
                ppo_reason=ppo_reason,
            )
        except Exception as exc:
            log.debug(f"Halim entry ring: {exc}")
    def _await_halim_entry_slot(self, ticker: str, fingerprint: str) -> str:
        """Wait for async Halim entry LM before fast paths (replay + live)."""
        try:
            from core.halim_entry_line import halim_entry_await_sec
            wait = halim_entry_await_sec(self.cfg)
            if wait <= 0 or not hasattr(self, "_halim_entry"):
                return "skip"
            outcome = self._halim_entry.wait_for_completion(ticker, fingerprint, wait)
            if outcome == "ready":
                peek = self._halim_entry.peek(ticker)
                h_conf = float((peek.get("parsed") or {}).get("confidence", 0) or 0)
                h_enter = bool((peek.get("parsed") or {}).get("enter", False))
                log.info(
                    f"  🧠 Halim entry fresh {ticker.upper()} "
                    f"enter={h_enter} conf={h_conf:.0%} (await {wait:.1f}s)"
                )
            elif outcome == "timeout":
                log.info(
                    f"  🧠 Halim entry await timeout {ticker.upper()} ({wait:.1f}s) — fast path"
                )
            elif outcome in ("empty", "wrong_fp", "missing"):
                log.info(
                    f"  🧠 Halim entry await {outcome} {ticker.upper()} ({wait:.1f}s) — fast path"
                )
            return outcome
        except Exception as exc:
            log.info(f"  🧠 Halim entry await error {ticker.upper()}: {exc}")
            return "error"
    def _blend_halim_entry(
        self,
        decision: Dict[str, Any],
        *,
        ticker: str,
        fingerprint: str,
        ppo_buy: bool,
        ppo_conf: float,
        min_conf: float,
        advisory_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            from core.halim_entry_line import merge_halim_entry_advisory
            halim_live = self._halim_entry.consume(ticker, fingerprint)
            result = merge_halim_entry_advisory(
                decision,
                halim_live,
                ticker=ticker,
                ppo_buy=ppo_buy,
                ppo_conf=ppo_conf,
                min_conf=min_conf,
                cfg=self.cfg,
            )
            if result.get("halim_enter") is not None:
                ctx = advisory_ctx or {}
                try:
                    from core.halim_outcome_gold import register_entry_advisory
                    register_entry_advisory(
                        ticker,
                        halim_enter=bool(result["halim_enter"]),
                        halim_conf=float(result.get("halim_conf", 0)),
                        halim_reason=str(result.get("halim_reason", "")),
                        spike_ratio=float(ctx.get("spike_ratio", 0)),
                        scan_score=float(ctx.get("scan_score", 0)),
                        ppo_buy=ppo_buy,
                        ppo_conf=ppo_conf,
                        pipeline=str(result.get("pipeline", "")),
                        cfg=self.cfg,
                    )
                except Exception:
                    pass
            return result
        except Exception as exc:
            log.debug(f"Halim entry blend: {exc}")
            return decision
    def _resolve_halim_local_entry(
        self,
        *,
        halim_live: Dict[str, Any],
        quality: Optional[Dict[str, Any]],
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        min_conf: float,
        scan_score: float,
        spike_ratio: float,
        allow_pending_in_flight: bool = True,
    ) -> Dict[str, Any]:
        from core.smart_stack import build_halim_local_entry
        return build_halim_local_entry(
            self.cfg,
            halim_live=halim_live,
            quality=quality or {},
            ppo_action=ppo_action,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            min_conf=min_conf,
            scan_score=scan_score,
            spike_ratio=spike_ratio,
            allow_pending_in_flight=allow_pending_in_flight,
        )
    def _entry_council_prompt(
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
        chart_line: str = "",
        extra_lines: str = "",
    ) -> str:
        mctx = market_ctx or {}
        bid = mctx.get("bid")
        ask = mctx.get("ask")
        spread = mctx.get("spread_pct", 0)
        avg_vol = mctx.get("avg_volume", 0)
        open_n = int(account.get("open_positions", 0))
        max_pos = int(account.get("max_positions", effective_max_concurrent_positions(self.cfg)))
        held = account.get("held_tickers") or []
        deployed = float(account.get("deployed_usd", 0))
        micro = (account or {}).get("micro_forecast") or {}
        quality_line = ""
        if micro:
            try:
                from core.entry_quality import assess_entry_quality
                q = assess_entry_quality(
                    self.cfg, micro,
                    spike_ratio=spike_ratio,
                    scan_score=scan_score,
                    ppo_action=ppo_action,
                    ppo_conf=ppo_conf,
                    live_px=current_px,
                )
                quality_line = (
                    f"Quality: profit_prob={q.get('profit_probability', 0):.0%} "
                    f"fakeout_risk={q.get('fakeout_risk', 0):.0%} "
                    f"setup={q.get('setup_type', '?')} | {q.get('reason', '')[:80]}\n"
                    f"Micro: spike={micro.get('spike_likelihood', 0):.0%} "
                    f"fade={micro.get('fade_risk', 0):.0%} "
                    f"profit_run={micro.get('profit_run', 0):.0%} "
                    f"pred_1bar=${(micro.get('pred_1bar') or current_px):.4f}\n"
                )
            except Exception:
                pass
        return (
            f"DECIDE ENTRY for {ticker} @ ${current_px:.4f}\n"
            f"Volume spike {spike_ratio:.2f}x | Scan score {scan_score:.0f}\n"
            f"PPO entry signal: action={ppo_action} conf={ppo_conf:.2f} reason={ppo_reason[:80]}\n"
            f"{quality_line}"
            f"Account: equity ${account.get('equity', 0):,.0f} | cash ${account.get('cash', 0):,.0f} | "
            f"NAV ${account.get('nav', 0):,.0f}\n"
            f"Open: {open_n}/{max_pos} | Held: {', '.join(held) if held else 'none'} | "
            f"Deployed ${deployed:,.0f}\n"
            f"{extra_lines}"
            f"Bid ${bid or 0:.4f} Ask ${ask or 0:.4f} Spread {spread:.2%} | Avg vol {avg_vol:,.0f}\n"
            + (chart_line if chart_line else "")
            + (
                "PENNY STOCK: IB rejects large MARKET orders (error 2161). "
                "Use smaller size — max deploy $350, max ~1200 shares. Limit entry only.\n"
                if is_penny else ""
            )
            + "QUALITY-FIRST ENTRY — watch always, enter rarely. Loss is NOT acceptable.\n"
            "enter=true ONLY when YOU + PPO agree, profit_probability≥62%, score strong, fakeout low.\n"
            "Skip marginal spikes — one bad entry costs more than a missed trade. Profit is mandatory.\n"
            "Estimate profit_probability and fakeout risk; fakeout fades OK when bounce odds are clear.\n"
            "Math engine sets brackets from ATR after you decide enter/skip.\n"
            'JSON: {"enter":true/false,"confidence":0-1,"profit_probability":0-1,'
            '"fakeout_risk":0-1,"setup_type":"momentum_breakout|fakeout_fade|skip",'
            '"gut_feel":0-1,"intuition":"gut read",'
            '"reason":"why","journal":"first-person pilot log"}'
        )
    def prefetch_entry_decision(
        self,
        ticker: str,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        ppo_action: int = 0,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
        market_ctx: Optional[Dict[str, Any]] = None,
        df: Optional[pd.DataFrame] = None,
    ) -> None:
        """Keep council hotline warm — disabled in nanny mode (saves RPM)."""
        from core.council_nanny import prefetch_enabled
        if not prefetch_enabled(self.cfg):
            return
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        mctx = market_ctx or {}
        chart_line = self._chart_context_line(ticker, current_px, spike_ratio, scan_score)
        prompt = (
            f"DECIDE ENTRY for {ticker} @ ${current_px:.4f}\n"
            f"Volume spike {spike_ratio:.2f}x | Scan score {scan_score:.0f}\n"
            f"PPO entry signal: action={ppo_action} conf={ppo_conf:.2f} reason={ppo_reason[:80]}\n"
            f"Bid ${mctx.get('bid') or 0:.4f} Ask ${mctx.get('ask') or 0:.4f} "
            f"Spread {mctx.get('spread_pct', 0):.2%}\n"
            + (chart_line if chart_line else "")
            + "You are the pilot on the LIVE hotline. Judgment only — no stop, target, or shares.\n"
            '{"enter":true/false,"confidence":0-1,"gut_feel":0-1,"intuition":"brief",'
            '"reason":"why","journal":"log"}'
        )
        fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        mood, conf, lessons = self._mood_context()
        full = enrich_prompt("entry_decision", {"request": prompt[:2500]}, self.cfg, mood, conf, lessons)
        self._live_line.ring(
            ticker, "entry_decision", full, fp,
            spike_ratio=spike_ratio, scan_score=scan_score, for_learning=True,
        )
    def execute_ppo_led_entry_while_pending(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        account: Dict[str, Any],
        *,
        ppo_action: int,
        ppo_conf: float,
        ppo_reason: str,
        min_conf: float,
        pilot=None,
        market_ctx: Optional[Dict[str, Any]] = None,
        fingerprint: str = "",
        micro: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Enter on PPO now — Ollama still ringing for deferred learning."""
        mctx = market_ctx or {}
        micro = micro or account.get("micro_forecast") or {}
        deploy_cap = get_ai_deploy_budget(
            self.cfg, pilot,
            float(account.get("equity", 0)),
            float(account.get("cash", 0)),
            int(account.get("open_positions", 0)),
        )
        equity = float(account.get("equity", 0))
        max_risk = get_trade_risk_usd(self.cfg, equity)
        is_penny = current_px < float(getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0))
        avg_vol = float(mctx.get("avg_volume", 0))
        fp = fingerprint or entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        if not fingerprint:
            self._ring_entry_council_for_learning(
                ticker, current_px, spike_ratio, scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                account=account, market_ctx=mctx, is_penny=is_penny, df=df,
            )
        fast_out = {
            "enter": True,
            "confidence": max(ppo_conf, 0.58),
            "reason": (
                f"⚡ PPO-led (council pending): {ppo_reason or 'profit hunt'} | "
                f"spike={spike_ratio:.1f}x score={scan_score:.0f}"
            )[:200],
            "journal": f"PPO execute while council deliberates — {ticker}",
            "pipeline": "ppo:pending_lead",
            "pending": False,
        }
        decision = self._finalize_entry_decision(
            fast_out, ticker=ticker, current_px=current_px,
            spike_ratio=spike_ratio, scan_score=scan_score,
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
            use_fixed_risk=bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False)),
            is_penny=is_penny, avg_vol=avg_vol,
            df=df, equity=equity, cash=float(account.get("cash", 0)),
        )
        if decision.get("enter"):
            self._schedule_deferred_entry(
                ticker=ticker, fingerprint=fp, decision=decision,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                market_ctx=mctx,
            )
        return decision
    def decide_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        current_px: float,
        spike_ratio: float,
        scan_score: float,
        account: Dict[str, Any],
        obs: Optional[np.ndarray] = None,
        bar_df: Optional[pd.DataFrame] = None,
        pilot=None,
        market_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """AI decides entry, sizing, stop, target — guardrails clamp output."""
        ppo_action, ppo_conf, ppo_reason = (0, 0.5, "")
        if obs is not None:
            ppo_action, ppo_conf, ppo_reason = self.ppo_action(obs, bar_df, for_entry=True)
        consecutive_losses = int(account.get("consecutive_losses", 0) or 0)

        try:
            from core.war_account import (
                check_entry_allowed as war_entry_block,
                sniper_conf_bump,
                war_account_enabled,
                war_context_line,
            )
            if war_account_enabled(self.cfg):
                war_block = war_entry_block(self.cfg, ticker=ticker, pipeline="entry")
                if war_block:
                    log.info(f"  ⚔️ war:block {ticker.upper()} — {war_block}")
                    return {
                        "enter": False,
                        "confidence": ppo_conf,
                        "reason": war_block,
                        "pipeline": "war:veto",
                        "ppo_action": ppo_action,
                        "ppo_conf": ppo_conf,
                    }
            else:
                from core.live_trade_guard import check_entry_allowed
                guard_block = check_entry_allowed(ticker, self.cfg)
                if guard_block:
                    log.info(f"  🛡️ guard:block {ticker.upper()} — {guard_block}")
                    return {
                        "enter": False,
                        "confidence": ppo_conf,
                        "reason": guard_block,
                        "pipeline": "guard:cooldown",
                        "ppo_action": ppo_action,
                        "ppo_conf": ppo_conf,
                    }
        except Exception as exc:
            log.debug(f"Entry gate war/guard: {exc}")
            try:
                from core.live_trade_guard import check_entry_allowed
                guard_block = check_entry_allowed(ticker, self.cfg)
                if guard_block:
                    log.info(f"  🛡️ guard:block {ticker.upper()} — {guard_block}")
                    return {
                        "enter": False,
                        "confidence": ppo_conf,
                        "reason": guard_block,
                        "pipeline": "guard:cooldown",
                        "ppo_action": ppo_action,
                        "ppo_conf": ppo_conf,
                    }
            except Exception:
                pass

        try:
            from core.trading_copilot import copilot_blocks_entry, get_copilot_brief, copilot_caution_for_ticker
            blocked, creason = copilot_blocks_entry(self.cfg, ticker)
            if blocked:
                return {
                    "enter": False,
                    "confidence": ppo_conf,
                    "reason": f"Copilot SKIP — {creason}",
                    "pipeline": "copilot:veto",
                    "ppo_action": ppo_action,
                    "ppo_conf": ppo_conf,
                }
            brief = get_copilot_brief()
            boost = brief.conf_boost()
            if boost and ppo_conf > 0:
                ppo_conf = min(0.99, ppo_conf + boost)
        except Exception:
            pass

        deploy_cap = get_ai_deploy_budget(
            self.cfg, pilot,
            float(account.get("equity", 0)),
            float(account.get("cash", 0)),
            int(account.get("open_positions", 0)),
        )
        use_fixed_cap = bool(getattr(self.cfg, "USE_FIXED_DEPLOY_CAP", False))
        use_fixed_risk = bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False))
        equity = float(account.get("equity", 0))
        max_risk = get_trade_risk_usd(self.cfg, equity)
        min_conf = max(
            get_effective_confidence_threshold(self.cfg, pilot),
            min_confidence_for_state(self.cfg),
        )
        try:
            from core.trading_copilot import copilot_caution_for_ticker
            from core.live_trade_guard import guard_conf_bump, loss_context_for_prompt
            from core.war_account import war_context_line
            from core.sniper_execution import sniper_conf_bump_effective
            caution_bump = copilot_caution_for_ticker(self.cfg, ticker)
            if caution_bump:
                min_conf = min(0.95, min_conf + caution_bump)
            g_bump = guard_conf_bump(ticker)
            if g_bump:
                min_conf = min(0.95, min_conf + g_bump)
            s_bump = sniper_conf_bump_effective(
                self.cfg,
                spike_ratio=spike_ratio,
                scan_score=scan_score,
                ppo_action=ppo_action,
                ppo_conf=ppo_conf,
                ticker=ticker,
            )
            if s_bump:
                min_conf = min(0.95, min_conf + s_bump)
        except Exception:
            pass
        loss_ctx_line = ""
        war_line = ""
        macro_line = ""
        try:
            from core.live_trade_guard import loss_context_for_prompt
            loss_ctx_line = loss_context_for_prompt(ticker)
        except Exception:
            pass
        try:
            war_line = war_context_line(self.cfg)
        except Exception:
            pass
        try:
            from core.market_context import macro_context_line, macro_ticker_hint
            macro_line = macro_context_line()
            hint = macro_ticker_hint(ticker)
            if hint:
                macro_line = f"{macro_line}\n{hint}" if macro_line else hint
        except Exception:
            pass
        mctx = market_ctx or {}
        bid = mctx.get("bid")
        ask = mctx.get("ask")
        spread = mctx.get("spread_pct", 0)
        avg_vol = mctx.get("avg_volume", 0)
        penny_thr = float(getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0))
        is_penny = current_px < penny_thr
        open_n = int(account.get("open_positions", 0))
        max_pos = int(account.get("max_positions", effective_max_concurrent_positions(self.cfg)))
        held = account.get("held_tickers") or []
        deployed = float(account.get("deployed_usd", 0))

        cap_line = (
            f"Fixed deploy cap ${deploy_cap:.0f} | Fixed max risk ${max_risk:.0f}/trade\n"
            if use_fixed_cap and use_fixed_risk
            else (
                f"Fixed deploy cap ${deploy_cap:.0f} | Max risk ${max_risk:.0f}/trade\n"
                if use_fixed_cap
                else (
                    f"Fixed max risk ${max_risk:.0f}/trade | "
                    f"Budget hint ${deploy_cap:,.0f}/slot\n"
                    if use_fixed_risk
                    else (
                        f"AI sizes from full account (no fixed $1k cap) | "
                        f"Budget hint ${deploy_cap:,.0f}/slot\n"
                        f"ATR math engine sets stop/TP after council enter (no LLM prices) — "
                        f"equity ${equity:,.0f}\n"
                    )
                )
            )
        )
        if loss_ctx_line:
            cap_line = f"{cap_line}{loss_ctx_line}\n"
        if war_line:
            cap_line = f"{cap_line}{war_line}\n"
        if macro_line:
            cap_line = f"{cap_line}{macro_line}\n"

        fp = entry_fingerprint(ticker, current_px, spike_ratio, scan_score)
        self._ring_halim_entry(
            ticker, fp,
            price=current_px, spike=spike_ratio, scan=scan_score,
            ppo_buy=ppo_action == 1, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
        )
        micro = (account or {}).get("micro_forecast") or {}
        from core.entry_quality import assess_entry_quality, apply_ai_entry_quality
        quality = assess_entry_quality(
            self.cfg, micro,
            spike_ratio=spike_ratio,
            scan_score=scan_score,
            ppo_action=ppo_action,
            ppo_conf=ppo_conf,
            live_px=current_px,
            ticker=ticker,
        )
        account = dict(account or {})
        account["entry_quality"] = quality
        self._entry_quality_snapshot = quality
        gate_ctx = dict(account.get("smart_gate_context") or {})
        self._decide_entry_gate_ctx = gate_ctx
        gate_line = ""
        try:
            from core.smart_stack import format_gate_context_for_prompt
            gate_line = format_gate_context_for_prompt(gate_ctx)
            if gate_line:
                cap_line = f"{cap_line}{gate_line}\n"
        except Exception:
            pass
        halim_peek = self._halim_entry.peek(ticker) if hasattr(self, "_halim_entry") else {}
        halim_parsed = halim_peek.get("parsed") or {}
        halim_enter_flag = bool(halim_parsed.get("enter", False))
        halim_conf_flag = float(halim_parsed.get("confidence", 0) or 0)
        if int(ppo_action) != 1:
            log.debug(
                f"  🧠 PPO HOLD {ppo_conf:.0%} {ticker} — escalating to Halim+council"
            )
        from core.fast_execution import (
            should_spike_fast_entry,
            should_micro_fast_entry,
            should_disciplined_strong_entry,
            council_fast_sec,
            council_fast_min_score,
            council_fast_min_spike,
        )
        from core.capital_discipline import (
            allows_micro_fast_entry,
            allows_spike_fast_entry,
            allows_disciplined_spike_fast,
            capital_discipline_enabled,
        )
        from core.sniper_execution import should_sniper_flash_entry, should_sniper_strong_entry
        if should_sniper_flash_entry(
            self.cfg, spike_ratio, scan_score, ppo_action, ppo_conf, micro,
            ticker=ticker, consecutive_losses=consecutive_losses,
            live_px=float(current_px or 0),
            halim_enter=halim_enter_flag, halim_conf=halim_conf_flag,
        ):
            fast_out = {
                "enter": True,
                "confidence": max(ppo_conf, 0.55, min(scan_score / 70.0, 0.88)),
                "reason": (
                    f"🎯 SNIPER flash: vol={spike_ratio:.1f}x score={scan_score:.0f} "
                    f"PPO {ppo_conf:.0%} — no council wait"
                )[:200],
                "journal": f"Sniper flash hunt — {ticker}",
                "pipeline": "sniper:flash",
                "pending": False,
            }
            decision = self._finalize_entry_decision(
                fast_out, ticker=ticker, current_px=current_px,
                spike_ratio=spike_ratio, scan_score=scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                df=df, equity=equity, cash=float(account.get("cash", 0)),
            )
            return decision
        if should_sniper_strong_entry(
            self.cfg, spike_ratio, scan_score, ppo_action, ppo_conf, micro,
            ticker=ticker, consecutive_losses=consecutive_losses,
            live_px=float(current_px or 0),
        ):
            fast_out = {
                "enter": True,
                "confidence": max(ppo_conf, 0.58, min(scan_score / 75.0, 0.85)),
                "reason": (
                    f"🎯 SNIPER strong: vol={spike_ratio:.1f}x score={scan_score:.0f} "
                    f"PPO {ppo_conf:.0%} — lottery band"
                )[:200],
                "journal": f"Sniper strong hunt — {ticker}",
                "pipeline": "sniper:strong",
                "pending": False,
            }
            decision = self._finalize_entry_decision(
                fast_out, ticker=ticker, current_px=current_px,
                spike_ratio=spike_ratio, scan_score=scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                df=df, equity=equity, cash=float(account.get("cash", 0)),
            )
            return decision
        if allows_disciplined_spike_fast(
            self.cfg, scan_score, spike_ratio,
        ) and should_disciplined_strong_entry(
            self.cfg, spike_ratio, scan_score, ppo_action, ppo_conf, micro,
            ticker=ticker, consecutive_losses=consecutive_losses,
        ):
            fast_out = {
                "enter": True,
                "confidence": max(ppo_conf, 0.58, min(scan_score / 80.0, 0.85)),
                "reason": (
                    f"⚡ PPO strong-spike: score={scan_score:.0f} vol={spike_ratio:.1f}x "
                    f"| PPO {ppo_conf:.0%} (council nanny async)"
                )[:200],
                "journal": f"Disciplined profit hunt — {ticker}",
                "pipeline": "ppo:strong_spike",
                "pending": False,
            }
            decision = self._finalize_entry_decision(
                fast_out, ticker=ticker, current_px=current_px,
                spike_ratio=spike_ratio, scan_score=scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                df=df, equity=equity, cash=float(account.get("cash", 0)),
            )
            return decision
        self._await_halim_entry_slot(ticker, fp)
        if allows_micro_fast_entry(self.cfg) and should_micro_fast_entry(
            self.cfg, spike_ratio, scan_score, micro, ppo_action, ppo_conf,
            ticker=ticker,
        ):
            fp = self._ring_entry_council_for_learning(
                ticker, current_px, spike_ratio, scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                account=account, market_ctx=mctx, is_penny=is_penny, df=df,
                pipeline="ppo:micro_fast",
            )
            fast_out = {
                "enter": True,
                "confidence": max(ppo_conf, 0.58, min(scan_score / 80.0, 0.85)),
                "reason": (
                    f"⚡ PPO-led micro-fast: score={scan_score:.0f} micro={float(micro.get('spike_likelihood', 0)):.0%} "
                    f"vol={spike_ratio:.1f}x | PPO {ppo_conf:.0%} ({_deferred_gold_log_tag(self.cfg)})"
                )[:200],
                "journal": f"PPO profit hunt — {ticker}",
                "pipeline": "ppo:micro_fast",
                "pending": False,
            }
            decision = self._finalize_entry_decision(
                fast_out, ticker=ticker, current_px=current_px,
                spike_ratio=spike_ratio, scan_score=scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                df=df, equity=equity, cash=float(account.get("cash", 0)),
            )
            if fp:
                self._schedule_deferred_entry(
                    ticker=ticker, fingerprint=fp, decision=decision,
                    ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    market_ctx=mctx,
                )
            return decision
        self._await_halim_entry_slot(ticker, fp)
        if allows_spike_fast_entry(self.cfg) and should_spike_fast_entry(
            self.cfg, spike_ratio, scan_score, ppo_action, ppo_conf, micro,
        ):
            fp = self._ring_entry_council_for_learning(
                ticker, current_px, spike_ratio, scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                account=account, market_ctx=mctx, is_penny=is_penny, df=df,
                pipeline="ppo:spike_fast",
            )
            fast_out = {
                "enter": True,
                "confidence": max(ppo_conf, 0.58, min(scan_score / 80.0, 0.85)),
                "reason": (
                    f"⚡ PPO-led spike-fast: vol={spike_ratio:.1f}x score={scan_score:.0f} "
                    f"| PPO {ppo_conf:.0%} ({_deferred_gold_log_tag(self.cfg)})"
                )[:200],
                "journal": f"PPO fast execution — hunting spike on {ticker}",
                "pipeline": "ppo:spike_fast",
                "pending": False,
            }
            decision = self._finalize_entry_decision(
                fast_out, ticker=ticker, current_px=current_px,
                spike_ratio=spike_ratio, scan_score=scan_score,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                df=df, equity=equity, cash=float(account.get("cash", 0)),
            )
            if fp:
                self._schedule_deferred_entry(
                    ticker=ticker, fingerprint=fp, decision=decision,
                    ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    market_ctx=mctx,
                )
            return decision

        if (
            df is not None
            and len(df) >= 20
            and getattr(self.cfg, "CHART_VISION_ENTRY_ONLY", True)
        ):
            from core.council_nanny import nanny_mode_enabled
            if not nanny_mode_enabled(self.cfg):
                self.prefetch_chart_vision(ticker, df, current_px, spike_ratio, scan_score)
        chart_line = self._chart_context_line(ticker, current_px, spike_ratio, scan_score)
        pipeline_on = getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True)

        # Student proxy path — grows with brain maturity (before cloud council)
        if obs is not None:
            try:
                from core.brain_maturity import should_use_student_entry
                from core.hybrid_distiller import proxy_entry_decision
                if should_use_student_entry(self.cfg):
                    proxy_dec = proxy_entry_decision(
                        obs, spike_ratio, scan_score, mctx, self.cfg,
                    )
                    if proxy_dec is not None:
                        proxy_dec.setdefault("pipeline", "student:proxy")
                        proxy_dec["ppo_action"] = ppo_action
                        proxy_dec["ppo_conf"] = ppo_conf
                        proxy_dec["proxy_enter"] = bool(proxy_dec.get("enter"))
                        proxy_dec["proxy_conf"] = float(
                            proxy_dec.get("confidence", 0.5) or 0.5,
                        )
                        proxy_dec["proxy_reason"] = str(proxy_dec.get("reason", ""))[:200]
                        decision = self._finalize_entry_decision(
                            proxy_dec, ticker=ticker, current_px=current_px,
                            spike_ratio=spike_ratio, scan_score=scan_score,
                            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                            min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
                            use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
                            df=df, equity=equity, cash=float(account.get("cash", 0)),
                        )
                        if decision.get("enter"):
                            self._schedule_deferred_entry(
                                ticker=ticker, fingerprint=fp, decision=decision,
                                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                                market_ctx=mctx,
                            )
                        return decision
            except Exception as exc:
                log.debug(f"Student proxy entry: {exc}")

        prompt = self._entry_council_prompt(
            ticker, current_px, spike_ratio, scan_score,
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            account=account, market_ctx=mctx, is_penny=is_penny,
            chart_line=chart_line, extra_lines=cap_line,
        )
        if pipeline_on:
            mood, conf_m, lessons = self._mood_context()
            full = enrich_prompt(
                "entry_decision", {"request": prompt[:2500]}, self.cfg, mood, conf_m, lessons,
            )
            halim_live_pre = self._halim_entry.consume(ticker, fp)
            halim_st = halim_live_pre.get("status", "")
            h_parsed = halim_live_pre.get("parsed") or {}
            ring_teacher, teacher_why = False, "teacher:init"
            try:
                from core.smart_stack import smart_stack_enabled, should_ring_teacher_api
                if smart_stack_enabled(self.cfg):
                    ring_teacher, teacher_why = should_ring_teacher_api(
                        self.cfg,
                        ticker=ticker,
                        halim_status=halim_st,
                        halim_conf=float(h_parsed.get("confidence", 0) or 0),
                        ppo_action=ppo_action,
                        ppo_conf=ppo_conf,
                        scan_score=scan_score,
                        spike_ratio=spike_ratio,
                        disagreement=(
                            h_parsed.get("enter") is not None
                            and bool(h_parsed.get("enter")) != (ppo_action == 1)
                        ),
                    )
                else:
                    ring_teacher, teacher_why = True, "legacy_always_ring"
            except Exception:
                pass
            if ring_teacher:
                self._live_line.ring(
                    ticker, "entry_decision", full, fp,
                    spike_ratio=spike_ratio, scan_score=scan_score,
                )
                log.debug(f"  🧠 teacher ring {ticker}: {teacher_why}")
            else:
                log.info(f"  🧠 Halim local {ticker}: {teacher_why}")

            if not ring_teacher:
                local = self._resolve_halim_local_entry(
                    halim_live=halim_live_pre,
                    quality=quality,
                    ppo_action=ppo_action,
                    ppo_conf=ppo_conf,
                    ppo_reason=ppo_reason,
                    min_conf=min_conf,
                    scan_score=scan_score,
                    spike_ratio=spike_ratio,
                    allow_pending_in_flight=True,
                )
                if not local.get("pending"):
                    out = apply_ai_entry_quality(self.cfg, local, quality)
                    return self._finalize_entry_decision(
                        out,
                        ticker=ticker,
                        current_px=current_px,
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                        ppo_action=ppo_action,
                        ppo_conf=ppo_conf,
                        ppo_reason=ppo_reason,
                        min_conf=min_conf,
                        deploy_cap=deploy_cap,
                        max_risk=max_risk,
                        use_fixed_risk=use_fixed_risk,
                        is_penny=is_penny,
                        avg_vol=avg_vol,
                        df=df,
                        equity=equity,
                        cash=float(account.get("cash", 0)),
                        gate_context=gate_ctx,
                    )
                return {
                    "pending": True,
                    "enter": False,
                    "fingerprint": fp,
                    "ppo_action": ppo_action,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "spike_ratio": spike_ratio,
                    "scan_score": scan_score,
                    "pipeline": local.get("pipeline", "halim:in_flight"),
                    "reason": local.get("reason", ""),
                    "local_only": True,
                    "teacher_rung": False,
                }

            live = self._live_line.consume(ticker, "entry_decision", fp)
            merged = merge_entry_decision(
                live.get("parsed") or {},
                live.get("status", "missing"),
                ppo_action, ppo_conf, ppo_reason, min_conf,
                scan_score=scan_score, spike_ratio=spike_ratio,
                quality=quality, cfg=self.cfg,
                ticker=ticker, consecutive_losses=consecutive_losses,
            )
            out = self._stamp_council_signals(merged, live.get("parsed") or {})
            if out.get("pending"):
                # Hard-case API slow — try Halim/quality before blocking
                local = self._resolve_halim_local_entry(
                    halim_live=halim_live_pre,
                    quality=quality,
                    ppo_action=ppo_action,
                    ppo_conf=ppo_conf,
                    ppo_reason=ppo_reason,
                    min_conf=min_conf,
                    scan_score=scan_score,
                    spike_ratio=spike_ratio,
                    allow_pending_in_flight=False,
                )
                if not local.get("pending") and local.get("enter"):
                    out = apply_ai_entry_quality(self.cfg, local, quality)
                    out["pipeline"] = f"{local.get('pipeline', 'halim:local')}+teacher_wait"
                    return self._finalize_entry_decision(
                        out,
                        ticker=ticker,
                        current_px=current_px,
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                        ppo_action=ppo_action,
                        ppo_conf=ppo_conf,
                        ppo_reason=ppo_reason,
                        min_conf=min_conf,
                        deploy_cap=deploy_cap,
                        max_risk=max_risk,
                        use_fixed_risk=use_fixed_risk,
                        is_penny=is_penny,
                        avg_vol=avg_vol,
                        df=df,
                        equity=equity,
                        cash=float(account.get("cash", 0)),
                        gate_context=gate_ctx,
                    )
                return {
                    "pending": True,
                    "enter": False,
                    "fingerprint": fp,
                    "ppo_action": ppo_action,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": min_conf,
                    "spike_ratio": spike_ratio,
                    "scan_score": scan_score,
                    "pipeline": out.get("pipeline", ""),
                    "reason": out.get("reason", ""),
                    "local_only": False,
                    "teacher_rung": True,
                }
            enter = bool(out.get("enter"))
            confidence = float(out.get("confidence", ppo_conf))
            if live.get("status") == "fresh":
                gut_feel = float(out.get("gut_feel", 0.5) or 0.5)
                intuition = str(out.get("intuition", ""))[:120]
                from core.capital_discipline import capital_discipline_enabled
                if not capital_discipline_enabled(self.cfg):
                    enter, gut_note = apply_gut_override(enter, gut_feel, ppo_action, ppo_conf, min_conf)
                    if gut_note:
                        out["reason"] = f"{out.get('reason', '')} | {gut_note}".strip(" |")
                if intuition:
                    out["journal"] = f"{intuition} — {out.get('journal', '')}"[:300]
        else:
            out = self.think_json(
                prompt, cache_key=f"entry_{ticker}",
                ttl=float(getattr(self.cfg, "COUNCIL_MIN_CALL_INTERVAL_SEC", 0.5)),
                task="entry_decision",
            )
            if not out:
                enter = ppo_action == 1 and ppo_conf >= min_conf
                confidence = ppo_conf
                out = {
                    "reason": ppo_reason or f"PPO ensemble conf={ppo_conf:.0%} (council offline)",
                    "journal": f"PPO ensemble: spike {spike_ratio:.1f}x score {scan_score:.0f}",
                }
            else:
                enter = bool(out.get("enter", ppo_action == 1))
                confidence = float(out.get("confidence", ppo_conf) or ppo_conf)
                out = self._stamp_council_signals(out, out)
                gut_feel = float(out.get("gut_feel", 0.5) or 0.5)
                intuition = str(out.get("intuition", ""))[:120]
                enter, gut_note = apply_gut_override(enter, gut_feel, ppo_action, ppo_conf, min_conf)
                if gut_note:
                    out["reason"] = f"{out.get('reason', '')} | {gut_note}".strip(" |")
                if intuition:
                    out["journal"] = f"{intuition} — {out.get('journal', '')}"[:300]
                if self.full_control and not enter and ppo_action == 1 and ppo_conf >= min_conf * 0.85:
                    enter = True
                    confidence = max(confidence, ppo_conf)
                    out["reason"] = f"PPO+AI ensemble: {ppo_reason}"

        # PPO-led momentum — only when capital discipline is off
        if not enter and not capital_discipline_enabled(self.cfg) and getattr(
            self.cfg, "AI_FAST_EXECUTION", True
        ):
            if should_spike_fast_entry(self.cfg, spike_ratio, scan_score, ppo_action, ppo_conf):
                enter = True
                confidence = max(confidence, 0.58)
                out["reason"] = (
                    f"⚡ PPO spike hunt: vol={spike_ratio:.1f}x score={scan_score:.0f} "
                    f"({_deferred_gold_log_tag(self.cfg)})"
                )[:200]
                out["pipeline"] = "ppo:spike_fast_fallback"
            elif ppo_action == 1 and ppo_conf >= min_conf * 0.85:
                enter = True
                confidence = max(confidence, ppo_conf)
                out["reason"] = f"PPO buy lead: {ppo_reason or 'ensemble'} ({_deferred_gold_log_tag(self.cfg)})"
                out["pipeline"] = "ppo:buy_lead"
        if (
            not enter
            and not capital_discipline_enabled(self.cfg)
            and not is_ai_unlimited(self.cfg)
            and not self.council_mode
        ):
            if spike_ratio >= 1.5 and scan_score >= 35:
                enter = True
                confidence = max(confidence, 0.55)
                out["reason"] = (
                    f"Momentum entry: spike={spike_ratio:.1f}x score={scan_score:.0f} | "
                    f"{out.get('reason', ppo_reason or '')}"
                )[:200]
            elif spike_ratio >= 1.3 and scan_score >= 45 and ppo_conf >= min_conf * 0.75:
                enter = True
                confidence = max(confidence, ppo_conf)
                out["reason"] = (
                    f"Scanner+AI: score={scan_score:.0f} spike={spike_ratio:.1f}x | "
                    f"{ppo_reason or ''}"
                )[:200]
            elif ppo_action == 1 and ppo_conf >= min_conf:
                enter = True
                confidence = max(confidence, ppo_conf)
                out["reason"] = f"PPO buy signal: {ppo_reason or 'ensemble confirmed'}"

        out["enter"] = enter
        out["confidence"] = confidence
        if pipeline_on and live.get("status") == "fresh":
            parsed_live = live.get("parsed") or {}
            if parsed_live.get("profit_probability") is not None:
                out["ollama_profit_probability"] = parsed_live.get("profit_probability")
        out = apply_ai_entry_quality(self.cfg, out, quality)
        decision = self._finalize_entry_decision(
            out,
            ticker=ticker,
            current_px=current_px,
            spike_ratio=spike_ratio,
            scan_score=scan_score,
            ppo_action=ppo_action,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            min_conf=min_conf,
            deploy_cap=deploy_cap,
            max_risk=max_risk,
            use_fixed_risk=use_fixed_risk,
            is_penny=is_penny,
            avg_vol=avg_vol,
            df=df,
            equity=equity,
            cash=float(account.get("cash", 0)),
        )
        if decision.get("enter"):
            self._schedule_deferred_entry(
                ticker=ticker, fingerprint=fp, decision=decision,
                ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                market_ctx=mctx,
            )
        return decision
    def poll_entry_council(
        self, state: Dict[str, Any], df: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """Non-blocking poll — Ollama+PPO council resolves when hotline is fresh."""
        ticker = str(state["ticker"])
        fp = str(state["fingerprint"])
        live = self._live_line.consume(ticker, "entry_decision", fp)
        status = live.get("status", "missing")
        parsed = live.get("parsed") or {}
        age = time.time() - float(state.get("started_at", time.time()))
        in_flight_age = float(live.get("age_sec", 0) or 0)
        from core.fast_execution import (
            council_fast_sec,
            council_fast_min_score,
            council_fast_min_spike,
            council_max_wait_sec,
            should_micro_fast_entry,
        )
        max_wait = council_max_wait_sec(self.cfg)
        fast_sec = council_fast_sec(self.cfg)
        fast_score = council_fast_min_score(self.cfg)
        fast_spike = council_fast_min_spike(self.cfg)
        scan_score = float(state.get("scan_score", 0))
        spike_ratio = float(state.get("spike_ratio", 1.0))
        micro = state.get("micro_forecast") or {}
        local_only = bool(state.get("local_only", False))
        teacher_rung = bool(state.get("teacher_rung", True))
        quality = (state.get("account") or {}).get("entry_quality")

        if local_only or (not teacher_rung and status in ("missing", "empty", "in_flight")):
            halim_live = self._halim_entry.consume(ticker, fp)
            wait_halim = age < fast_sec
            local = self._resolve_halim_local_entry(
                halim_live=halim_live,
                quality=quality,
                ppo_action=int(state.get("ppo_action", 0)),
                ppo_conf=float(state.get("ppo_conf", 0.5)),
                ppo_reason=str(state.get("ppo_reason", "")),
                min_conf=float(state.get("min_conf", 0.5)),
                scan_score=scan_score,
                spike_ratio=spike_ratio,
                allow_pending_in_flight=wait_halim,
            )
            if not local.get("pending"):
                from core.entry_quality import apply_ai_entry_quality
                local = apply_ai_entry_quality(self.cfg, local, quality)
                return self._finalize_entry_decision(
                    local,
                    ticker=ticker,
                    current_px=float(state.get("current_px", 0)),
                    spike_ratio=spike_ratio,
                    scan_score=scan_score,
                    ppo_action=int(state.get("ppo_action", 0)),
                    ppo_conf=float(state.get("ppo_conf", 0.5)),
                    ppo_reason=str(state.get("ppo_reason", "")),
                    min_conf=float(state.get("min_conf", 0.5)),
                    deploy_cap=get_ai_deploy_budget(
                        self.cfg, state.get("pilot"),
                        float((state.get("account") or {}).get("equity", 0)),
                        float((state.get("account") or {}).get("cash", 0)),
                        int((state.get("account") or {}).get("open_positions", 0)),
                    ),
                    max_risk=get_trade_risk_usd(
                        self.cfg, float((state.get("account") or {}).get("equity", 0)),
                    ),
                    use_fixed_risk=bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False)),
                    is_penny=float(state.get("current_px", 0)) < float(
                        getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0),
                    ),
                    avg_vol=float((state.get("market_ctx") or {}).get("avg_volume", 0)),
                    df=df,
                    equity=float((state.get("account") or {}).get("equity", 0)),
                    cash=float((state.get("account") or {}).get("cash", 0)),
                    gate_context=(state.get("account") or {}).get("smart_gate_context"),
                )
            if local.get("pending"):
                return {
                    "pending": True,
                    "enter": False,
                    "reason": local.get("reason", ""),
                    "pipeline": local.get("pipeline", "halim:in_flight"),
                }

        if status in ("in_flight", "missing", "empty") and max(in_flight_age, age) >= fast_sec:
            promote_scanner = False
            if scan_score >= fast_score and spike_ratio >= fast_spike:
                promote_scanner = True
            elif should_micro_fast_entry(
                self.cfg, spike_ratio, scan_score, micro, ticker=ticker,
            ):
                promote_scanner = True
                spike_ratio = max(spike_ratio, float(micro.get("vol_accel", spike_ratio)))
                state["spike_ratio"] = spike_ratio
            if promote_scanner:
                try:
                    from core.live_trade_guard import check_fast_entry_bypass
                    block = check_fast_entry_bypass(
                        self.cfg,
                        ticker=ticker,
                        ppo_action=int(state.get("ppo_action", 0)),
                        ppo_conf=float(state.get("ppo_conf", 0.5)),
                        consecutive_losses=int((state.get("account") or {}).get("consecutive_losses", 0)),
                        pipeline="council:scanner_fast",
                    )
                    if not block:
                        status = "scanner_fast"
                        parsed = {}
                except Exception:
                    status = "scanner_fast"
                    parsed = {}
        elif status != "fresh" and age > max_wait:
            status = "timeout"
            parsed = {}
        merged = merge_entry_decision(
            parsed,
            status,
            int(state.get("ppo_action", 0)),
            float(state.get("ppo_conf", 0.5)),
            str(state.get("ppo_reason", "")),
            float(state.get("min_conf", 0.5)),
            scan_score=float(state.get("scan_score", 0)),
            spike_ratio=float(state.get("spike_ratio", 1.0)),
            quality=(state.get("account") or {}).get("entry_quality"),
            cfg=self.cfg,
            ticker=ticker,
            consecutive_losses=int((state.get("account") or {}).get("consecutive_losses", 0)),
        )
        if merged.get("pending"):
            return {
                "pending": True,
                "enter": False,
                "reason": merged.get("reason", ""),
                "pipeline": merged.get("pipeline", ""),
            }
        current_px = float(state.get("current_px", 0))
        mctx = state.get("market_ctx") or {}
        account = state.get("account") or {}
        pilot = state.get("pilot")
        deploy_cap = get_ai_deploy_budget(
            self.cfg, pilot,
            float(account.get("equity", 0)),
            float(account.get("cash", 0)),
            int(account.get("open_positions", 0)),
        )
        use_fixed_risk = bool(getattr(self.cfg, "USE_FIXED_RISK_CAP", False))
        equity = float(account.get("equity", 0))
        max_risk = get_trade_risk_usd(self.cfg, equity)
        min_conf = float(state.get("min_conf", 0.5))
        ppo_action = int(state.get("ppo_action", 0))
        ppo_conf = float(state.get("ppo_conf", 0.5))
        ppo_reason = str(state.get("ppo_reason", ""))
        is_penny = current_px < float(getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0))
        avg_vol = float(mctx.get("avg_volume", 0))
        from core.entry_quality import apply_ai_entry_quality
        quality = (account or {}).get("entry_quality")
        if status == "fresh" and parsed.get("profit_probability") is not None:
            merged["ollama_profit_probability"] = parsed.get("profit_probability")
        merged = apply_ai_entry_quality(self.cfg, merged, quality)
        return self._finalize_entry_decision(
            merged, ticker=ticker, current_px=current_px,
            spike_ratio=float(state.get("spike_ratio", 1)),
            scan_score=float(state.get("scan_score", 0)),
            ppo_action=ppo_action, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
            min_conf=min_conf, deploy_cap=deploy_cap, max_risk=max_risk,
            use_fixed_risk=use_fixed_risk, is_penny=is_penny, avg_vol=avg_vol,
            df=df, equity=equity, cash=float(account.get("cash", 0)),
            gate_context=(account or {}).get("smart_gate_context"),
        )
