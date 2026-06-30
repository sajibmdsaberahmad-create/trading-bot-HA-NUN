#!/usr/bin/env python3
"""Extracted from scalper_runner — scalper exit executor."""

from __future__ import annotations

from core.scalper_mixin_imports import *  # noqa: F403

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    pass


class ScalperExitMixin:
    """Mixin — composed into ScalperRunner."""

    def _service_tick_position_exit(self, ticker: str, price: float) -> None:
        """Sub-second micro profit/loss exit on tick (mechanical, no council wait)."""
        can_trade, _ = can_trade_now(self.cfg)
        if not can_trade:
            return
        if not getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True):
            return
        if not self._load_position_context(ticker):
            return
        if price > self._position_peak:
            self._position_peak = price
            if self.risk.plan:
                self.risk.plan.peak_price = max(self.risk.plan.peak_price, price)

        entry_px = self._entry_price
        if entry_px <= 0 or self.shares <= 0:
            return
        pnl_pct = (price / entry_px) - 1.0
        min_hold = effective_min_hold_for_exit(self.cfg, pnl_pct)
        opened = getattr(self, "_position_opened_at", 0.0)
        if min_hold > 0 and opened and (time.time() - opened) < min_hold:
            return

        _, _, _, forecast = self._resolve_live_bars(ticker, min_bars=6)
        fade_thr = float(getattr(self.cfg, "MICRO_FADE_EXIT", 0.55))
        loss_thr = float(getattr(self.cfg, "MICRO_LOSS_EXIT", 0.58))

        if (
            pnl_pct > float(getattr(self.cfg, "IN_PROFIT_MANAGE_PNL_PCT", 0.003))
            and forecast.get("fade_risk", 0) >= fade_thr
            and forecast.get("dir", 0) <= 0
        ):
            if self._execute_mechanical_profit_exit(
                price, f"tick_micro_fade:{forecast.get('fade_risk', 0):.2f}", defer=True,
            ):
                self._save_position_context(ticker)
                return

        if pnl_pct < -0.002 and forecast.get("loss_pressure", 0) >= loss_thr and forecast.get("dir", 0) < 0:
            log.info(
                f"  ⚡ TICK LOSS EXIT {ticker}: ${price:.4f} | "
                f"pressure={forecast.get('loss_pressure', 0):.2f}"
            )
            self._exit_position(price, "tick_micro_loss", ticker=ticker, defer=True)
            self._save_position_context(ticker)
    def _request_deferred_exit(self, ticker: str, price: float, reason: str) -> None:
        """Queue exit for main loop — safe when called from IB tick callbacks."""
        ticker = (ticker or "").upper()
        if not ticker or ticker not in self._position_slots:
            return
        if ticker in self._pending_closes or ticker in self._deferred_exits:
            return
        self._deferred_exits[ticker] = {
            "price": float(price),
            "reason": str(reason),
            "requested_at": time.time(),
        }
    def _service_deferred_exits(self) -> None:
        if not self._deferred_exits:
            return
        can_trade, _ = can_trade_now(self.cfg)
        if not can_trade:
            return
        for ticker, req in list(self._deferred_exits.items()):
            if ticker in self._pending_closes:
                self._deferred_exits.pop(ticker, None)
                continue
            if ticker not in self._position_slots:
                self._deferred_exits.pop(ticker, None)
                continue
            self._deferred_exits.pop(ticker, None)
            self._exit_position(
                float(req.get("price", 0)),
                str(req.get("reason", "deferred_exit")),
                ticker=ticker,
            )
    def _monitor_all_open_positions(self):
        for ticker in list(self._position_slots.keys()):
            loaded = False
            try:
                if not self._load_position_context(ticker):
                    continue
                loaded = True
                px, trusted = self._resolve_monitor_price(ticker, self._entry_price)
                if px > 0:
                    self._live_position_monitor(px, price_trusted=trusted)
            except Exception as exc:
                log.error(f"Position monitor failed for {ticker}: {exc}")
            finally:
                if loaded:
                    self._save_position_context(ticker)
        self._refresh_aggregate_position_state()

    def _risk_plan_sane_for_tick(self, current_px: float) -> bool:
        return risk_plan_sane_for_tick(
            self.risk.plan,
            entry_price=self._entry_price,
            shares=self.shares,
            current_px=current_px,
        )

    def _detect_all_exits(self):
        if not getattr(self.cfg, "USE_MULTI_POSITION", True):
            self._detect_exit(self._latest_price())
            return
        for ticker in list(self._position_slots.keys()):
            loaded = False
            try:
                if not self._load_position_context(ticker):
                    continue
                loaded = True
                px, _trusted = self._resolve_monitor_price(ticker, self._entry_price)
                if px > 0:
                    self._detect_exit(px)
            finally:
                if loaded:
                    self._save_position_context(ticker)
        self._refresh_aggregate_position_state()

    def _credit_exit_proceeds(self, quantity: float, exit_px: float):
        """Return sale proceeds to bot cash and refresh NAV."""
        proceeds = float(quantity) * exit_px * (1 - self.cfg.TRANSACTION_COST_PCT)
        self.bot_cash += proceeds
        self.bot_nav = self.bot_cash
    def _detect_exit(self, current_px: float):
        """Detect if position was closed (by bracket or manually) — reconcile IB fill async."""
        if self._prev_shares > 0 and self.shares == 0:
            opened_at = getattr(self, "_position_opened_at", 0.0)
            if opened_at and (time.time() - opened_at) < 60.0:
                return
            closed_ticker = (self.current_ticker or "").upper()
            if not closed_ticker:
                self._prev_shares = self.shares
                return
            bracket = self._bracket_by_ticker.get(closed_ticker) or self.bracket_handle
            self._enqueue_pending_close(
                closed_ticker,
                "bracket_exit",
                current_px,
                event="trade_closed",
                bracket=bracket,
                shares=self._prev_shares,
            )
            self._clear_closed_position_state(closed_ticker)
        self._prev_shares = self.shares
    def _ensure_position_stream(self, ticker: str):
        """Dedicated tick stream on open position — never stop monitoring after entry."""
        if not ticker:
            return
        self._active_stream_ticker = ticker
        self._ensure_target_stream(ticker, mode="tick")
    def _build_trade_close_record(
        self,
        ticker: str,
        quote_exit_px: float,
        reason: str = "",
        *,
        flatten_trade=None,
        bracket: Optional[BracketHandle] = None,
    ) -> Dict[str, Any]:
        """Resolve IB entry/exit fills and build a round-trip trade record."""
        slot = dict(self._position_slots.get(ticker, {}))
        entry_quote = float(slot.get("entry_price") or self._entry_price or 0)
        slot_entry = float(slot.get("entry_fill_px") or entry_quote)
        shares = float(slot.get("shares") or self._prev_shares or self.shares or 0)
        opened_at = float(slot.get("opened_at") or getattr(self, "_position_opened_at", 0))
        cache = self._fill_cache()
        entry_fill, entry_ok = resolve_entry_from_ib(
            self.ib, cache,
            symbol=ticker,
            slot_entry_fill=slot_entry,
            slot_entry_quote=entry_quote,
            opened_at=opened_at,
        )
        exit_fill, exit_ok = resolve_exit_from_ib(
            self.ib, cache,
            symbol=ticker,
            flatten_trade=flatten_trade,
            bracket=bracket or self._bracket_by_ticker.get(ticker) or self.bracket_handle,
            quote_px=quote_exit_px,
            since_ts=opened_at,
            entry_fill=entry_fill,
        )
        if ib_fill_strict(self.cfg) and not exit_ok:
            return {}
        rec = build_round_trip_record(
            ticker=ticker,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            quote_entry=entry_quote,
            quote_exit=quote_exit_px,
            shares=shares,
            exit_reason=reason,
            limit_px=slot.get("limit_px"),
            entry_mode=str(slot.get("entry_mode", "")),
            regime=str(slot.get("regime") or getattr(self, "_last_entry_regime", "")),
            hold_sec=max(0.0, time.time() - opened_at) if opened_at else 0.0,
            peak_px=float(slot.get("peak") or self._position_peak or 0),
            stop_px=float(slot.get("stop") or self._position_stop or 0),
            target_px=float(slot.get("target") or self._position_target or 0),
        )
        rec["fill_confirmed"] = exit_ok and (entry_ok or entry_fill > 0)
        rec["entry_fill_confirmed"] = entry_ok
        rec["exit_fill_confirmed"] = exit_ok
        return rec
    def _enqueue_pending_close(
        self,
        ticker: str,
        reason: str,
        quote_exit_px: float,
        *,
        event: str = "trade_closed",
        flatten_trade=None,
        bracket=None,
        slot: Optional[Dict] = None,
        shares: Optional[float] = None,
    ) -> None:
        """Queue IB fill reconciliation — notifications fire after confirmed fill."""
        ticker = (ticker or "").upper()
        if not ticker:
            return
        key = f"{ticker}:{time.time():.3f}"
        snap = snapshot_slot(slot or self._position_slots.get(ticker, {}))
        if not snap.get("entry_fill_px") and self._entry_price > 0 and self.current_ticker == ticker:
            snap.setdefault("entry_fill_px", self._entry_price)
            snap.setdefault("entry_price", self._entry_price)
        qty = float(shares if shares is not None else snap.get("shares") or self._prev_shares or self.shares or 0)
        opened = float(snap.get("opened_at") or getattr(self, "_position_opened_at", 0))
        self._pending_closes[key] = PendingClose(
            ticker=ticker,
            reason=reason,
            quote_exit_px=quote_exit_px,
            slot=snap,
            shares=qty,
            opened_at=opened,
            event=event,
            flatten_trade=flatten_trade,
            bracket=bracket,
        )
    def _service_pending_closes(self) -> None:
        """Instant IB cache lookup each tick — zero sleep, zero throttle; notify when fill lands."""
        if not self._pending_closes:
            return
        cache = self._fill_cache()
        fallback_sec = float(getattr(self.cfg, "FILL_RECONCILE_FALLBACK_SEC", 8.0))
        force_sec = float(getattr(self.cfg, "IB_FILL_FORCE_SEC", 120.0))
        now = time.time()

        for key, pending in list(self._pending_closes.items()):
            age = now - pending.started_at
            force = age >= fallback_sec
            if ib_fill_strict(self.cfg) and age < force_sec:
                force = False
            trade_rec = build_close_record(
                pending, self.ib, cache, force=force, cfg=self.cfg,
            )
            if trade_rec is None:
                continue
            if not self._finalize_closed_trade(trade_rec, pending):
                continue
            self._pending_closes.pop(key, None)
    def _finalize_closed_trade(self, trade_rec: Dict[str, Any], pending: PendingClose) -> bool:
        """Notify and learn using IB-confirmed fills. Returns False if still awaiting IB."""
        ticker = pending.ticker
        exit_fill = float(trade_rec.get("exit_fill") or trade_rec.get("exit", 0))
        pnl = float(trade_rec.get("pnl_usd", 0))
        pnl_pct = float(trade_rec.get("pnl_pct", 0))
        result = trade_rec.get("result", "loss")
        confirmed = bool(trade_rec.get("fill_confirmed"))
        qty = float(trade_rec.get("shares") or pending.shares or 0)

        if not pending.credited and qty > 0 and exit_fill > 0:
            if confirmed or not ib_fill_strict(self.cfg):
                self._credit_exit_proceeds(qty, exit_fill)
                pending.credited = True
            else:
                log.warning(
                    f"  ⏳ EXIT {ticker}: awaiting IB fill confirmation "
                    f"(quote ${pending.quote_exit_px:.4f}) — cash/P&L deferred"
                )
                return False

        if not confirmed and ib_fill_strict(self.cfg):
            log.warning(
                f"  ⏳ EXIT {ticker}: IB fill not confirmed — finalize deferred"
            )
            return False

        tag = "IB fill" if confirmed else "est. fill"
        log.info(
            f"📕 EXIT {ticker} ({tag}): ${exit_fill:.4f} "
            f"(quote ${pending.quote_exit_px:.4f}) | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | "
            f"{result.upper()} | {pending.reason[:60]}"
        )

        exit_ctx = {
            "ticker": ticker,
            "pnl_usd": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "result": result,
            "entry_fill": trade_rec.get("entry_fill"),
            "exit_fill": exit_fill,
            "fill_confirmed": confirmed,
            "pilot_level": self.pilot.state.level if hasattr(self, "pilot") else "Cadet",
        }
        notify_event = "early_exit" if pending.event == "early_exit" else "trade_closed"
        fallback = (
            f"📕 EXIT {ticker} | P&L ${pnl:+.2f} ({pnl_pct:+.1f}%) | {result.upper()}\n"
            f"Entry ${trade_rec.get('entry_fill', 0):.4f} → Exit ${exit_fill:.4f} ({tag})"
        )
        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, notify_event,
                self._notify_context(exit_ctx),
                fallback,
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info(
                f"📕 HANOON EXIT\nTicker: {ticker}\n"
                f"Exit fill: ${exit_fill:.4f}\n"
                f"Entry fill: ${trade_rec.get('entry_fill', 0):.4f}\n"
                f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                f"Result: {result.upper()}"
            )

        self._apply_trade_close_learning(trade_rec, ticker)
        try:
            from core.lottery_bank import on_trade_closed
            on_trade_closed(
                self.cfg, self.notifier, trade_rec,
                slot=pending.slot if pending else {},
            )
        except Exception as exc:
            log.debug(f"Lottery bank close: {exc}")
        if pending.event == "early_exit" and is_mechanical_profit_exit(pending.reason):
            record_profit_hunt_learning(
                self.cfg,
                event=pending.reason.split(":")[0],
                ticker=ticker,
                context={"reason": pending.reason, **getattr(self, "_profit_hunt_spike_ctx", {})},
                pnl_usd=pnl,
                won=pnl > 0,
            )
        self.risk.record_trade_result(pnl)
        if self.risk.needs_learning_session:
            self._service_loss_streak_learning()
        try:
            self.shadow_circuit.on_live_trade_closed(pnl, self.account_equity)
        except Exception:
            pass

        try:
            pnl_usd = round(pnl, 2)
            self.pilot.complete_flight(exit_fill, pnl_usd, round(pnl_pct, 2) / 100, pending.reason[:80])
            if pnl > 0:
                self.pilot.record_pattern_match("win", True, pnl_usd)
            else:
                self.pilot.record_pattern_match("loss", False, pnl_usd)
        except Exception:
            pass
        try:
            self._refresh_account_balance()
        except Exception:
            pass

        try:
            pilot_experience_to_git(self.pilot)
            if getattr(self.cfg, "LEARNING_PUSH_ON_TRADE", True):
                push_learning_checkpoint_async(f"trade_exit_{ticker}")
        except Exception:
            pass
        try:
            from core.learning_coordinator import schedule_post_close_learning
            schedule_post_close_learning(self.cfg, self)
        except Exception as exc:
            log.debug(f"Post-close learning schedule: {exc}")
        self._sync_bot_nav_from_ib()
        self._attempt_hot_swap_entry()
        return True
    def _clear_closed_position_state(self, ticker: str) -> None:
        """Drop local position tracking after exit (IB brackets may still rest)."""
        if hasattr(self, "_active_positions"):
            self._active_positions = [
                p for p in self._active_positions if p.get("ticker") != ticker
            ]
        if ticker:
            self._position_slots.pop(ticker, None)
            self._bracket_by_ticker.pop(ticker, None)
            self._risk_plans.pop(ticker, None)
        if self.current_ticker == ticker:
            self.current_ticker = None
        self.bracket_handle = None
        self._position_opened_at = 0.0
        self._position_stop = 0.0
        self._position_target = 0.0
        self._position_peak = 0.0
        self._hard_stop_floor = 0.0
        if self._active_stream_ticker == ticker:
            self._stop_target_stream(self._active_stream_ticker)
            self._active_stream_ticker = None
        if getattr(self, "_next_best_pick", None) and self._next_best_score >= 25:
            self.top_pick = self._next_best_pick
        self._refresh_aggregate_position_state()
    def _apply_trade_close_learning(self, trade_rec: Dict[str, Any], ticker: str) -> None:
        """Feed round-trip fills into every learning / telemetry hook."""
        pnl = float(trade_rec.get("pnl_usd", 0))
        pnl_pct = float(trade_rec.get("pnl_pct", 0))
        result = trade_rec.get("result", "loss")
        exit_fill = float(trade_rec.get("exit_fill") or trade_rec.get("exit", 0))
        entry_fill = float(trade_rec.get("entry_fill") or trade_rec.get("entry", 0))
        shares = float(trade_rec.get("shares", 0))
        reason = str(trade_rec.get("exit_reason", ""))
        regime = str(trade_rec.get("regime", ""))
        hold_sec = float(trade_rec.get("hold_sec", 0))
        entry_slip = float(trade_rec.get("entry_slippage_pct", 0))
        exit_slip = float(trade_rec.get("exit_slippage_pct", 0))
        stop_px = float(trade_rec.get("stop", 0))
        target_px = float(trade_rec.get("target", 0))

        append_fill_ledger({**trade_rec, "event": "round_trip"})
        try:
            from core.slow_coach import observe_round_trip
            observe_round_trip(
                self.cfg, trade_rec,
                equity=float(self._war_account_equity() or self.bot_nav or 1000),
            )
        except Exception:
            pass
        try:
            from core.war_account import record_exit, war_account_enabled
            if war_account_enabled(self.cfg):
                record_exit(
                    self.cfg,
                    ticker=ticker,
                    shares=int(shares),
                    ib_fill=exit_fill,
                    quote=float(trade_rec.get("quote_exit", exit_fill)),
                    pnl_usd_ib=pnl,
                    entry_ib_fill=entry_fill,
                    exit_reason=reason,
                    spread_pct=abs(exit_slip),
                )
        except Exception as exc:
            log.debug(f"War account exit: {exc}")
        log_round_trip_fills(
            ticker=ticker,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            quote_entry=float(trade_rec.get("quote_entry", entry_fill)),
            quote_exit=float(trade_rec.get("quote_exit", exit_fill)),
            shares=shares,
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            result=result,
            exit_reason=reason,
            entry_slippage_pct=entry_slip,
            exit_slippage_pct=exit_slip,
            regime=regime,
            hold_sec=hold_sec,
            entry_mode=str(trade_rec.get("entry_mode", "")),
            limit_px=trade_rec.get("limit_px"),
        )
        self.trade_journal.append(trade_rec)
        if len(self.trade_journal) > self._trade_journal_max:
            self.trade_journal = self.trade_journal[-self._trade_journal_max:]
        if self.ai_commander:
            try:
                self.ai_commander.record_trade(trade_rec)
            except Exception:
                pass
        try:
            from core.halim_ppo_coevolution import attach_trade_outcome
            attach_trade_outcome(
                ticker,
                pnl=float(pnl),
                win=(result == "win"),
                cfg=self.cfg,
                trade_rec=trade_rec,
            )
        except Exception:
            pass
        try:
            from core.halim_outcome_gold import record_trade_outcome
            record_trade_outcome(trade_rec, cfg=self.cfg)
        except Exception:
            pass
        try:
            self.account_evaluator.evaluate(
                self, "trade_closed", ai_commander=self.ai_commander,
            )
        except Exception:
            pass
        observe_trade_everywhere(
            trade_rec, self.autopilot, self.consciousness, self.pilot, cfg=self.cfg,
        )
        exit_type = "other"
        if stop_px > 0 and exit_fill <= stop_px * 1.003:
            exit_type = "stop_hit"
        elif target_px > 0 and exit_fill >= target_px * 0.997:
            exit_type = "target_hit"
        elif pnl > 0:
            exit_type = "profit_exit"
        elif pnl < 0:
            exit_type = "loss_exit"
        if "stop" in reason.lower():
            exit_type = "stop_hit"
        atr = float((getattr(self, "_last_entry_telemetry", None) or {}).get("atr", 0) or 0)
        if not atr and trade_rec:
            atr = float(trade_rec.get("atr_at_entry", 0) or 0)
        noise_sec = float(getattr(self.cfg, "REGIME_ATR_NOISE_STOP_SEC", 120.0))
        noise_stop = exit_type == "stop_hit" and hold_sec < noise_sec
        from core.trade_telemetry import _raw_rr
        log_regime_atr_outcome(
            ticker=ticker,
            regime=regime,
            exit_type=exit_type,
            entry=entry_fill,
            exit_px=exit_fill,
            stop=stop_px,
            target=target_px,
            atr=atr,
            hold_sec=hold_sec,
            pnl_usd=pnl,
            planned_rr=_raw_rr(entry_fill, stop_px, target_px),
            noise_stop=noise_stop,
        )
        log_exit_postmortem(
            ticker=ticker,
            entry=entry_fill,
            exit_px=exit_fill,
            shares=shares,
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            result=result,
            regime=regime,
            hold_sec=hold_sec,
            entry_slippage_pct=entry_slip,
            exit_reason=reason,
        )
        self._observe_runtime(
            "trade_closed",
            ticker=ticker,
            reason=reason or result,
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            won=(pnl > 0),
            exit_type=exit_type,
            hold_sec=hold_sec,
            regime=regime,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            entry_slippage_pct=entry_slip,
            exit_slippage_pct=exit_slip,
            market_state=get_market_state(self.cfg),
        )
        if pnl < 0:
            self._observe_runtime(
                "loss_streak",
                ticker=ticker,
                reason=reason,
                pnl_usd=pnl,
                consecutive_losses=int(getattr(self.risk, "_consecutive_losses", 0)),
                market_state=get_market_state(self.cfg),
            )
            try:
                from core.live_trade_guard import on_trade_closed as guard_trade_closed
                guard_trade_closed(ticker, pnl, self.cfg, exit_reason=reason)
            except Exception:
                pass
        try:
            from core.ppo_entry_learning import record_ppo_trade_close
            record_ppo_trade_close(
                self.cfg,
                ticker=ticker,
                pnl_usd=pnl,
                pnl_pct=float(trade_rec.get("pnl_pct", 0) or 0),
                result=str(result),
                exit_reason=reason,
            )
        except Exception:
            pass
        slot = self._position_slots.get(ticker, {})
        combined_slip = abs(entry_slip) + abs(exit_slip)
        try:
            buffer_append({
                "source": "live_trade",
                "ticker": ticker,
                "action": "SELL",
                "entry_price": entry_fill,
                "exit_price": exit_fill,
                "quote_entry": trade_rec.get("quote_entry"),
                "quote_exit": trade_rec.get("quote_exit"),
                "entry_slippage_pct": entry_slip,
                "exit_slippage_pct": exit_slip,
                "pnl_usd": round(pnl, 2),
                "win": 1 if pnl > 0 else 0,
                "reward": reward_from_trade(
                    pnl, self.cfg,
                    slippage_pct=combined_slip,
                    spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                    pnl_pct=float(trade_rec.get("pnl_pct", 0) or 0),
                    peak_pct=float(trade_rec.get("peak_pct", 0) or 0),
                    entry_fill=float(entry_fill or 0),
                    exit_fill=float(exit_fill or 0),
                    shares=float(trade_rec.get("shares", 0) or 0),
                ),
                "regime": regime,
                "confidence": getattr(self, "_last_ai_confidence", 0.5),
                "vision_read": (slot.get("vision_read") or "")[:800],
                "features": snapshot_features(self._feature_buffer, self.cfg),
                "exit_reason": reason[:200],
                "hold_sec": hold_sec,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        if getattr(self.cfg, "LEARNING_PUSH_ON_TRADE", True):
            try:
                push_learning_checkpoint_async(f"trade_closed_{ticker}")
            except Exception:
                pass
        try:
            maybe_refresh_session_limits(self, min_interval_sec=300.0)
        except Exception:
            pass
    def _record_early_exit_learning(
        self,
        ticker: str,
        entry: float,
        exit_px: float,
        shares: float,
        pnl: float,
        reason: str,
        *,
        flatten_trade=None,
        trade_rec: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Early exits — resolve IB exit fill and feed all learning hooks."""
        if trade_rec is None:
            trade_rec = self._build_trade_close_record(
                ticker, exit_px, reason, flatten_trade=flatten_trade,
            )
        self._apply_trade_close_learning(trade_rec, ticker)
    def _exit_position(
        self,
        current_px: float,
        reason: str,
        ticker: Optional[str] = None,
        *,
        defer: bool = False,
    ):
        """Manually exit position — submit flatten, reconcile IB fill async."""
        ticker = (ticker or self.current_ticker or "").upper()
        if defer:
            self._request_deferred_exit(ticker, current_px, reason)
            return
        can_trade, market_state = can_trade_now(self.cfg)
        if not can_trade:
            log.debug(
                f"Exit deferred — session {market_state} (no orders outside "
                f"{allowed_trading_sessions_label(self.cfg)})"
            )
            self._request_deferred_exit(ticker, current_px, reason)
            return
        if ticker and ticker in self._position_slots:
            self._load_position_context(ticker)
        if self.shares <= 0:
            return
        quantity = int(self.shares)
        handle = self._bracket_by_ticker.get(ticker) or self.bracket_handle
        slot_snap = snapshot_slot(self._position_slots.get(ticker, {}))
        flatten_trade = None
        try:
            flatten_trade = self.broker.flatten_position(
                quantity, handle=handle, urgent=True, symbol=ticker,
            )
            log.info(
                f"⚡ EXIT submitted: SELL {quantity} {ticker} @ market "
                f"(quote ${current_px:.4f}) | {reason[:80]}"
            )
            self._enqueue_pending_close(
                ticker, reason, current_px,
                event="early_exit",
                flatten_trade=flatten_trade,
                bracket=handle,
                slot=slot_snap,
                shares=float(quantity),
            )
            self.shares = 0.0
            self._prev_shares = 0.0
            self.bracket_handle = None
            if self.risk.plan:
                self.risk.close_position()
            self._reset_profit_hunt_state()
            self._clear_closed_position_state(ticker)
            self._clear_pending_entry(ticker, cooldown_sec=30.0)
            self._clear_ai_councils(ticker)
        except Exception as exc:
            log.error(f"Early exit failed: {exc}")
    def commander_positions_intel(self) -> Dict[str, Any]:
        from core.position_intel import collect_positions
        return collect_positions(self)
    def commander_risk_summary(self) -> Dict[str, Any]:
        from core.position_intel import collect_risk
        return collect_risk(self)
    def commander_exit_ticker(self, ticker: str, reason: str = "commander_exit") -> Dict[str, Any]:
        """Exit one position from Telegram / AI commander (bot slot or IB-only)."""
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return {"ok": False, "error": "no ticker"}

        self._sync_all_positions_from_ib()
        px = self._live_price_for(ticker, 0.0)

        if ticker in self._position_slots:
            if px <= 0:
                px = float(self._position_slots[ticker].get("entry_price", 0) or 0)
            if px <= 0:
                return {"ok": False, "error": f"no price for {ticker}"}
            self._exit_position(px, reason, ticker=ticker)
            return {"ok": True, "ticker": ticker, "price": px, "reason": reason, "source": "bot_slot"}

        qty = 0
        entry = 0.0
        try:
            self.ib.reqPositions()
            self.ib.sleep(0.3)
            for p in self.ib.positions():
                sym = (getattr(p.contract, "symbol", "") or "").upper()
                if sym == ticker:
                    qty = int(float(p.position))
                    entry = float(getattr(p, "avgCost", 0) or 0)
                    break
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if qty <= 0:
            return {"ok": False, "error": f"no open long position for {ticker}"}
        if px <= 0:
            px = entry or self._live_price_for(ticker, entry)

        old_ticker = self.cfg.TICKER
        try:
            self.cfg.TICKER = ticker
            self.conn._contract = None
            self.broker.cancel_open_orders_for_symbol(ticker)
            self.broker.flatten_position(qty, urgent=True, symbol=ticker)
            self.ib.sleep(1)
            pnl = (px - entry) * qty if entry > 0 else 0.0
            log.info(f"⚡ COMMANDER EXIT: {ticker} @ ${px:.2f} | {reason} | P&L ${pnl:+.2f}")
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                from core.notify import send_dynamic_notification
                send_dynamic_notification(
                    self.notifier, self.autopilot, "commander_exit",
                    self._notify_context({
                        "ticker": ticker, "price": px, "pnl_usd": round(pnl, 2),
                        "reason": reason, "entry": entry,
                    }),
                    f"⚡ COMMANDER EXIT\n{ticker} @ ${px:.2f}\nReason: {reason}\nP&L: ${pnl:+.2f}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            else:
                self.notifier.info(f"⚡ COMMANDER EXIT\n{ticker} @ ${px:.2f}\nReason: {reason}\nP&L: ${pnl:+.2f}")
            try:
                from core.telegram_broadcast import broadcast_ops
                broadcast_ops(
                    self.cfg,
                    "commander_exit",
                    {
                        "ticker": ticker, "price": px, "pnl": round(pnl, 2),
                        "reason": reason, "entry": entry,
                    },
                    f"EXIT {ticker} @ ${px:.2f} | {reason} | P&L ${pnl:+.2f}",
                )
            except Exception:
                pass
            return {"ok": True, "ticker": ticker, "price": px, "pnl": round(pnl, 2), "reason": reason, "source": "ib_only"}
        except Exception as exc:
            log.error(f"Commander exit failed {ticker}: {exc}")
            return {"ok": False, "error": str(exc)}
        finally:
            self.cfg.TICKER = old_ticker
            self.conn._contract = None
    def commander_exit_filtered(self, mode: str, reason: str = "commander_bulk_exit") -> Dict[str, Any]:
        """Exit positions: mode = profit | loss | all."""
        mode = (mode or "all").lower().strip()
        intel = self.commander_positions_intel()
        results: List[Dict[str, Any]] = []
        for p in intel.get("positions", []):
            pnl = float(p.get("unrealized_pnl", 0) or 0)
            if mode == "profit" and pnl <= 0:
                continue
            if mode == "loss" and pnl >= 0:
                continue
            results.append(self.commander_exit_ticker(p["ticker"], reason))
        ok_n = sum(1 for r in results if r.get("ok"))
        return {"ok": ok_n > 0, "mode": mode, "exited": ok_n, "results": results}
    def _evaluate_profit_hunt_exit(self, current_px: float) -> Tuple[bool, str]:
        """Spike-top + spike-fade opportunistic exits while in profit."""
        if self.shares <= 0 or self._entry_price <= 0:
            return False, ""
        if not self._risk_plan_sane_for_tick(current_px):
            return False, ""

        ticker = self.current_ticker or ""
        entry_px = self._entry_price
        pnl_pct = (current_px / entry_px) - 1

        min_hold = effective_min_hold_for_exit(self.cfg, pnl_pct)
        opened = getattr(self, "_position_opened_at", 0.0)
        if min_hold > 0 and opened and (time.time() - opened) < min_hold:
            return False, ""

        extended = is_extended_session(get_market_state(self.cfg))

        fast_df, live_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=6)
        if fast_df is None:
            dm = dm or self._dm_for_ticker(ticker)
            fast_df = coalesce_bars(
                dm.get_live_decision_bars(min_bars=6) if dm else None,
                dm.get_bar_dataframe(min_bars=10) if dm else None,
                self._scan_data_cache.get(ticker),
                min_len=3,
            )
        if live_px <= 0:
            live_px = current_px

        fade_thr = float(getattr(self.cfg, "MICRO_FADE_EXIT", 0.55))
        if (
            getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True)
            and pnl_pct > 0.002
            and forecast.get("fade_risk", 0) >= fade_thr
            and forecast.get("dir", 0) <= 0
        ):
            return True, (
                f"micro_fade: risk={forecast['fade_risk']:.2f} "
                f"pred↓${(forecast.get('pred_1bar') or live_px):.2f}"
            )

        should_exit, reason, ctx = evaluate_spike_top_exit(
            self.cfg, fast_df, dm, current_px, entry_px,
            pnl_pct, self._position_peak, extended=extended,
        )
        if ctx.get("spike_detected"):
            self._profit_hunt_spike_ctx = ctx
            self._profit_hunt_spike_peak = max(self._profit_hunt_spike_peak, current_px)
            self._profit_hunt_spike_at = time.time()
            track_profit_hunt_event(
                self.cfg, "spike_detected", ticker,
                {**ctx, "price": current_px, "pnl_pct": round(pnl_pct * 100, 3)},
                pnl_usd=(current_px - entry_px) * self.shares,
                pnl_pct=pnl_pct,
                record_buffer=True,
                push_git=False,
            )

        if should_exit:
            track_profit_hunt_event(
                self.cfg, "hunt_signal", ticker,
                {**ctx, "reason": reason, "price": current_px},
                pnl_usd=(current_px - entry_px) * self.shares,
                pnl_pct=pnl_pct,
                record_buffer=True,
            )
            return True, reason

        fade_exit, fade_reason = evaluate_wave_end_on_spike_fade(
            self.cfg, fast_df, current_px, entry_px, self._position_peak, pnl_pct,
        )
        if fade_exit:
            return True, fade_reason

        missed = check_missed_profit_hunt(
            self.cfg,
            {
                "spike_peak": self._profit_hunt_spike_peak,
                "spike_seen_at": self._profit_hunt_spike_at,
                "spike_ctx": self._profit_hunt_spike_ctx,
                "shares": self.shares,
            },
            current_px,
            entry_px,
            ticker,
        )
        if missed and not self._profit_hunt_missed_logged:
            self._profit_hunt_missed_logged = True
            missed["reason"] = (
                f"Missed spike-top exit on {ticker}: peak ${missed['spike_peak']:.2f} "
                f"left ~${missed['left_on_table_usd']:.0f} on table"
            )
            track_profit_hunt_event(
                self.cfg, "missed_profit_hunt", ticker, missed,
                pnl_usd=-float(missed.get("left_on_table_usd", 0)),
                pnl_pct=pnl_pct,
                record_buffer=True,
                push_git=True,
            )
            teach_profit_hunt_lesson(
                self.autopilot, self.consciousness,
                missed["reason"],
            )
            self._observe_runtime(
                "missed_profit_hunt",
                ticker=ticker,
                reason=missed["reason"],
                pnl_usd=-float(missed.get("left_on_table_usd", 0)),
                market_state=get_market_state(self.cfg),
                **{k: v for k, v in missed.items() if k != "reason"},
            )
            log.warning(f"  📚 {missed['reason']}")

        return False, ""
    def _ai_profit_decision_stalled(self, pnl_pct: float = 0.0) -> bool:
        """True when AI/council has not acted on a green position within the wait window."""
        if pnl_pct <= 0:
            return False
        from core.green_profit_lock import ai_wait_sec

        wait = ai_wait_sec(self.cfg)
        now = time.time()
        ticker = self.current_ticker or ""

        for task in ("exit_decision", "position_manage", "stagnation_check", "risk_exit"):
            if self._has_ai_council(ticker, task):
                st = self._ai_councils.get(self._council_key(ticker, task), {})
                if now - float(st.get("started_at", now)) >= wait:
                    return True

        if self.ai_commander:
            for task in ("exit_decision", "position_manage", "risk_exit"):
                try:
                    st = self.ai_commander._live_line.status(ticker, task)
                    if st.get("in_flight") and float(st.get("age_sec", 0) or 0) >= wait:
                        return True
                except Exception:
                    pass

        if getattr(self.cfg, "AI_FULL_CONTROL", True) and not self.ai_commander:
            return True

        ride_at = getattr(self, "_profit_ride_started_at", 0.0)
        if ride_at and now - ride_at >= wait:
            return True

        return False
    def _enforce_green_profit_lock(self, current_px: float) -> bool:
        """Mechanical quick green scalp when AI stalls — never let profit bleed to red."""
        from core.green_profit_lock import (
            evaluate_green_lock,
            green_profit_lock_enabled,
            min_green_pnl_pct,
            is_green_lock_reason,
        )

        if not green_profit_lock_enabled(self.cfg):
            return False
        if self.shares <= 0 or self._entry_price <= 0:
            return False
        if not self._risk_plan_sane_for_tick(current_px):
            return False

        entry_px = self._entry_price
        pnl_pct = ((current_px / entry_px) - 1) if entry_px else 0.0
        if pnl_pct <= 0:
            return False

        peak_pct = ((self._position_peak / entry_px) - 1) if entry_px else 0.0
        giveback = max(0.0, peak_pct - pnl_pct)
        if pnl_pct >= min_green_pnl_pct(self.cfg):
            self._was_in_profit = True

        stalled = self._ai_profit_decision_stalled(pnl_pct)
        should_lock, reason = evaluate_green_lock(
            self.cfg,
            pnl_pct=pnl_pct,
            peak_pct=peak_pct,
            ai_stalled=stalled,
            giveback_from_peak=giveback,
            was_green=self._was_in_profit,
        )
        if not should_lock:
            return False

        log.info(f"  🔒 GREEN LOCK: {reason}")
        if is_green_lock_reason(reason):
            ticker = self.current_ticker or ""
            pnl = pnl_pct * self.shares * entry_px
            track_profit_hunt_event(
                self.cfg, "green_profit_lock", ticker,
                {"reason": reason, "price": current_px, "ai_stalled": stalled},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, reason)
            return True
        return self._execute_mechanical_profit_exit(current_px, reason)
    def _execute_mechanical_profit_exit(
        self, current_px: float, reason: str, *, defer: bool = False,
    ) -> bool:
        """Profit hunt signal — AI council decides exit vs ride for higher profit."""
        if not reason:
            return False
        from core.green_profit_lock import is_green_lock_reason

        ticker = self.current_ticker or ""
        entry_px = self._entry_price
        pnl_pct = ((current_px / entry_px) - 1) if entry_px else 0.0
        pnl = pnl_pct * self.shares * entry_px if entry_px else 0.0

        if is_green_lock_reason(reason):
            log.info(f"  🔒 GREEN LOCK: {reason[:100]}")
            track_profit_hunt_event(
                self.cfg, "green_profit_lock", ticker,
                {**self._profit_hunt_spike_ctx, "reason": reason, "price": current_px},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, reason, ticker=ticker, defer=defer)
            return True

        from core.profit_hunting import ai_profit_full_power

        stalled = self._ai_profit_decision_stalled(pnl_pct)

        if (
            ai_profit_full_power(self.cfg)
            and pnl_pct > 0
            and self.ai_commander
            and not stalled
        ):
            log.info(f"  🧠 AI PROFIT SIGNAL: {reason[:80]} — council decides exit vs ride")
            self._last_ai_position_manage = 0.0
            self._ai_manage_position(current_px)
            if self._deliberate_exit_council(
                ticker, current_px, True, 0.65, reason,
                {"signal": "profit_hunt", "mechanical": True, "ride_ok": True},
            ):
                track_profit_hunt_event(
                    self.cfg, reason.split(":")[0].strip(), ticker,
                    {**self._profit_hunt_spike_ctx, "reason": reason, "price": current_px},
                    pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
                )
                return True
            track_profit_hunt_event(
                self.cfg, "ai_ride", ticker,
                {"reason": reason, "price": current_px, "pnl_pct": pnl_pct},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=False,
            )
            log.info(f"  🧠 AI RIDING {ticker}: holding for higher profit — {reason[:60]}")
            self._profit_ride_started_at = time.time()
            return False

        if profit_exit_bypasses_council(
            self.cfg, reason, pnl_pct, ai_stalled=stalled,
        ):
            log.info(f"  🎯 PROFIT HUNT: {reason}")
            if self.ai_commander:
                ppo_exit, ppo_conf, ppo_reason = (True, 0.65, reason)
                obs = self._build_ppo_obs(current_px)
                if obs is not None and self.ai_commander.model is not None:
                    action, conf, ppo_reason = self.ai_commander.ppo_action(obs)
                    ppo_exit = action == 2
                    ppo_conf = conf
                self.ai_commander.ring_exit_for_deferred_learning(
                    {
                        "ticker": ticker, "price": current_px,
                        "pnl_pct": round(pnl_pct * 100, 2),
                        "entry": entry_px, "reason": reason,
                    },
                    ppo_exit=ppo_exit, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    executed_exit=True, pipeline="ppo:profit_lock",
                )
            track_profit_hunt_event(
                self.cfg, reason.split(":")[0].strip(), ticker,
                {**self._profit_hunt_spike_ctx, "reason": reason, "price": current_px},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
            )
            teach_profit_hunt_lesson(
                self.autopilot, self.consciousness,
                f"Spike-top hunt on {ticker}: {reason}",
            )
            self._exit_position(current_px, reason, ticker=ticker, defer=defer)
            return True
        if is_ai_council_mode(self.cfg) and self.ai_commander:
            if self._deliberate_exit_council(
                ticker, current_px, True, 0.65, reason,
                {"signal": "profit_hunt", "mechanical": True},
            ):
                return True
            track_profit_hunt_event(
                self.cfg, "council_hold", ticker,
                {"reason": reason, "price": current_px},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=False,
            )
            return False
        self._exit_position(current_px, reason, ticker=ticker, defer=defer)
        return True
    def _reset_profit_hunt_state(self):
        self._profit_hunt_spike_peak = 0.0
        self._profit_hunt_spike_at = 0.0
        self._profit_hunt_spike_ctx = {}
        self._profit_hunt_missed_logged = False
        self._profit_ride_started_at = 0.0
        self._was_in_profit = False
    def _live_position_monitor(self, current_px: float, *, price_trusted: bool = True):
        """Continuous post-entry tracking: pulse log, AI manage, trail, exit."""
        if self.shares <= 0 or self._entry_price <= 0:
            return

        ticker = self.current_ticker or getattr(self.cfg, "TICKER", "")
        price_eps = max(self._entry_price * 0.0001, 0.0001)
        now = time.time()
        trusted = price_trusted and self._risk_plan_sane_for_tick(current_px)

        if self._last_pulse_price <= 0:
            self._last_pulse_price = current_px
            self._last_price_change_at = now

        if abs(current_px - self._last_pulse_price) > price_eps:
            self._last_pulse_price = current_px
            self._last_price_change_at = now
        else:
            frozen_for = now - self._last_price_change_at
            ai_snap = bool(self._last_stagnation_decision.get("force_snapshot"))
            stale_sec = float(getattr(self.cfg, "STALE_PRICE_REFRESH_SEC", 20.0))
            snap_gap = max(stale_sec, 5.0)
            if (
                ticker
                and (now - self._last_price_snapshot_at) >= snap_gap
                and (ai_snap or frozen_for >= stale_sec)
            ):
                snap_px = self._force_price_snapshot(ticker)
                self._last_price_snapshot_at = now
                if snap_px > 0 and abs(snap_px - current_px) > price_eps:
                    current_px = snap_px
                    self._last_pulse_price = current_px
                    self._last_price_change_at = now

        stagnant_sec = now - self._last_price_change_at
        frozen_sec = stagnant_sec

        if trusted:
            if current_px > self._position_peak:
                self._position_peak = current_px
            if self.risk.plan:
                self.risk.plan.peak_price = max(self.risk.plan.peak_price, current_px)

        pnl_frac = ((current_px / self._entry_price) - 1) if self._entry_price else 0.0
        if (
            pnl_frac > 0
            and getattr(self.cfg, "DYNAMIC_TRAILING_ENABLED", False)
            and self.risk.plan
        ):
            try:
                _, ppo_conf, _ = self._ai_gate_exit(current_px)
                obs = self._build_ppo_obs(current_px)
                overrides = self.risk.update_ai_dynamic_trailing(
                    ai_confidence=float(ppo_conf),
                    regime_trend_strength=0.0,
                    regime_label="unknown",
                    observation=obs,
                )
                if overrides.get("early_loss_exit_threshold_pct") is not None:
                    self.risk._early_loss_threshold_pct = overrides[
                        "early_loss_exit_threshold_pct"
                    ]
            except Exception:
                pass

        pulse_ctx = {
            "ticker": ticker,
            "price": current_px,
            "pnl_usd": round((current_px - self._entry_price) * self.shares, 2),
            "pnl_pct": round(((current_px / self._entry_price) - 1) * 100, 2),
            "stop": self._position_stop,
            "target": self._position_target,
            "peak": self._position_peak,
            "stagnant_sec": round(stagnant_sec, 1),
            "price_frozen_sec": round(frozen_sec, 1),
        }
        ai_check_sec = float(getattr(self.cfg, "AI_STAGNATION_CHECK_SEC", 30.0))
        if (
            getattr(self.cfg, "AI_FULL_CONTROL", True)
            and self.ai_commander
            and stagnant_sec >= ai_check_sec
        ):
            try:
                self.ai_commander.prefetch_stagnation(pulse_ctx)
            except Exception:
                pass

        fingerprint = (
            f"{pulse_ctx['price']:.4f}|{pulse_ctx['pnl_usd']:.2f}|"
            f"{pulse_ctx['stop']:.4f}|{pulse_ctx['target']:.4f}"
        )
        unchanged = fingerprint == self._last_pulse_fingerprint
        pulse_verbose = bool(self._last_stagnation_decision.get("pulse_verbose"))
        if unchanged and not pulse_verbose:
            pulse_sec = float(getattr(self.cfg, "POSITION_PULSE_UNCHANGED_SEC", 30.0))
        else:
            pulse_sec = float(getattr(self.cfg, "POSITION_PULSE_SEC", 5.0))
        if now - self._last_position_pulse >= pulse_sec:
            self._last_position_pulse = now
            if trusted:
                if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                    self.ai_commander.ai_log("LIVE_PULSE", pulse_ctx)
                else:
                    log.info(
                        f"📡 LIVE {ticker}: ${current_px:.4f} | "
                        f"P&L ${pulse_ctx['pnl_usd']:+.2f} ({pulse_ctx['pnl_pct']:+.2f}%) | "
                        f"Stop ${self._position_stop:.4f} | TP ${self._position_target:.4f} | "
                        f"Peak ${self._position_peak:.4f}"
                    )
                self._last_pulse_fingerprint = fingerprint

        ai_sec = float(getattr(self.cfg, "AI_POSITION_MANAGE_SEC", 10.0))
        if pnl_frac > float(getattr(self.cfg, "IN_PROFIT_MANAGE_PNL_PCT", 0.003)):
            ai_sec = float(getattr(self.cfg, "AI_POSITION_MANAGE_IN_PROFIT_SEC", 1.0))
        min_hold = effective_min_position_hold_sec(self.cfg)
        opened = getattr(self, "_position_opened_at", 0.0)
        if now - self._last_ai_position_manage >= ai_sec:
            self._last_ai_position_manage = now
            if not opened or (now - opened) >= min_hold:
                self._ai_manage_position(current_px)

        # Opportunistic profit hunt — AI full power decides exit vs ride
        if trusted:
            hunt_exit, hunt_reason = self._evaluate_profit_hunt_exit(current_px)
            if hunt_exit:
                if self._execute_mechanical_profit_exit(current_px, hunt_reason):
                    self._active_stream_ticker = None
                    return

            # Green profit lock — quick scalp if AI stalls while in profit
            if self._enforce_green_profit_lock(current_px):
                self._active_stream_ticker = None
                return

        if trusted:
            self._update_trailing_stops(current_px)

        # Risk engine tick exits — AI council on profit; mechanical only on loss
        if trusted and self.risk.plan:
            ticker = self.current_ticker or ""
            if not self._risk_plan_sane_for_tick(current_px):
                self._bind_risk_plan_for_ticker(ticker)
            if self._risk_plan_sane_for_tick(current_px):
                prev_stop = self.risk.plan.current_stop_price
                should_risk_exit, risk_reason = self.risk.evaluate_tick(current_px)
                if self.risk.plan.current_stop_price != prev_stop:
                    self._apply_stop_update(
                        self.risk.plan.current_stop_price,
                        f"risk trail ({risk_reason or 'ratchet'})",
                    )
                if should_risk_exit and risk_reason:
                    entry_px = self._entry_price
                    pnl_pct = ((current_px / entry_px) - 1) if entry_px else 0.0
                    stalled = self._ai_profit_decision_stalled(pnl_pct)
                    if profit_exit_bypasses_council(
                        self.cfg, risk_reason, pnl_pct, ai_stalled=stalled,
                    ):
                        log.info(f"  ⚡ MECHANICAL RISK EXIT: {risk_reason}")
                        track_profit_hunt_event(
                            self.cfg, risk_reason, ticker,
                            {"reason": risk_reason, "price": current_px},
                            pnl_usd=(current_px - entry_px) * self.shares if entry_px else 0,
                            pnl_pct=pnl_pct, record_buffer=True, push_git=True,
                        )
                        self._exit_position(current_px, risk_reason)
                        self._active_stream_ticker = None
                        return
                    if is_ai_council_mode(self.cfg) and self.ai_commander:
                        if self._deliberate_risk_exit(ticker, current_px, risk_reason):
                            self._active_stream_ticker = None
                            return
                    else:
                        log.info(f"  ⚡ RISK EXIT: {risk_reason}")
                        self._exit_position(current_px, risk_reason)
                        self._active_stream_ticker = None
                        return
            else:
                log.warning(
                    f"  ⚠️ Risk tick skipped {ticker}: plan/price mismatch "
                    f"(px=${current_px:.2f} entry=${self._entry_price:.2f})"
                )

        if not trusted:
            return

        # Hard stop breach — always exit, bypasses min-hold
        stop_level = self._position_stop if self._position_stop > 0 else self._hard_stop_floor
        if stop_level > 0 and current_px <= stop_level:
            log.info(f"  🛑 STOP BREACH: ${current_px:.4f} <= ${stop_level:.4f}")
            self._exit_position(current_px, "stop_breach")
            self._active_stream_ticker = None
            return

        should_exit, exit_reason = self._should_exit_early(
            current_px, self._entry_price,
            (current_px - self._entry_price) * self.shares,
            self._position_risk_budget(),
            stagnant_sec=now - self._last_price_change_at,
        )
        if should_exit:
            log.info(f"  ⚡ LIVE EXIT: {exit_reason}")
            self._exit_position(current_px, exit_reason)
            self._active_stream_ticker = None
    def _ai_manage_position(self, current_px: float):
        """Ollama + PPO full thinking on open position — dynamic stop/TP."""
        if self.shares <= 0 or not self.bracket_handle:
            return

        entry = self._entry_price
        pnl_usd = (current_px - entry) * self.shares
        pnl_pct = ((current_px / entry) - 1) * 100 if entry else 0

        vol_ratio = 1.0
        regime = "slow_grind"
        fast_df = None
        ticker_dm = self._dm_for_ticker(self.current_ticker or "")
        fast_df, _, _, forecast = self._resolve_live_bars(self.current_ticker or "", min_bars=6)
        if fast_df is None and ticker_dm is not None:
            fast_df = ticker_dm.get_live_decision_bars(min_bars=6)
        if fast_df is not None and len(fast_df) >= 5:
            _, vol_ratio = self._detect_volume_spike(fast_df)
            try:
                _, regime = resolve_regime(
                    self.regime_detector, fast_df,
                    spike_ratio=float(vol_ratio),
                    vol_ratio=float(vol_ratio),
                )
            except Exception:
                pass

        if getattr(self.cfg, "DYNAMIC_TRAILING_ENABLED", False) and self.risk.plan:
            try:
                _, ppo_conf, _ = self._ai_gate_exit(current_px)
                obs = self._build_ppo_obs(current_px)
                overrides = self.risk.update_ai_dynamic_trailing(
                    ai_confidence=float(ppo_conf),
                    regime_trend_strength=0.0,
                    regime_label=str(regime),
                    observation=obs,
                )
                if overrides.get("early_loss_exit_threshold_pct") is not None:
                    self.risk._early_loss_threshold_pct = overrides["early_loss_exit_threshold_pct"]
            except Exception:
                pass

        pos_ctx = {
            "ticker": self.current_ticker,
            "entry": entry,
            "price": current_px,
            "peak": self._position_peak,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "peak_pct": round(
                ((self._position_peak / entry) - 1) * 100 if self._position_peak > entry else pnl_pct,
                2,
            ),
            "stop": self._position_stop,
            "target": self._position_target,
            "hard_floor": self._hard_stop_floor,
            "vol_ratio": round(vol_ratio, 2),
            "regime": str(regime),
            "stagnant_sec": round(max(0.0, time.time() - self._last_price_change_at), 1),
            "price_frozen_sec": round(max(0.0, time.time() - self._last_price_change_at), 1),
        }
        ppo_exit, ppo_conf, ppo_reason = False, 0.5, ""
        try:
            ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
        except Exception:
            pass
        mech_stop, mech_target = self._compute_mechanical_trail(current_px)
        ticker = self.current_ticker or ""
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            if self._has_ai_council(ticker, "position_manage"):
                return
            if self._has_ai_council(ticker, "exit_decision"):
                return
            if ppo_exit:
                return
            decision = self.ai_commander.decide_position_manage(
                pos_ctx, ppo_exit, ppo_conf, ppo_reason, mech_stop, mech_target,
            )
            if decision.get("pending"):
                self._set_ai_council(ticker, "position_manage", {
                    "fingerprint": decision["fingerprint"],
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                    "ctx": pos_ctx,
                    "mechanical_stop": mech_stop,
                    "mechanical_target": mech_target,
                    "current_px": current_px,
                })
                log.info(
                    f"  🧠 COUNCIL manage {ticker}: "
                    f"{(decision.get('reason') or 'deliberating')[:100]} | "
                    f"{decision.get('pipeline', '')}"
                )
                return
        else:
            decision = generative_position_decision(self.cfg, self.autopilot, pos_ctx)

        self._apply_position_manage_decision(decision, current_px)
    def _apply_stop_update(self, new_stop: float, reason: str):
        if not self.bracket_handle or new_stop <= 0:
            return
        new_stop = round(new_stop, 4)
        if new_stop <= self._hard_stop_floor:
            new_stop = self._hard_stop_floor
        try:
            self.broker.update_stop_price(self.bracket_handle, new_stop)
            self._position_stop = new_stop
            if self.risk.plan:
                self.risk.plan.current_stop_price = new_stop
            log.info(f"  🛡️ STOP → ${new_stop:.4f} | {reason}")
        except Exception as exc:
            log.debug(f"Stop update failed: {exc}")
    def _apply_target_update(self, new_target: float, reason: str):
        if not self.bracket_handle or new_target <= 0:
            return
        new_target = round(new_target, 4)
        try:
            self.broker.update_target_price(self.bracket_handle, new_target)
            self._position_target = new_target
            if self.risk.plan:
                self.risk.plan.take_profit_price = new_target
            log.info(f"  🎯 TP → ${new_target:.4f} | {reason}")
        except Exception as exc:
            log.debug(f"Target update failed: {exc}")
    def _should_exit_early(self, current_px: float, entry_px: float, 
                           unrealized_pnl: float, risk_usd: float,
                           stagnant_sec: float = 0.0) -> Tuple[bool, str]:
        """
        Exit when profit gives back from peak, AI says exit, slippage risk high,
        or position is stagnant (flat/losing with no price progress).
        """
        if self.shares <= 0 or entry_px <= 0:
            return False, "no position"

        pnl_pct = (current_px / entry_px) - 1
        min_hold = effective_min_hold_for_exit(self.cfg, pnl_pct)
        opened = getattr(self, "_position_opened_at", 0.0)
        if min_hold > 0 and opened and (time.time() - opened) < min_hold:
            return False, "hold (min hold)"

        peak_pct = (self._position_peak / entry_px) - 1 if self._position_peak > 0 else pnl_pct

        if getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True):
            _, _, _, forecast = self._resolve_live_bars(self.current_ticker or "", min_bars=6)
            loss_thr = float(getattr(self.cfg, "MICRO_LOSS_EXIT", 0.58))
            if pnl_pct < -0.002 and forecast.get("loss_pressure", 0) >= loss_thr and forecast.get("dir", 0) < 0:
                return True, (
                    f"micro_loss: pressure={forecast['loss_pressure']:.2f} "
                    f"pred↓${(forecast.get('pred_1bar') or current_px):.2f}"
                )
            fade_thr = float(getattr(self.cfg, "MICRO_FADE_EXIT", 0.55))
            if (
                pnl_pct > 0.004
                and peak_pct > 0.008
                and forecast.get("fade_risk", 0) >= fade_thr
                and forecast.get("profit_run", 1.0) < 0.35
            ):
                return True, (
                    f"micro_profit_fade: fade={forecast['fade_risk']:.2f} "
                    f"peak +{peak_pct:.2%} now +{pnl_pct:.2%}"
                )
        
        # Dead trade: Ollama + PPO decide (rules are guardrail fallback only)
        ai_check_sec = float(getattr(self.cfg, "AI_STAGNATION_CHECK_SEC", 30.0))
        if getattr(self.cfg, "STAGNATION_EXIT_ENABLED", True) and stagnant_sec >= ai_check_sec:
            flat_band = float(getattr(self.cfg, "STAGNATION_FLAT_BAND_PCT", 0.008))
            max_peak = float(getattr(self.cfg, "STAGNATION_MAX_PEAK_PCT", 0.003))
            loss_cut = float(getattr(self.cfg, "STAGNATION_LOSS_CUT_PCT", -0.005))
            stagnation_sec = float(getattr(self.cfg, "STAGNATION_EXIT_SEC", 90.0))
            never_ran = peak_pct < max_peak
            in_flat_band = abs(pnl_pct) <= flat_band
            losing_flat = pnl_pct <= loss_cut and abs(pnl_pct) <= flat_band * 2
            if never_ran and (in_flat_band or losing_flat or pnl_pct <= loss_cut):
                stagnation_ctx = {
                    "ticker": self.current_ticker,
                    "price": current_px,
                    "entry": entry_px,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "peak_pct": round(peak_pct * 100, 2),
                    "stagnant_sec": round(stagnant_sec, 1),
                    "price_frozen_sec": round(stagnant_sec, 1),
                    "stop": self._position_stop,
                    "target": self._position_target,
                }
                ppo_exit, ppo_conf, ppo_reason = False, 0.5, ""
                try:
                    ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
                except Exception:
                    pass
                if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                    if self._has_ai_council(self.current_ticker or "", "stagnation_check"):
                        return False, "council_deliberating"
                    ai_dec = self.ai_commander.decide_stagnation(
                        stagnation_ctx, ppo_exit, ppo_conf, ppo_reason,
                    )
                    self._last_stagnation_decision = ai_dec
                    if ai_dec.get("pending"):
                        self._set_ai_council(self.current_ticker or "", "stagnation_check", {
                            "fingerprint": ai_dec["fingerprint"],
                            "ppo_exit": ppo_exit,
                            "ppo_conf": ppo_conf,
                            "ppo_reason": ppo_reason,
                            "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                            "stagnant_sec": stagnant_sec,
                            "stagnation_sec": stagnation_sec,
                            "ctx": stagnation_ctx,
                            "current_px": current_px,
                        })
                        log.info(
                            f"  🧠 COUNCIL stagnation {self.current_ticker}: "
                            f"{(ai_dec.get('reason') or 'deliberating')[:100]} | "
                            f"{ai_dec.get('pipeline', '')}"
                        )
                        return False, "council_deliberating"
                    if ai_dec.get("force_snapshot") and self.current_ticker:
                        snap_px = self._force_price_snapshot(self.current_ticker)
                        self._last_price_snapshot_at = time.time()
                        if snap_px > 0:
                            current_px = snap_px
                    min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
                    if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf * 0.9:
                        return True, f"ai_stagnation: {ai_dec.get('reason', '')[:100]}"
                # Hard guardrail only when council mode is off
                if (
                    not is_ai_unlimited(self.cfg)
                    and not is_ai_council_mode(self.cfg)
                    and stagnant_sec >= stagnation_sec
                ):
                    if never_ran and (in_flat_band or losing_flat):
                        return True, (
                            f"stagnation_guard: {stagnant_sec:.0f}s flat "
                            f"P&L {pnl_pct:+.2%} peak {peak_pct:+.2%}"
                        )
                    if pnl_pct < loss_cut and never_ran:
                        return True, (
                            f"dead_momentum_guard: {pnl_pct:+.2%} for {stagnant_sec:.0f}s "
                            f"(peak {peak_pct:+.2%})"
                        )

        # Lock profit — AI first; green lock quick-scalp if AI stalls
        if peak_pct > 0.015:
            giveback = peak_pct - pnl_pct
            if giveback > peak_pct * 0.4 and pnl_pct > 0.003:
                ticker = self.current_ticker or ""
                reason = f"profit_lock: peak +{peak_pct:.2%} now +{pnl_pct:.2%}"
                stalled = self._ai_profit_decision_stalled(pnl_pct)
                from core.green_profit_lock import evaluate_green_lock

                should_lock, lock_reason = evaluate_green_lock(
                    self.cfg,
                    pnl_pct=pnl_pct,
                    peak_pct=peak_pct,
                    ai_stalled=stalled,
                    giveback_from_peak=giveback,
                    was_green=getattr(self, "_was_in_profit", False),
                )
                if should_lock:
                    return True, lock_reason
                if is_ai_council_mode(self.cfg) and self.ai_commander and not stalled:
                    ppo_exit, ppo_conf, ppo_reason = False, 0.55, reason
                    try:
                        ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
                        ppo_conf = max(ppo_conf, 0.55)
                    except Exception:
                        pass
                    if self._deliberate_exit_council(
                        ticker, current_px, True, ppo_conf, ppo_reason or reason,
                        {"signal": "profit_lock"},
                    ):
                        return False, "council_deliberating"
                else:
                    return True, reason

        try:
            ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
            ticker = self.current_ticker or ""
            if is_ai_council_mode(self.cfg) and self.ai_commander:
                if self._deliberate_exit_council(
                    ticker, current_px, ppo_exit, ppo_conf, ppo_reason,
                ):
                    return False, "council_deliberating"
            elif getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                if self._has_ai_council(ticker, "exit_decision"):
                    return False, "council_deliberating"
                exit_ctx = {
                    "ticker": ticker,
                    "price": current_px,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "entry": entry_px,
                    "stop": self._position_stop,
                    "target": self._position_target,
                }
                ai_dec = self.ai_commander.decide_exit(
                    exit_ctx, obs=self._build_ppo_obs(current_px),
                )
                if ai_dec.get("pending"):
                    self._set_ai_council(ticker, "exit_decision", {
                        "fingerprint": ai_dec["fingerprint"],
                        "ppo_exit": ppo_exit,
                        "ppo_conf": ppo_conf,
                        "ppo_reason": ppo_reason,
                        "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                        "ctx": exit_ctx,
                        "current_px": current_px,
                    })
                    return False, "council_deliberating"
                if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= self.cfg.CONFIDENCE_THRESHOLD:
                    return True, f"AI_exit: conf={float(ai_dec.get('confidence', 0)):.2f} | {ai_dec.get('reason', '')[:80]}"
            elif ppo_exit and ppo_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                return True, f"AI_exit: conf={ppo_conf:.2f} | {ppo_reason[:80]}"
        except Exception:
            pass
        
        try:
            fast_df = None
            if self._active_stream_ticker and self._active_stream_ticker in self._target_monitors:
                fast_df = self._target_monitors[self._active_stream_ticker].get_bar_dataframe()
            if fast_df is None and hasattr(self.data, 'get_bar_dataframe'):
                fast_df = self.data.get_bar_dataframe()
            if fast_df is not None and len(fast_df) >= 10:
                slippage = self._predict_slippage(fast_df, current_px)
                ticker = self.current_ticker or ""
                if slippage > 0.75 and pnl_pct > 0.005:
                    reason = f"slippage_risk: {slippage:.0%}"
                    if is_ai_council_mode(self.cfg) and self.ai_commander:
                        if self._deliberate_exit_council(
                            ticker, current_px, True, 0.6, reason, {"signal": "slippage"},
                        ):
                            return False, "council_deliberating"
                    else:
                        return True, reason
                is_spike, _ = self._detect_volume_spike(fast_df)
                fade_exit, fade_reason = evaluate_wave_end_on_spike_fade(
                    self.cfg, fast_df, current_px, entry_px, self._position_peak, pnl_pct,
                )
                if fade_exit:
                    reason = fade_reason
                    if is_ai_council_mode(self.cfg) and self.ai_commander:
                        if mechanical_bypass_council(self.cfg):
                            return True, reason
                        if self._deliberate_exit_council(
                            ticker, current_px, True, 0.55, reason, {"signal": "wave_end_spike_fade"},
                        ):
                            return False, "council_deliberating"
                    else:
                        return True, reason
                if not is_spike and pnl_pct > 0.012:
                    reason = f"wave_end: profit {pnl_pct:.2%} volume fading"
                    if is_ai_council_mode(self.cfg) and self.ai_commander:
                        if self._deliberate_exit_council(
                            ticker, current_px, True, 0.55, reason, {"signal": "wave_end"},
                        ):
                            return False, "council_deliberating"
                    else:
                        return True, reason
        except Exception:
            pass
        
        if unrealized_pnl > 0 and unrealized_pnl < 2.0 and risk_usd > 35 and pnl_pct < 0.008:
            if getattr(self.cfg, "USE_FIXED_RISK_CAP", False):
                return True, f"low_profit_high_risk: ${unrealized_pnl:.2f}"
        
        return False, "hold"
    def _update_trailing_stops(self, current_px: float):
        """Ratchet stop / extend TP — always applies mechanical trail; prefetches council when AI on."""
        if self.shares <= 0 or self._entry_price <= 0 or not self.bracket_handle:
            return

        mech_stop, mech_target = self._compute_mechanical_trail(current_px)
        entry = self._entry_price
        pnl_pct = ((current_px / entry) - 1) * 100 if entry else 0
        peak_pct = (
            ((self._position_peak / entry) - 1) * 100
            if self._position_peak > entry else pnl_pct
        )

        pipeline_on = getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True)
        ai_full = getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander
        if pipeline_on and ai_full:
            ticker = self.current_ticker or ""
            if ticker:
                try:
                    from core.council_nanny import prefetch_enabled
                    if prefetch_enabled(self.cfg):
                        self.ai_commander.prefetch_position_manage({
                            "ticker": ticker,
                            "price": current_px,
                            "pnl_pct": round(pnl_pct, 2),
                            "peak_pct": round(peak_pct, 2),
                            "stop": self._position_stop,
                            "target": self._position_target,
                            "mechanical_stop": mech_stop,
                            "mechanical_target": mech_target,
                        })
                except Exception:
                    pass

        if mech_stop:
            self._apply_stop_update(mech_stop, f"trail locked +{peak_pct / 100:.2%}")
        if mech_target:
            self._apply_target_update(mech_target, "momentum TP extension")
    def _deliberate_exit_council(
        self,
        ticker: str,
        current_px: float,
        ppo_exit: bool,
        ppo_conf: float,
        ppo_reason: str,
        extra_ctx: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Run exit council. Returns True if deliberating (pending) or position was exited.
        False means council says hold — caller continues other checks.
        """
        entry_px = self._entry_price
        pnl_pct_frac = ((current_px / entry_px) - 1) if entry_px else 0.0
        pnl_pct = pnl_pct_frac * 100

        if profit_exit_bypasses_council(
            self.cfg, ppo_reason or "", pnl_pct_frac,
            ai_stalled=self._ai_profit_decision_stalled(pnl_pct_frac),
        ) and ppo_exit:
            log.info(f"  🎯 PROFIT HUNT bypass council: {ppo_reason[:80]}")
            track_profit_hunt_event(
                self.cfg, "profit_hunt_exit", ticker,
                {"reason": ppo_reason, "price": current_px, "bypass": "council"},
                pnl_usd=(current_px - entry_px) * self.shares if entry_px else 0,
                pnl_pct=pnl_pct_frac, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, ppo_reason[:120])
            return True

        if not self.ai_commander or not is_ai_council_mode(self.cfg):
            if ppo_exit and ppo_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                self._exit_position(current_px, f"ppo_exit: {ppo_reason[:80]}")
                return True
            return False
        if self._has_ai_council(ticker, "exit_decision"):
            return True
        exit_ctx = {
            "ticker": ticker,
            "price": current_px,
            "pnl_pct": round(pnl_pct, 2),
            "entry": entry_px,
            "stop": self._position_stop,
            "target": self._position_target,
            **(extra_ctx or {}),
        }
        ai_dec = self.ai_commander.decide_exit(
            exit_ctx, obs=self._build_ppo_obs(current_px),
        )
        if ai_dec.get("pending"):
            self._set_ai_council(ticker, "exit_decision", {
                "fingerprint": ai_dec["fingerprint"],
                "ppo_exit": ppo_exit,
                "ppo_conf": ppo_conf,
                "ppo_reason": ppo_reason,
                "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                "ctx": exit_ctx,
                "current_px": current_px,
            })
            log.info(
                f"  🧠 COUNCIL exit {ticker}: "
                f"{(ai_dec.get('reason') or 'deliberating')[:80]} | {ai_dec.get('pipeline', '')}"
            )
            return True
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
                f"{ai_dec.get('pipeline', '')}"
            )
            self._exit_position(current_px, f"council_exit: {ai_dec.get('reason', '')[:80]}")
            return True
        return False
    def _deliberate_risk_exit(self, ticker: str, current_px: float, risk_signal: str) -> bool:
        """Risk-engine exit via council. True = pending or exited."""
        entry_px = self._entry_price
        pnl_pct_frac = ((current_px / entry_px) - 1) if entry_px else 0.0
        pnl_pct = pnl_pct_frac * 100

        if profit_exit_bypasses_council(
            self.cfg, risk_signal, pnl_pct_frac,
            ai_stalled=self._ai_profit_decision_stalled(pnl_pct_frac),
        ):
            log.info(f"  ⚡ PROFIT HUNT risk bypass: {risk_signal}")
            track_profit_hunt_event(
                self.cfg, risk_signal, ticker,
                {"reason": risk_signal, "price": current_px, "bypass": "council"},
                pnl_usd=(current_px - entry_px) * self.shares if entry_px else 0,
                pnl_pct=pnl_pct_frac, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, risk_signal)
            return True

        if not self.ai_commander or not is_ai_council_mode(self.cfg):
            log.info(f"  ⚡ RISK EXIT: {risk_signal}")
            self._exit_position(current_px, risk_signal)
            return True
        if self._has_ai_council(ticker, "risk_exit"):
            return True
        ppo_exit, ppo_conf, ppo_reason = False, 0.5, ""
        try:
            ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
        except Exception:
            pass
        ctx = {
            "ticker": ticker,
            "price": current_px,
            "pnl_pct": round(pnl_pct, 2),
            "risk_signal": risk_signal,
            "stop": self._position_stop,
            "target": self._position_target,
        }
        ai_dec = self.ai_commander.decide_risk_exit(
            ctx, risk_signal, ppo_exit, ppo_conf, ppo_reason,
        )
        if ai_dec.get("pending"):
            self._set_ai_council(ticker, "risk_exit", {
                "fingerprint": ai_dec["fingerprint"],
                "risk_signal": risk_signal,
                "ppo_exit": ppo_exit,
                "ppo_conf": ppo_conf,
                "ppo_reason": ppo_reason,
                "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                "ctx": ctx,
                "current_px": current_px,
            })
            log.info(
                f"  🧠 COUNCIL risk {ticker}: {risk_signal} | "
                f"{(ai_dec.get('reason') or 'deliberating')[:80]}"
            )
            return True
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL risk exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
                f"{ai_dec.get('pipeline', '')}"
            )
            self._exit_position(current_px, f"council_risk: {risk_signal}")
            return True
        return False
    def _resolve_stagnation_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_stagnation_council(st)
        if ai_dec.get("pending"):
            self._last_stagnation_decision = ai_dec
            return
        self._ai_councils.pop(key, None)
        self._last_stagnation_decision = ai_dec
        pipeline = str(ai_dec.get("pipeline", ""))
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf * 0.9:
            log.info(
                f"  🧠 COUNCIL stagnation exit {ticker}: "
                f"{(ai_dec.get('reason') or '')[:80]} | {pipeline}"
            )
            self._exit_position(px, f"ai_stagnation: {ai_dec.get('reason', '')[:100]}")
            self._save_position_context(ticker)
    def _resolve_position_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        ctx = dict(st.get("ctx") or {})
        ctx["price"] = px
        ctx["pnl_usd"] = round((px - self._entry_price) * self.shares, 2)
        ctx["pnl_pct"] = round(((px / self._entry_price) - 1) * 100, 2) if self._entry_price else 0
        ctx["stop"] = self._position_stop
        ctx["target"] = self._position_target
        st["ctx"] = ctx
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_position_council(st, df=self._scan_data_cache.get(ticker))
        if ai_dec.get("pending"):
            max_wait = council_max_wait_sec(self.cfg)
            if time.time() - float(st.get("started_at", time.time())) > max_wait:
                self._ai_councils.pop(key, None)
            return
        self._ai_councils.pop(key, None)
        pipeline = str(ai_dec.get("pipeline", ""))
        log.info(
            f"  🧠 COUNCIL manage {ticker}: {ai_dec.get('action', 'HOLD')} | "
            f"{(ai_dec.get('reason') or '')[:80]} | {pipeline}"
        )
        self._apply_position_manage_decision(ai_dec, px)
        self._save_position_context(ticker)
    def _resolve_exit_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_exit_council(st)
        if ai_dec.get("pending"):
            max_wait = council_max_wait_sec(self.cfg)
            age = time.time() - float(st.get("started_at", time.time()))
            if age > max_wait:
                self._ai_councils.pop(key, None)
                ppo_reason = str(st.get("ppo_reason", "") or "")
                if "shape" in ppo_reason.lower() or "observation" in ppo_reason.lower():
                    log.warning(
                        f"  ⚠️ COUNCIL exit {ticker}: clearing stuck council (bad PPO obs)"
                    )
                return
            return
        self._ai_councils.pop(key, None)
        pipeline = str(ai_dec.get("pipeline", ""))
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | {pipeline}"
            )
            self._exit_position(px, f"council_exit: {ai_dec.get('reason', '')[:80]}")
            self._save_position_context(ticker)
    def _resolve_risk_exit_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_risk_exit_council(st)
        if ai_dec.get("pending"):
            return
        self._ai_councils.pop(key, None)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL risk exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
                f"{ai_dec.get('pipeline', '')}"
            )
            self._exit_position(px, f"council_risk: {st.get('risk_signal', 'risk')}")
            self._save_position_context(ticker)
    def _apply_position_manage_decision(self, decision: Dict[str, Any], current_px: float):
        action = str(decision.get("action", "HOLD")).upper()
        reason = decision.get("reason", "")
        if action == "EXIT":
            log.info(f"  🧠 AI EXIT: {reason}")
            self._exit_position(current_px, f"ai_position: {reason}")
            return
        if action == "WIDEN_STOP":
            new_stop = decision.get("stop")
            if new_stop and float(new_stop) < self._position_stop - 0.0001:
                self._apply_stop_update(float(new_stop), f"AI widen (ATR): {reason}")
        elif action == "TIGHTEN_STOP":
            new_stop = decision.get("stop")
            if new_stop and float(new_stop) > self._position_stop + 0.0001:
                self._apply_stop_update(float(new_stop), f"AI tighten (ATR): {reason}")
        elif action == "RAISE_TP":
            new_target = decision.get("target")
            if new_target and float(new_target) > self._position_target + 0.0001:
                self._apply_target_update(float(new_target), f"AI raise TP (ATR): {reason}")
