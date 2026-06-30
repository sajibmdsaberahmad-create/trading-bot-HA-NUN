#!/usr/bin/env python3
"""Extracted from scalper_runner — scalper session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner


class ScalperSessionMixin:
    """Mixin — use via ScalperRunner multiple inheritance."""

    def _suspend_off_hours_market_data(self, market_state: str) -> None:
        """Release IB market data streams when session is not tradable."""
        if not getattr(self.cfg, "OFF_HOURS_SUSPEND_MARKET_DATA", True):
            return
        if self._md_suspended:
            return
        self._md_suspended = True
        self.conn.set_market_data_active(False)
        self.conn.clear_pending_session_reclaim()
        n = len(self._target_monitors)
        if n:
            log.info(
                f"⏸ Off-hours ({market_state}) — releasing {n} market data stream(s)"
            )
            self._stop_all_target_streams()
    def _resume_tradable_market_data(self) -> None:
        """Re-open streams when pre-market/RTH returns."""
        if not self._md_suspended:
            return
        self._md_suspended = False
        self.conn.set_market_data_active(True)
        if self._locked_targets:
            log.info("📡 Session tradable — re-subscribing market data streams")
            self._queue_locked_stream_repairs()
            self._ensure_locked_streams(quiet=False)
    def _halt_trading_for_closed_market(self, market_state: str) -> None:
        """Cancel in-flight entry orders when session is not tradable."""
        if not (
            self._pending_entry_ticker
            or self._entry_poll_states
            or self._pending_brackets_by_ticker
        ):
            return
        tickers = list(self._entry_poll_states.keys()) or list(self._pending_brackets_by_ticker.keys())
        if self._pending_entry_ticker and self._pending_entry_ticker not in tickers:
            tickers.append(self._pending_entry_ticker)
        for ticker in tickers:
            try:
                self.broker.cancel_open_orders_for_symbol(ticker)
            except Exception:
                pass
        self.bracket_handle = None
        self._clear_pending_entry(None, cooldown_sec=120.0)
        log.info(f"⏸ Pending entry halted — market {market_state}")
    def _on_day_session_end(self, market_state: str) -> None:
        """RTH/pre-market window ended — stop all trading for the day."""
        if getattr(self, "_day_session_ended", False):
            return
        self._day_session_ended = True
        sessions = allowed_trading_sessions_label(self.cfg)
        log.info(
            f"🏁 DAY SESSION FINISHED ({market_state}) — enabled sessions: {sessions}. "
            f"No new orders until next pre-market. Open brackets remain on IB."
        )
        self._halt_trading_for_closed_market(market_state)
        self._suspend_off_hours_market_data(market_state)
        self._deferred_exits.clear()
        if getattr(self.cfg, "DAILY_IB_LEARNING_ON_SESSION_END", True):
            try:
                from core.daily_ib_learning import schedule_daily_ib_learning
                schedule_daily_ib_learning(
                    self.cfg, self,
                    trigger="session_end",
                    connector=self.conn,
                )
            except Exception as exc:
                log.debug(f"Session-end IB learning schedule: {exc}")
        try:
            from core.slow_coach import schedule_post_session_coach
            from core.market_hours import now_et
            schedule_post_session_coach(
                self.cfg, self, day=now_et().strftime("%Y-%m-%d"),
            )
        except Exception as exc:
            log.debug(f"Session-end coach lane: {exc}")
    def _on_rth_open(self, old_state: str) -> None:
        """
        Bell at 09:30 ET — shift to live RTH mode when transitioning from pre-market.
        Mid-day startup (old_state=startup) only clears flaky MD blocks — no teardown.
        """
        today = now_et().strftime("%Y-%m-%d")
        if self._rth_open_day == today:
            return
        self._rth_open_day = today
        self._day_session_ended = False

        is_startup = old_state == "startup"
        status = rth_status_line(self.cfg)
        from core.startup_log import sinfo
        if is_startup:
            sinfo(self.cfg, f"🔔 RTH OPEN ({old_state} → open) | {status}")
        else:
            log.info(f"🔔 RTH OPEN ({old_state} → open) | {status}")
            log.info(f"  🧠 {ai_session_context_block(self.cfg)}")

        cleared = clear_transient_md_blocks(self.cfg)
        if cleared:
            for t in cleared:
                self._contract_blacklist.discard(t.upper())
                self._contract_blacklist.discard(t)
        try:
            from core.market_data_learning import clear_hmds_transient_blocks
            clear_hmds_transient_blocks()
        except Exception:
            pass
        try:
            from core.market_context import refresh_macro_context
            ctx = refresh_macro_context(force=True)
            log.info(
                f"🌍 RTH macro: SPY {ctx.get('spy_pct', 0):+.2f}% | "
                f"QQQ {ctx.get('qqq_pct', 0):+.2f}% | "
                f"VIX {ctx.get('vix_level', 0):.1f} ({ctx.get('risk_tone', '?')})"
            )
        except Exception:
            pass

        if is_startup:
            sinfo(
                self.cfg,
                "📡 Mid-session start — streams kept (no 9:30 teardown)",
            )
            teach_profit_hunt_lesson(
                self.autopilot, self.consciousness,
                "RTH session live — profit hunt on stream bars while cache warms.",
            )
            return

        teach_profit_hunt_lesson(
            self.autopilot, self.consciousness,
            "RTH open — super alert: opening noise, ride real volume, protect capital.",
        )
        self._observe_runtime(
            "rth_open",
            old_state=old_state,
            tier=rth_tier(self.cfg),
            cleared_md=cleared[:20],
        )

        if getattr(self.cfg, "RTH_OPEN_STREAM_REFRESH", True):
            for ticker in list(self._target_monitors.keys()):
                self._stop_target_stream(ticker)
            self._scan_data_cache.clear()
            self._bar_warm_due = True
            self._bar_warm_idx = 0
            self._queue_locked_stream_repairs()

        if getattr(self.cfg, "RTH_OPEN_FORCE_RESCAN", True):
            self._last_scan_time = 0.0
            self._needs_initial_scan = True
            log.info("  🔍 RTH open — forcing live IB universe rescan")

        try:
            from core.ai_session_limits import maybe_refresh_session_limits
            maybe_refresh_session_limits(self, min_interval_sec=0.0)
        except Exception:
            pass

        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            try:
                send_dynamic_notification(
                    self.notifier, self.autopilot, "rth_open",
                    self._notify_context({"tier": rth_tier(self.cfg), "old_state": old_state}),
                    f"🔔 RTH OPEN — super alert mode\n{status}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            except Exception:
                pass

        if not is_startup:
            try:
                from core.halim_companion import companion_session_ping
                companion_session_ping(self, self.cfg, trigger="rth_open")
            except Exception as exc:
                log.debug(f"Halim companion RTH ping: {exc}")
    def _schedule_self_train(self):
        """Local weight update only — no git push until session shutdown."""
        try:
            from core.async_utils import get_background_worker
            get_background_worker()._executor.submit(self._daily_self_train)
        except Exception:
            try:
                self._daily_self_train()
            except Exception:
                pass
    def _write_live_metrics(self):
        try:
            now = time.time()
            if now - self._last_metrics_write < 2.0:
                return
            self._last_metrics_write = now
            win_rate = (self.risk.win_rate * 100) if hasattr(self.risk, 'win_rate') else 0.0
            scan_data = []
            for r in self.scan_results[:5]:
                if isinstance(r, dict):
                    ticker = r.get("ticker", "?")
                    px = self._live_price_for(ticker, float(r.get("price", 0) or 0))
                    scan_data.append({
                        "ticker": ticker,
                        "price": round(px, 4) if px > 0 else r.get("price", 0),
                        "score": round(float(r.get("total_score", 0)), 1),
                        "reason": str(r.get("reasons", ""))[:30],
                    })
                else:
                    px = self._live_price_for(r.ticker, float(r.price or 0))
                    scan_data.append({
                        "ticker": r.ticker, "price": round(px, 4) if px > 0 else r.price,
                        "score": round(r.rank_score, 1), "reason": r.reason[:30],
                    })
            metrics = {
                "mode": "HANOON",
                "account_equity": round(self.account_equity, 2),
                "available_cash": round(self.available_cash or 0, 2),
                "position_value": round(self.shares * self._latest_price(), 2),
                "nav": round(self.bot_nav, 2),
                "deployed_pct": round(
                    (self.shares * self._latest_price()) / (self.account_equity + 1e-9) * 100, 1
                ),
                "current_ticker": self.current_ticker or "NONE",
                "position": f"{self.shares:.0f} {self.current_ticker}" if self.shares > 0 else "NONE",
                "win_rate": round(win_rate, 1),
                "trades_today": self.trades_today,
                "top_pick": self.top_pick.ticker if self.top_pick else None,
                "top_score": self.top_pick.rank_score if self.top_pick else 0,
                "next_best": (
                    self._next_best_pick.ticker
                    if getattr(self, "_next_best_pick", None) else None
                ),
                "next_best_score": round(getattr(self, "_next_best_score", 0.0), 1),
                "scan_results": scan_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                if self.shares <= 0 and now - getattr(self, "_last_ai_narrative", 0) > 30.0:
                    self._last_ai_narrative = now
                    metrics["ai_narrative"] = self.ai_commander.account_narrative(metrics)
            with open("live_metrics.json", "w") as f:
                json.dump(metrics, f, indent=2)
        except Exception as exc:
            log.debug(f"Could not write live_metrics.json: {exc}")
    def _maybe_resume_ib_from_shadow(self) -> None:
        """Paper: clear shadow gate on startup so orders reach IB Gateway."""
        paper_bypass = (
            getattr(self.cfg, "PAPER_TRADING", False)
            and not getattr(self.cfg, "SHADOW_ON_PAPER", False)
        )
        if not paper_bypass and not getattr(self.cfg, "SHADOW_RESUME_ON_START", True):
            return
        reason = (
            "paper account — shadow sim disabled (SHADOW_ON_PAPER=false)"
            if paper_bypass
            else "SHADOW_RESUME_ON_START"
        )
        if self.shadow_circuit.force_resume_live(reason=reason):
            msg = (
                "☀️ SHADOW CLEARED — entries will place **real IB paper orders** "
                "(you will get IB app notifications again)."
            )
            log.info(msg)
            try:
                self.notifier.info(msg)
            except Exception:
                pass
    def _log_tick_stream_config(self) -> None:
        """Startup audit — tick-by-tick vs 5s fallback and IB stream budget."""
        from core.data import tick_by_tick_type

        tbt = tick_by_tick_type(self.cfg)
        use_tick = bool(getattr(self.cfg, "USE_TICK_STREAM", True))
        paper_rt_only = bool(
            getattr(self.cfg, "PAPER_TRADING", False)
            and getattr(self.cfg, "PAPER_REALTIME_BARS_ONLY", False)
        )
        if paper_rt_only:
            mode = "5s bars only (PAPER_REALTIME_BARS_ONLY)"
        elif use_tick:
            try:
                from core.sniper_execution import sniper_tick_stream_count, sniper_tick_streams_enabled
                if sniper_tick_streams_enabled(self.cfg):
                    n = sniper_tick_stream_count(self.cfg) or 0
                    mode = f"sniper top-{n} tick + 5s on rest"
                else:
                    mode = f"tick-by-tick ({tbt})"
            except Exception:
                mode = f"tick-by-tick ({tbt})"
        else:
            mode = "5s bars (USE_TICK_STREAM=false)"
        n_tick = tick_stream_count(self.cfg)
        n_rt = max_realtime_bar_streams(self.cfg)
        log.info(
            f"📡 Market data: {mode} | IB budget {n_tick} tick + {n_rt} 5s-bars "
            f"(cap ~5 each — extras deferred)"
        )
    def _log_startup_banner(self) -> None:
        """One structured boot summary — details at DEBUG when STARTUP_LOG_COMPACT=true."""
        from core.startup_log import log_block, sinfo, startup_compact
        from core.data import tick_by_tick_type
        from core.market_hours import allowed_trading_sessions_label
        from core.ram_tier import ram_tier_summary
        from core.memory_guard import memory_status
        from core.ai_session_limits import format_limits_log, should_ai_define_limits

        acct_vals = self.conn.ib.accountValues()
        account = acct_vals[0].account if acct_vals else "unknown"
        mode = "PAPER" if self.cfg.PAPER_TRADING else "LIVE"
        market_state = get_market_state(self.cfg)
        can_trade, _ = can_trade_now(self.cfg)
        sessions = allowed_trading_sessions_label(self.cfg)

        paper_rt = bool(
            getattr(self.cfg, "PAPER_TRADING", False)
            and getattr(self.cfg, "PAPER_REALTIME_BARS_ONLY", False)
        )
        if paper_rt:
            md_mode = "5s bars (paper)"
        elif getattr(self.cfg, "USE_TICK_STREAM", True):
            md_mode = f"tick ({tick_by_tick_type(self.cfg)})"
        else:
            md_mode = "5s bars"

        defer = getattr(self.cfg, "SCAN_DEFER_IB_ON_STARTUP", False)
        warmup = int(getattr(self.cfg, "IB_SCANNER_WARMUP_SEC", 5))
        from core.scanner_session import scanner_session_log_line
        scan_mode = f"deferred curated" if defer else scanner_session_log_line(self.cfg)

        council_on = getattr(self.cfg, "COUNCIL_ENABLED", False)
        council = (
            f"{getattr(self.cfg, 'COUNCIL_BACKEND', 'groq')}"
            if council_on else "off"
        )

        lines = [
            f"{mode} | {account} | ${self.account_equity:,.0f}",
            f"Market: {market_state} | tradable={'yes' if can_trade else 'no'} | sessions: {sessions}",
            f"Scanner: {scan_mode} | MD: {md_mode} | Council: {council}",
            f"PPO: {'loaded' if not getattr(self, '_model_fresh', True) else 'fresh'} | "
            f"tick budget {tick_stream_count(self.cfg)}+{max_realtime_bar_streams(self.cfg)} 5s",
        ]
        if hasattr(self, "pilot"):
            vs = self.pilot.get_veteran_status()
            lines.append(
                f"Pilot: {vs.get('level', '?')} XP={vs.get('total_xp', 0)} "
                f"conf={vs.get('confidence_threshold', 0):.0%}"
            )
        if should_ai_define_limits(self.cfg):
            lines.append(format_limits_log(self.cfg, self.account_equity))

        log_block("HANOON STARTUP", lines)

        if startup_compact(self.cfg):
            return

        mem = memory_status(self.cfg)
        tier_info = ram_tier_summary(self.cfg)
        sinfo(
            self.cfg,
            f"🧠 Cloud council detail: groq={getattr(self.cfg, 'GROQ_MODEL', '?')} | "
            f"gemini={getattr(self.cfg, 'GEMINI_MODEL', '?')} | "
            f"RAM {mem['total_ram_mb']}MB tier={tier_info['label']}",
            force=True,
        )
        discipline_log = startup_log_line(self.cfg)
        if discipline_log:
            sinfo(self.cfg, discipline_log, force=True)
        try:
            from core.smart_stack import startup_banner_line
            ss_line = startup_banner_line(self.cfg)
            if ss_line:
                sinfo(self.cfg, ss_line, force=True)
        except Exception:
            pass
    def _register_shutdown_signals(self):
        import signal

        def _handler(signum, _frame):
            log.info(f"Signal {signum} received — graceful shutdown...")
            try:
                from core.learning_persistence import emergency_snapshot
                emergency_snapshot(self.cfg, model=getattr(self, "model", None), runner=self)
            except Exception:
                pass
            self._shutdown_requested_flag = True
            try:
                self.ib.sleep(0)
            except Exception:
                pass
            if getattr(self, "_shutdown_done", False):
                import os
                os._exit(0)

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
    def _shutdown_abort(self) -> bool:
        """True when stop script or signal requested exit."""
        if getattr(self, "_shutdown_requested_flag", False):
            return True
        try:
            from core.shutdown_control import shutdown_requested
            return shutdown_requested()
        except Exception:
            return False
    def _on_ib_connectivity(self, event: str) -> None:
        """IB 1100/1102 — pause/resume bar warm while socket is down."""
        if event == "connectivity_lost":
            self._ib_connectivity_paused = True
            log.info("IB connectivity handler: market-data warm paused (1100)")
        elif event == "data_ok":
            self._ib_connectivity_paused = False
            log.info("IB connectivity handler: market-data warm resumed (1102)")
    def _resubscribe_all_streams(self, force: bool = False) -> None:
        """Re-request live streams after IB reconnect, 1101, or 10197 reclaim."""
        if not self._locked_targets:
            return
        try:
            from core.market_data_learning import (
                clear_competing_session_blocks,
                clear_reconnect_transient_blocks,
            )
            cleared = clear_competing_session_blocks() + clear_reconnect_transient_blocks()
            if cleared:
                log.info(f"  🔓 MD blocks cleared before re-subscribe ({cleared} ticker(s))")
        except Exception:
            pass
        if force:
            for ticker in list(self._target_monitors.keys()):
                self._stop_target_stream(ticker)
        self._queue_locked_stream_repairs()
        self._ensure_locked_streams(quiet=False)
        n = len(self._target_monitors)
        log.info(f"  📡 Re-subscribed {n} live stream(s) after IB reconnect")
    def _on_ib_session_reclaim(self) -> None:
        """Cancel streams before IB disconnect/reconnect so zombie MD slots are released."""
        n = len(self._target_monitors)
        if n:
            log.info(f"IB session reclaim: stopping {n} live stream(s)")
        self._stop_all_target_streams()
        self._queue_locked_stream_repairs()
    def _train_off_hours(self):
        """
        When market is closed, launch isolated training subprocess.
        
        Training is moved to a separate short-lived process to:
        - Free MPS/GPU memory completely after training
        - Prevent memory fragmentation in the long-running trading process
        - Isolate crashes from the main trading loop
        """
        try:
            log.info("🧠 OFF-HOURS TRAINING: Launching isolated training subprocess...")
            
            # Full IB yesterday bundle → Ollama analyze + PPO (beat yesterday goal)
            if getattr(self.cfg, "DAILY_IB_LEARNING_ENABLED", True):
                try:
                    from core.daily_ib_learning import run_daily_ib_learning_cycle
                    from core.market_hours import learning_day_for_trigger
                    ib_day = learning_day_for_trigger("off_hours")
                    run_daily_ib_learning_cycle(
                        self.cfg, self,
                        connector=self.conn,
                        trigger="off_hours",
                        day_str=ib_day,
                        train_ppo=True,
                    )
                except Exception as exc:
                    log.debug(f"Off-hours IB learning: {exc}")
            
            # Update market regime from broader context (lightweight, stays in-process)
            self._update_market_context()
            
            # Train weights on historical data (lightweight, stays in-process)
            self._daily_self_train()
            
            # Launch heavy training (Transformer + PPO + LSTM) in isolated subprocess
            if getattr(self.cfg, "OFF_HOURS_HEAVY_TRAINING", True):
                try:
                    from core.memory_guard import is_low_ram_machine
                    light = is_low_ram_machine()
                    timesteps = "40000" if light else "100000"
                    session_id = launch_training([
                        sys.executable, "-m", "core.advanced_training",
                        "--mode", "full",
                        "--ticker", self.cfg.TICKER,
                        "--ppo-timesteps", timesteps,
                        "--epochs", "12" if timesteps == "40000" else "20",
                        "--save-model", "models/transformer_model.pth",
                    ], timeout_minutes=30)

                    if session_id:
                        log.info(f"🏋️ Training subprocess launched: {session_id}")
                        self.notifier.info(f"🏋️ OFF-HOURS TRAINING\nIsolated subprocess launched.\nSession: {session_id}")
                    else:
                        log.warning("Training subprocess failed to launch")
                except Exception as exc:
                    log.debug(f"Subprocess training launch failed: {exc}")
            else:
                log.info("🏋️ Off-hours heavy training skipped (OFF_HOURS_HEAVY_TRAINING=false — 8GB mode)")
            
            # Consciousness reflection (lightweight, stays in-process)
            try:
                if hasattr(self, 'consciousness') and self.consciousness:
                    self.consciousness.observe_scan({"source": "off_hours", "tickers": "live_ib"})
                    session = self.consciousness.continuous_train()
                    reflection = self.consciousness.reflect()
                    log.info(f"🧠 Consciousness reflection: {reflection[:200]}")
            except Exception as exc:
                log.debug(f"Consciousness training failed: {exc}")

            # Ollama meta-optimizer: AI proposes guarded param tweaks from performance
            try:
                if (
                    getattr(self.cfg, "OLLAMA_META_OPTIMIZER_ENABLED", True)
                    and self.autopilot
                    and getattr(self.autopilot, "core", None)
                    and getattr(self.autopilot.core, "ollama", None)
                ):
                    report = {
                        "win_rate": getattr(self.risk, "win_rate", 0.0),
                        "trades_today": self.trades_today,
                        "nav": self.bot_nav,
                        "pilot": self.pilot.get_veteran_status() if hasattr(self, "pilot") else {},
                    }
                    self.autopilot.core.ollama.meta_optimize(report, self.cfg)
                    log.info("🧬 Ollama meta-optimizer ran (guardrailed param proposals)")
            except Exception as exc:
                log.debug(f"Meta-optimizer: {exc}")
            
            # Self-improvement plan (lightweight, stays in-process)
            try:
                plan = generate_self_improvement_plan(self.cfg)
                if plan.get("adjustments"):
                    self.notifier.info(f"🧬 SELF-IMPROVEMENT PLAN\n{plan['guidelines'][:1000]}")
            except Exception as exc:
                log.debug(f"Self-improvement plan failed: {exc}")

            # Commander chat + session → guardrailed mutations & lessons
            try:
                if getattr(self.cfg, "COMMANDER_LEARNING_ENABLED", True) and self.ai_commander:
                    cl = run_commander_learning_cycle(
                        self.cfg,
                        self,
                        think_fn=self.ai_commander.compose_telegram,
                        trigger="off_hours_review",
                        apply=True,
                    )
                    if cl.get("applied", {}).get("applied"):
                        from core.commander_learning import format_apply_report
                        self.notifier.info(format_apply_report(cl)[:1200])
            except Exception as exc:
                log.debug(f"Commander learning cycle: {exc}")
            
            # Tag git release after off-hours training
            try:
                version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                push_model_release(version, notes="off_hours_full_training")
                sync_all_learning_artifacts(f"off_hours_{version}")
            except Exception:
                pass
            
            log.info("🧠 Off-hours training dispatched. Ready for next session.")
        except Exception as exc:
            log.debug(f"Off-hours training failed: {exc}")
    def _daily_self_train(self):
        try:
            weights = self._load_weights()
            # Load trade journal into win_history if not already there
            if self.trade_journal and not weights.get("win_history"):
                for trade in self.trade_journal:
                    weights["win_history"].append({
                        "result": trade["result"],
                        "pnl_usd": trade["pnl_usd"],
                        "weights_active": {k: weights.get(k, 1.0) for k in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]}
                    })
            wins = [w for w in weights.get("win_history", []) if w["result"] == "win"]
            losses = [w for w in weights.get("win_history", []) if w["result"] == "loss"]
            if wins or losses:
                win_rate = len(wins) / (len(wins) + len(losses))
                for w in weights.get("win_history", []):
                    factor = 1.15 if w["result"] == "win" else 0.85
                    for key in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]:
                        if key in w.get("weights_active", {}):
                            weights[key] = weights.get(key, 1.0) * factor
                for key in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]:
                    weights[key] = max(0.5, min(weights[key], 50.0))
                log.info(f"🧠 Self-train: win_rate={win_rate:.0%} | wins={len(wins)} losses={len(losses)} | weights updated")
            try:
                sim_scores = [
                    (r.get("total_score", 0) if isinstance(r, dict) else r.rank_score)
                    for r in self.scan_results[:10]
                ]
                if sim_scores:
                    max_score = max(sim_scores)
                    if max_score < 30:
                        weights["volume"] *= 1.2
                        weights["institutional"] *= 1.2
                        log.info(f"🧠 Weak top-score ({max_score:.0f}) → boosted volume+institutional weights")
            except Exception:
                pass
            self._save_weights(weights)
        except Exception as exc:
            log.debug(f"Self-train skipped: {exc}")
    def _maybe_daily_push(self):
        try:
            current_et = now_et()
            today_str = current_et.strftime("%Y-%m-%d")
            market_close_hour_et = 16
            if current_et.hour >= market_close_hour_et and self._last_daily_push_date != today_str:
                self._last_daily_push_date = today_str
                self._daily_self_train()
                guidelines = self._generate_guidelines()
                baseline = float(self.cfg.INITIAL_CASH)
                ib_pnl, ib_pnl_pct = self._day_pnl_ib()
                bot_pnl = self.bot_nav - baseline
                stmt = (
                    f"portfolio: {today_str} ET | "
                    f"IB=${self.account_equity:,.0f} | "
                    f"IB P&L=${ib_pnl:+,.0f} ({ib_pnl_pct:+.2f}%) | "
                    f"bot_nav=${self.bot_nav:,.0f} (internal) | "
                    f"trades={self.trades_today}"
                )
                push_daily_summary(self.bot_nav, self.account_equity)
                try:
                    weights = self._load_weights()
                    self.cfg._latest_account_balance = self.account_equity
                    os.makedirs("models", exist_ok=True)
                    with open("models/daily_guidelines.txt", "w") as f:
                        f.write(guidelines)
                        f.write(f"\nGenerated: {now_et.isoformat()}\n")
                        f.write(f"Weights: {json.dumps(weights, indent=2)}\n")
                        f.write(f"Performance: {stmt}\n")
                    # Async git commit (non-blocking)
                    self._worker.submit_git_commit(
                        files=["models/scalper_weights.json", "models/daily_guidelines.txt"],
                        message=f"train: hanoon daily self-improvement {today_str}",
                        push=True
                    )
                except Exception:
                    pass
                log.info(f"📤 {stmt}")
                log.info(f"🧭 Guidelines generated and pushed to git")
                if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                    send_dynamic_notification(
                        self.notifier, self.autopilot, "daily_summary",
                        self._notify_context({"stmt": stmt, "guidelines": guidelines[:500]}),
                        f"📊 HANOON DAILY COMPLETE\n{stmt}\n\n{guidelines}",
                        ai_commander=self.ai_commander,
                        consciousness=self.consciousness,
                        pilot=self.pilot,
                    )
                else:
                    self.notifier.info(f"📊 HANOON DAILY COMPLETE\n{stmt}\n\n{guidelines}")
                try:
                    from core.daily_self_evaluation import schedule_daily_self_evaluation
                    schedule_daily_self_evaluation(
                        self.cfg,
                        self,
                        notifier=self.notifier,
                        ai_commander=self.ai_commander,
                        autopilot=self.autopilot,
                        consciousness=self.consciousness,
                        pilot=self.pilot,
                        connector=self.conn,
                    )
                except Exception as exc:
                    log.debug(f"Daily self-eval schedule: {exc}")
        except Exception as exc:
            log.debug(f"Daily push skipped: {exc}")
    def _write_init_report(self) -> str:
        """Write full initialization report and push to git."""
        try:
            from datetime import datetime
            import json
            os.makedirs("models/daily_reports", exist_ok=True)
            report_path = f"models/daily_reports/init_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            report = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": "HANOON",
                "ticker": self.cfg.TICKER,
                "account": "DUO429233",
                "equity": round(self.account_equity, 2),
                "max_trade_usd": self.cfg.MAX_TRADE_SIZE_USD,
                "risk_per_trade": self.cfg.risk_amount_usd(self.account_equity),
                "baseline": self.cfg.INITIAL_CASH,
                "universe_size": len(PENNY_STOCK_UNIVERSE),
                "ai_models": list(self.ai_components.keys()) if self.ai_components else [],
                "ppo_loaded": self.model is not None,
                "consciousness_active": hasattr(self, 'consciousness') and self.consciousness is not None,
                "market_status": get_market_state(self.cfg),
            }
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            # Push to git (async, non-blocking)
            try:
                self._worker.submit_git_commit(
                    files=[report_path],
                    message=f"report: hanoon init {datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                    push=False
                )
            except Exception:
                pass
            return report_path
        except Exception as exc:
            log.debug(f"Init report failed: {exc}")
            return "N/A"
    def _write_close_report(self):
        """Write full shutdown/session report and push to git."""
        try:
            from datetime import datetime
            import json
            os.makedirs("models/daily_reports", exist_ok=True)
            baseline = float(self.cfg.INITIAL_CASH)
            pnl = self.bot_nav - baseline
            pnl_pct = (pnl / baseline) * 100 if baseline else 0.0
            ib_start = self._ib_starting_balance or self.account_equity
            ib_change = self.account_equity - ib_start
            ib_change_pct = (ib_change / ib_start) * 100 if ib_start else 0.0
            report_path = f"models/daily_reports/close_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            report = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": "HANOON",
                "ticker": self.cfg.TICKER,
                "ib_account": round(self.account_equity, 2),
                "ib_start": round(ib_start, 2),
                "ib_change": round(ib_change, 2),
                "ib_change_pct": round(ib_change_pct, 2),
                "bot_cash": round(self.bot_cash, 2),
                "bot_nav": round(self.bot_nav, 2),
                "day_pnl": round(pnl, 2),
                "day_pnl_pct": round(pnl_pct, 2),
                "baseline": baseline,
                "trades": self.trades_today,
                "wins": len([t for t in self.trade_journal if t["result"] == "win"]),
                "losses": len([t for t in self.trade_journal if t["result"] == "loss"]),
                "position": f"{self.shares:.0f} {self.current_ticker}" if self.shares > 0 else None,
                "scan_count": len(self.scan_results),
                "top_pick": self.top_pick.ticker if self.top_pick else None,
                "weights": self._load_weights(),
                "journal": self.trade_journal[-20:],
            }
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            # Push to git (async, non-blocking)
            try:
                self._worker.submit_git_commit(
                    files=[report_path],
                    message=f"report: hanoon close {datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                    push=False
                )
            except Exception:
                pass
            return report_path
        except Exception as exc:
            log.debug(f"Close report failed: {exc}")
            return "N/A"
    def _shutdown(self):
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        guard = getattr(self, "_learning_guard", None)
        if guard is not None:
            try:
                guard.stop(trigger="session_shutdown")
            except Exception as exc:
                log.debug(f"Learning guard stop: {exc}")
            self._learning_guard = None

        try:
            from core.graceful_shutdown import flush_halim_data
            flush_halim_data(self.cfg, trigger="session_shutdown")
        except Exception as exc:
            log.debug(f"Halim shutdown flush: {exc}")

        if os.getenv("REPLAY_LIVE", "").lower() not in ("1", "true", "yes"):
            log.info("🛑 Live shutdown — flushing Halim + evolution + git…")
            try:
                from core.graceful_shutdown import flush_owned_brain
                flush_owned_brain(
                    self.cfg,
                    model=getattr(self, "model", None),
                    trigger="live_session_end",
                    push_git=False,
                )
            except Exception as exc:
                log.debug(f"Owned brain evolution: {exc}")

        self._run_account_eval("session_shutdown", force=True)

        # Write and push full session report
        report_path = self._write_close_report()
        self._refresh_account_balance()
        ib_start = self._ib_starting_balance or self.account_equity
        ib_change = self.account_equity - ib_start
        ib_change_pct = (ib_change / ib_start) * 100 if ib_start else 0.0
        from core.account_view import day_pnl
        ib_pnl_usd, ib_pnl_pct = day_pnl(self, self.cfg)
        try:
            from core.war_account import war_account_context
            war_ctx = war_account_context(self.cfg)
        except Exception:
            war_ctx = {}
        summary = "📊 HANOON SESSION CLOSE\n"
        summary += f" IB Account:    ${self.account_equity:>12,.2f}  (start: ${ib_start:,.2f})\n"
        summary += f" IB Change:     ${ib_change:>+12,.2f} ({ib_change_pct:+.2f}%)\n"
        if war_ctx:
            if war_ctx.get("war_balance_driven"):
                war_trips_bit = (
                    f"bullets_left={war_ctx.get('war_bullets_remaining', 0)}  "
                    f"fired={war_ctx.get('war_round_trips_today', 0)}"
                )
            else:
                war_trips_bit = (
                    f"trips {war_ctx.get('war_round_trips_today', 0)}/"
                    f"{war_ctx.get('war_round_trips_max', '?')}"
                )
            summary += (
                f" War pool:      ${float(war_ctx.get('war_nav', 0)):>12,.0f}  "
                f"settled=${float(war_ctx.get('war_settled_cash', 0)):,.0f}  "
                f"{war_trips_bit}  "
                f"mode={war_ctx.get('war_mode', '?')}\n"
            )
        summary += f" Bot Cash:      ${self.bot_cash:>12,.2f}\n"
        summary += f" Day P&L (IB):  ${ib_pnl_usd:>+12,.2f} ({ib_pnl_pct:+.2f}%)\n"
        summary += f" Trades:        {self.trades_today:>12d}\n"
        if self.shares > 0:
            summary += f" Position:      {self.shares:.0f} {self.current_ticker}\n"
            summary += " (bracket orders remain active on IB)\n"
        summary += f"\nReport: {report_path}\n"
        log.info(summary)
        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, "session_close",
                self._notify_context({
                    "pnl": ib_pnl_usd, "pnl_pct": ib_pnl_pct, "ib_change": ib_change,
                    "trades_today": self.trades_today, "report": str(report_path),
                }),
                summary,
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info(summary)

        if os.getenv("REPLAY_LIVE", "").lower() not in ("1", "true", "yes"):
            try:
                from core.graceful_shutdown import flush_git_sync
                git_r = flush_git_sync(
                    replay=False,
                    nav=self.bot_nav,
                    pnl_pct=pnl_pct,
                    report_path=str(report_path or ""),
                )
                log.info(f"📤 Live git shutdown complete — {git_r}")
            except Exception as exc:
                log.error(f"Shutdown git sync failed: {exc}")
                try:
                    push_daily_summary(self.bot_nav, self.account_equity)
                except Exception:
                    pass

        try:
            if os.getenv("REPLAY_LIVE", "").lower() not in ("1", "true", "yes"):
                cleanup_local_workspace(aggressive=True)
            else:
                log.debug("Replay shutdown — skipping aggressive cleanup (teardown flush handles learning)")
        except Exception as exc:
            log.debug(f"Local cleanup: {exc}")

        self._stop_all_target_streams()

        if self.autopilot:
            try:
                self.autopilot.stop()
            except Exception:
                pass
        if getattr(self, "_telegram_listener", None):
            try:
                self._telegram_listener.stop()
            except Exception:
                pass
        self.conn.disconnect()
        try:
            from core.shutdown_control import clear_shutdown_request, remove_pid_file
            clear_shutdown_request()
            remove_pid_file()
        except Exception:
            pass
        log.info("HANOON stopped.")
