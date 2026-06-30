#!/usr/bin/env python3
"""
core/replay_scalper_runner.py — Full ScalperRunner on historical CSV (identical logic).

Uses the real ScalperRunner loop, council, PPO, multi-ticker lock/rotate.
Only differences: data from ReplayMarketHub, orders via shadow sim (no IB).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import Notifier, log
from core.replay_clock import activate, deactivate
from core.replay_connector import ReplayConnector
from core.replay_data_manager import ReplayDataManager
from core.replay_fill_simulator import ReplayFillSimulator
from core.replay_market_hub import ReplayMarketHub, list_intraday_tickers
from core.scalper_runner import ScalperRunner


class ReplayScalperRunner(ScalperRunner):
    """ScalperRunner with CSV fake-live feeds — same entry/exit/council/PPO path."""

    def __init__(
        self,
        connector: ReplayConnector,
        cfg: BotConfig,
        notifier: Notifier,
        hub: ReplayMarketHub,
    ):
        self.replay_hub = hub
        self._orig_get_universe = None
        self._replay_fills: Optional[ReplayFillSimulator] = None
        self._replay_meta: Dict[str, Dict[str, Any]] = {}
        self._replay_session_start = time.time()
        self._replay_session_cap_logged = False
        super().__init__(connector, cfg, notifier)

    def _setup_replay_mode(self) -> None:
        os.environ["REPLAY_LIVE"] = "true"
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        # Stale stop.request from live HANOON must not kill replay mid-session
        try:
            from core.shutdown_control import clear_shutdown_request, shutdown_requested
            if shutdown_requested():
                log.warning("Clearing stale shutdown.request before replay session")
            clear_shutdown_request()
        except Exception:
            pass
        # Virtual clock drives session — do not suspend on wall-clock off-hours
        self.cfg.OFF_HOURS_SUSPEND_MARKET_DATA = False
        self.cfg.STARTUP_CURATED_WHEN_NOT_TRADABLE = False
        self.cfg.SCAN_DEFER_IB_ON_STARTUP = True
        self.cfg.USE_LIVE_IB_SCANNER = False
        # Council + full AI like live (market closed on wall clock is fine)
        if os.getenv("COUNCIL_ENABLED", "true").lower() in ("1", "true", "yes"):
            self.cfg.COUNCIL_ENABLED = True
        # Always shadow — never send real IB orders during replay
        self.cfg.SHADOW_ON_PAPER = True  # block_broker() bypasses shadow when False on paper
        self.cfg.PAPER_TRADING = True
        self.shadow_circuit.in_shadow = True
        self.shadow_circuit._save()
        # Reduce replay-only noise / blocking work
        self.cfg.SCAN_RUN_DEFERRED_IB = False
        self.cfg.DYNAMIC_AI_NOTIFICATIONS = False
        self.cfg.DAILY_IB_LEARNING_ENABLED = False
        self.cfg.TELEGRAM_LISTEN_ENABLED = False
        replay_train = os.getenv("REPLAY_TRAINING_ENABLED", "true").lower() in (
            "1", "true", "yes",
        )
        self.cfg.INCREMENTAL_TRAINING_ENABLED = replay_train and os.getenv(
            "INCREMENTAL_TRAINING_ENABLED", "true",
        ).lower() in ("1", "true", "yes")
        self.cfg.OFF_HOURS_HEAVY_TRAINING = False
        self._deferred_ib_scan = False
        # Replay model copy — never overwrite live ppo_trader.zip
        replay_model = os.getenv("REPLAY_MODEL_PATH", "").strip()
        if replay_model:
            self.cfg.MODEL_PATH = replay_model
        try:
            from core.ppo_entry_learning import set_ppo_model
            if getattr(self, "model", None) is not None:
                set_ppo_model(self.model)
        except Exception:
            pass
        # Looser council gates for replay learning volume (live unchanged)
        if os.getenv("REPLAY_RELAX_COUNCIL", "true").lower() in ("1", "true", "yes"):
            self.cfg.MIN_PROFIT_PROBABILITY = float(
                os.getenv("REPLAY_MIN_PROFIT_PROB", "0.45"),
            )
            self.cfg.CAPITAL_MIN_PROFIT_PROBABILITY = float(
                os.getenv("REPLAY_CAPITAL_MIN_PROFIT_PROB", "0.45"),
            )
            self.cfg.CAPITAL_DISCIPLINE = False
            os.environ["CAPITAL_DISCIPLINE"] = "false"
        self.cfg.PPO_TEACHER_ENABLED = os.getenv(
            "PPO_TEACHER_ENABLED", "true",
        ).lower() in ("1", "true", "yes")
        try:
            from core.brain_maturity import apply_maturity_to_config, log_maturity_banner
            apply_maturity_to_config(self.cfg)
            log_maturity_banner(self.cfg)
        except Exception as exc:
            log.debug(f"Brain maturity init: {exc}")
        try:
            from core.halim_runtime import init_halim_runtime
            from core.halim_identity import apply_halim_native_mode
            apply_halim_native_mode(self.cfg)
            self._halim_runtime = init_halim_runtime(self.cfg)
            if self._halim_runtime:
                self._halim_runtime.attach_runner(self)
        except Exception as exc:
            log.debug(f"Halim runtime init: {exc}")
            self._halim_runtime = None
        try:
            from core.replay_training import log_ib_farm_banner
            log_ib_farm_banner(self.cfg)
        except Exception:
            pass
        try:
            from core.halim_developer import enable_halim_developer_mode
            enable_halim_developer_mode(self.cfg)
        except Exception as exc:
            log.debug(f"Halim developer mode: {exc}")
        try:
            from core.trading_copilot import reset_copilot_for_replay
            reset_copilot_for_replay(self.cfg)
        except Exception as exc:
            log.debug(f"Copilot replay reset: {exc}")
        try:
            from core.shutdown_control import write_pid
            os.environ.setdefault("HANOON_PID_FILE", "logs/replay.pid")
            write_pid()
            log.info(f"Replay PID → {os.environ['HANOON_PID_FILE']}")
        except Exception as exc:
            log.debug(f"Replay PID file: {exc}")
        self._replay_fills = ReplayFillSimulator()
        log.info(
            f"🎬 REPLAY SCALPER — {len(self.replay_hub.tickers)} tickers | "
            f"council={'on' if getattr(self.cfg, 'COUNCIL_ENABLED', False) else 'off'} | "
            f"fills=stochastic sim | IB orders BLOCKED"
        )
        import core.pilot_mode as pilot_mode
        tickers = self.replay_hub.tickers
        self._orig_get_universe = pilot_mode.get_live_scan_universe

        def _replay_universe(scanner, connector, cfg, **kwargs):
            return list(tickers), "replay_intraday"

        pilot_mode.get_live_scan_universe = _replay_universe  # type: ignore

    def _teardown_replay_mode(self) -> None:
        if self._replay_fills is not None:
            st = self._replay_fills.stats()
            log.info(
                f"Replay fill sim stats: {st['fills']} fills, "
                f"{st['partials']} partials, {st['rejects']} rejects, "
                f"{st['pending']} pending"
            )
        if self._orig_get_universe is not None:
            import core.pilot_mode as pilot_mode
            pilot_mode.get_live_scan_universe = self._orig_get_universe

        baseline = float(self.cfg.INITIAL_CASH)
        pnl_pct = (
            ((self.bot_nav - baseline) / baseline) * 100 if baseline else 0.0
        )

        try:
            from core.replay_training import run_replay_training_cycle, replay_training_enabled
            if replay_training_enabled(self.cfg):
                run_replay_training_cycle(
                    self.cfg, runner=self, trigger="replay_teardown",
                )
        except Exception as exc:
            log.debug(f"Replay teardown training: {exc}")

        try:
            from core.replay_consumption import finalize_replay_session
            fin = finalize_replay_session(
                self.replay_hub,
                trigger="replay_teardown",
                verbose=True,
            )
            unc = (fin.get("steps") or {}).get("unconsumed") or {}
            if int(unc.get("unconsumed_bars", 0)) < 20:
                log.info("🗑  Replay farm fully consumed — all CSV training data removed")
            elif (fin.get("steps") or {}).get("purge", {}).get("files_deleted"):
                purged = fin["steps"]["purge"]
                log.info(
                    f"🗑  Replay CSV farm purged ({purged.get('files_deleted')} files) — "
                    "re-download next session"
                )
        except Exception as exc:
            log.debug(f"Replay consumption finalize: {exc}")

        try:
            from core.graceful_shutdown import run_graceful_shutdown
            log.info("🛑 Replay teardown — Halim + co-evolution + evolution + git…")
            summary = run_graceful_shutdown(
                self.cfg,
                mode="replay",
                nav=self.bot_nav,
                pnl_pct=pnl_pct,
                model=self.model,
                push_git=False,
                trigger="replay_teardown",
                skip_replay_consumption=True,
            )
            git_step = (summary.get("steps") or {}).get("git") or {}
            log.info(f"📤 Replay flush complete — git={git_step.get('ok', '?')}")
        except Exception as exc:
            log.warning(f"Replay graceful teardown: {exc}")
            try:
                from core.git_sync import flush_replay_session_git_sync
                flush_replay_session_git_sync(self.bot_nav, pnl_pct)
            except Exception as exc3:
                log.warning(f"Replay git flush fallback: {exc3}")

        try:
            from core.shutdown_control import clear_shutdown_request, remove_pid_file
            clear_shutdown_request()
            remove_pid_file()
        except Exception:
            pass

    def _prefetch_one_ticker_bars(self, ticker: str, quiet: bool = True):
        need = self._min_bars_for(ticker)
        cached = self._scan_data_cache.get(ticker)
        if cached is not None and len(cached) >= need:
            return cached
        if getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
            live_df = self._bars_from_stream(ticker, need)
            if live_df is not None:
                return live_df
        df = self.replay_hub.warmup_bars(ticker, need)
        if df is not None and len(df) >= max(6, need // 2):
            self._scan_data_cache[ticker] = df
            return df
        return cached

    def _start_target_stream(
        self, ticker: str, quiet: bool = False, stream_mode: str = "tick",
    ):
        if ticker in self._target_monitors:
            return
        try:
            from core.config import BotConfig as BC
            cfg = BC(TICKER=ticker)
            dm = ReplayDataManager(self.conn, cfg, self.replay_hub)
            cached = self._scan_data_cache.get(ticker)
            n_cached = len(cached) if cached is not None else 0
            if cached is not None and n_cached > 0:
                dm.seed_buffer_from_dataframe(cached, n_bars=min(60, n_cached))
            dm.start_tick_stream(realtime_only=(stream_mode == "realtime"), quiet=quiet)
            from core.fast_execution import tick_spike_monitor_enabled
            if tick_spike_monitor_enabled(self.cfg):
                sym = ticker
                dm.on_tick(lambda px, ts, t=sym: self._on_locked_stream_tick(t, px, ts))
            self._target_monitors[ticker] = dm
            self._stream_modes[ticker] = "replay"
            self._target_last_bar_count[ticker] = n_cached
            warm = "warming" if n_cached < self._min_bars_for(ticker) else f"{n_cached} bars"
            msg = f"  📡 REPLAY STREAM {ticker} ({warm})"
            (log.debug if quiet else log.info)(msg)
        except Exception as exc:
            log.warning(f"  Replay stream start failed for {ticker}: {exc}")

    def _maybe_resume_ib_from_shadow(self) -> None:
        """Replay always stays in shadow — never route to IB."""
        return

    def _refresh_account_balance(self):
        """Simulated paper account — no IB."""
        cash = float(getattr(self.cfg, "INITIAL_CASH", 1000.0))
        self.account_equity = cash + self.shares * self._latest_price()
        self.available_cash = cash
        self.cash = cash
        if self._ib_starting_balance is None:
            self._ib_starting_balance = cash
            self.bot_cash = cash
            self.bot_nav = cash
        else:
            self.bot_nav = self.bot_cash + self.shares * self._latest_price()

    def _ensure_locked_streams(self, quiet: bool = True):
        """Replay: keep stream wiring, demote per-loop stream banner to debug."""
        import core.notify as notify
        orig_info = notify.log.info

        def _info(msg, *args, **kwargs):
            if isinstance(msg, str) and msg.startswith("📡 Streams:"):
                notify.log.debug(msg, *args, **kwargs)
            else:
                orig_info(msg, *args, **kwargs)

        notify.log.info = _info
        try:
            super()._ensure_locked_streams(quiet=True)
        finally:
            notify.log.info = orig_info

    def _train_off_hours(self):
        """Replay off-hours — train from IB CSV farm + replay_live buffer (no live IB HMDS)."""
        try:
            from core.replay_training import run_replay_training_cycle, replay_training_enabled
            if replay_training_enabled(self.cfg):
                run_replay_training_cycle(
                    self.cfg, runner=self, trigger="replay_off_hours",
                )
            else:
                log.debug("Replay: off-hours training disabled (REPLAY_TRAINING_ENABLED=false)")
        except Exception as exc:
            log.debug(f"Replay off-hours training: {exc}")

    def _scan_and_rank(self, startup: bool = False, skip_ib_scanner: bool = False):
        """Lock entire replay universe — same multi-ticker pool as live curated mode."""
        import time as _time
        t0 = _time.perf_counter()
        results: List[Dict] = []
        for idx, ticker in enumerate(self.replay_hub.tickers):
            df = self.replay_hub._data.get(ticker)
            if df is None or df.empty:
                continue
            ts = self.replay_hub.current_time
            if ts is not None:
                sub = self.replay_hub.history_before(ticker, ts)
            else:
                sub = df
            if sub is None or sub.empty:
                continue
            px = float(sub["close"].iloc[-1])
            vol = float(sub["volume"].tail(20).mean()) if len(sub) >= 20 else 1e6
            results.append({
                "ticker": ticker,
                "price": px,
                "volume": vol,
                "avg_volume": vol * 0.8,
                "rel_vol": 1.4,
                "total_score": max(40.0, 85.0 - idx * 0.5),
                "reasons": "replay_intraday",
            })
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        log.info(f"🔍 REPLAY SCAN: {len(results)} tickers from intraday CSV universe")
        self._commit_scan_lock(results, elapsed_ms, fast_lock=True)

    def _commit_scan_lock(self, results, elapsed_ms, fast_lock=False):
        ok = super()._commit_scan_lock(results, elapsed_ms, fast_lock=fast_lock)
        if ok:
            self.replay_hub.begin_feeding()
        return ok

    def _submit_ai_entry(self, ticker, df_fast, ai_dec, market_ctx, current_px):
        """Shadow-only entries during replay — before any IB broker calls."""
        if self.shadow_circuit.block_broker():
            return self._replay_shadow_entry(ticker, df_fast, ai_dec, market_ctx, current_px)
        return super()._submit_ai_entry(ticker, df_fast, ai_dec, market_ctx, current_px)

    def _replay_bar_for(self, ticker: str) -> Dict[str, float]:
        row = self.replay_hub.current_bar(ticker)
        if row is not None and self._replay_fills is not None:
            return self._replay_fills.bar_dict_from_row(row)
        df = self._scan_data_cache.get(ticker)
        if df is not None and len(df) > 0:
            last = df.iloc[-1]
            if self._replay_fills is not None:
                return self._replay_fills.bar_dict_from_row(last)
            return {
                "open": float(last["open"]),
                "high": float(last["high"]),
                "low": float(last["low"]),
                "close": float(last["close"]),
                "volume": float(last.get("volume", 0)),
            }
        px = self._live_price_for(ticker, 0.0)
        return {"open": px, "high": px, "low": px, "close": px, "volume": 0.0}

    def _apply_trade_close_learning(self, trade_rec: Dict[str, Any], ticker: str) -> None:
        trade_rec.setdefault("source", "replay_live")
        trade_rec.setdefault("entry_mode", trade_rec.get("entry_mode") or "replay_sim")
        import core.experience_buffer as exp_buf
        orig_append = exp_buf.append

        def _replay_append(row: Dict[str, Any]) -> None:
            if isinstance(row, dict) and row.get("source") == "live_trade":
                row = {**row, "source": "replay_live"}
            orig_append(row)

        exp_buf.append = _replay_append
        try:
            super()._apply_trade_close_learning(trade_rec, ticker)
        finally:
            exp_buf.append = orig_append
        # schedule_post_close_learning runs in parent _finalize_closed_trade — no duplicate here

    def _replay_book_entry_fill(
        self,
        ticker: str,
        plan,
        fill_px: float,
        shares: int,
        *,
        limit_px: float,
        slippage_pct: float,
        regime_label: str,
        ai_dec: Dict[str, Any],
        spike: float,
        partial: bool = False,
    ) -> str:
        """Mirror live _open_position_from_fill learning hooks without IB."""
        from core.bracket_validator import adapt_bracket_to_fill
        from core.experience_buffer import append as buffer_append
        from core.fill_tracker import append_fill_ledger
        from core.git_sync import push_trade
        from core.pilot_mode import snapshot_features
        from core.reward_shaping import reward_from_bracket_reject, reward_from_trade
        from core.risk import TradePlan
        from core.trade_telemetry import log_entry_execution, log_post_fill_adapt

        planned_entry = float(plan.entry_price)
        old_stop = float(plan.initial_stop_price)
        old_target = float(plan.take_profit_price)
        adapt = adapt_bracket_to_fill(
            self.cfg, planned_entry, fill_px,
            old_stop, old_target, shares, float(plan.atr_at_entry or 0),
        )
        log_post_fill_adapt(
            ticker=ticker,
            planned_entry=planned_entry,
            fill_px=fill_px,
            old_stop=old_stop,
            old_target=old_target,
            new_stop=adapt.stop,
            new_target=adapt.target,
            shares=shares,
            slippage_pct=adapt.slippage_pct,
            adjusted=adapt.adjusted,
            aborted=adapt.abort,
            reason=adapt.reason,
        )
        if adapt.abort:
            log.warning(
                f"  🛑 REPLAY SLIPPAGE ABORT {ticker}: sim flatten {shares}sh "
                f"@ ${fill_px:.4f} | {adapt.reason}"
            )
            try:
                buffer_append({
                    "source": "replay_slippage_abort",
                    "ticker": ticker,
                    "action": "ABORT",
                    "reward": reward_from_bracket_reject(
                        self.cfg,
                        spike_ratio=spike,
                        inverted=fill_px >= old_target,
                    ),
                    "reason": adapt.reason[:200],
                    "fill_px": fill_px,
                    "planned_entry": planned_entry,
                    "slippage_pct": adapt.slippage_pct,
                })
            except Exception:
                pass
            self._clear_pending_entry(ticker, cooldown_sec=60.0)
            self._replay_fills.cancel_pending(ticker)
            return "aborted_slippage"

        if adapt.adjusted or adapt.ok:
            plan = TradePlan(
                side="LONG",
                entry_price=fill_px,
                shares=float(shares),
                initial_stop_price=adapt.stop,
                take_profit_price=adapt.target,
                risk_usd=adapt.risk_usd or plan.risk_usd,
                atr_at_entry=plan.atr_at_entry,
            )

        self._clear_pending_entry(ticker)
        opened_at = time.time()
        cost = shares * fill_px * (1 + self.cfg.TRANSACTION_COST_PCT)
        self.bot_cash -= cost
        self._last_entry_telemetry = {
            "limit_px": limit_px,
            "entry_mode": "replay_sim",
            "regime": regime_label,
            "council": ai_dec,
            "slippage_pct": slippage_pct,
            "atr": float(plan.atr_at_entry or 0),
            "partial": partial,
        }
        slot = {
            "shares": float(shares),
            "entry_price": fill_px,
            "entry_fill_px": fill_px,
            "limit_px": limit_px,
            "entry_slippage_pct": round(slippage_pct, 6),
            "entry_mode": "replay_sim",
            "regime": regime_label,
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "peak": fill_px,
            "hard_floor": plan.initial_stop_price,
            "opened_at": opened_at,
            "prev_shares": float(shares),
            "last_pulse_price": fill_px,
            "last_price_change_at": opened_at,
            "last_price_snapshot_at": 0.0,
            "last_pulse_fingerprint": "",
            "last_position_pulse": 0.0,
            "last_ai_position_manage": 0.0,
            "last_stagnation_decision": {},
            "vision_read": "",
        }
        self._position_slots[ticker] = slot
        self._load_position_context(ticker)
        self._recalc_bot_nav()
        self._ensure_position_stream(ticker)
        self._risk_plans[ticker] = plan
        self.risk.open_position(plan)
        self._reset_profit_hunt_state()
        self._active_stream_ticker = ticker
        self.trades_today += 1
        self.current_ticker = ticker
        log.info(
            f"🎯 REPLAY ENTRY: {shares}x {ticker} @ ${fill_px:.4f} | "
            f"Stop ${plan.initial_stop_price:.4f} | TP ${plan.take_profit_price:.4f} | "
            f"slip {slippage_pct:+.3%} | Deployed ${cost:,.0f}"
            + (" PARTIAL" if partial else "")
        )
        push_trade(ticker, "BUY", fill_px, shares)
        append_fill_ledger({
            "event": "entry_fill",
            "source": "replay_live",
            "ticker": ticker,
            "entry_fill": round(fill_px, 4),
            "limit_px": limit_px,
            "entry_slippage_pct": round(slippage_pct, 6),
            "shares": shares,
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "entry_mode": "replay_sim",
            "regime": regime_label,
            "partial": partial,
        })
        log_entry_execution(
            ticker=ticker,
            limit_px=limit_px,
            fill_px=fill_px,
            entry_mode="replay_sim",
            shares=shares,
            stop=plan.initial_stop_price,
            target=plan.take_profit_price,
            regime=regime_label,
            spike_ratio=spike,
            council_decision=ai_dec,
            shadow=False,
        )
        try:
            buffer_append({
                "source": "replay_live",
                "ticker": ticker,
                "action": "BUY",
                "entry_price": fill_px,
                "shares": shares,
                "stop": plan.initial_stop_price,
                "target": plan.take_profit_price,
                "reward": reward_from_trade(
                    0.0, self.cfg,
                    slippage_pct=slippage_pct,
                    spike_ratio=spike,
                ),
                "regime": regime_label,
                "confidence": getattr(self, "_last_ai_confidence", 0.5),
                "features": snapshot_features(self._feature_buffer, self.cfg),
                "spike_ratio": spike,
                "scan_score": float(getattr(self, "_last_scan_score", 0)),
                "slippage_pct": round(slippage_pct, 6),
                "partial_fill": partial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        try:
            from core.ppo_entry_learning import on_entry_fill

            features = snapshot_features(self._feature_buffer, self.cfg)
            obs = None
            if len(self._feature_buffer) >= self.cfg.WINDOW_SIZE:
                window = np.array(
                    list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:],
                    dtype=np.float32,
                ).flatten()
                total = self.bot_cash + self.shares * fill_px
                c_rat = self.bot_cash / (total + 1e-9)
                p_rat = (self.shares * fill_px) / (total + 1e-9)
                obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
            entry_id = on_entry_fill(
                self.cfg,
                ticker=ticker,
                entry_price=fill_px,
                shares=shares,
                features=features,
                ai_commander=self.ai_commander,
                council_decision=ai_dec,
                spike_ratio=spike,
                scan_score=float(getattr(self, "_last_scan_score", 0)),
                slippage_pct=slippage_pct,
                regime=regime_label,
                model=self.model,
                obs=obs,
            )
            if ticker in self._position_slots:
                self._position_slots[ticker]["ppo_entry_id"] = entry_id
        except Exception as exc:
            log.debug(f"Replay PPO entry learning: {exc}")

        self.shadow_circuit.open_shadow_trade(
            ticker, fill_px, plan.initial_stop_price,
            plan.take_profit_price, shares, regime=regime_label,
        )
        self._replay_meta[ticker] = {
            "quote_entry": limit_px,
            "entry_slippage_pct": slippage_pct,
            "regime": regime_label,
            "partial": partial,
        }
        return "entered"

    def _service_replay_pending_entries(self) -> None:
        if not self._replay_fills:
            return
        for ticker in list(self.replay_hub.tickers):
            bar = self._replay_bar_for(ticker)
            fill = self._replay_fills.advance_pending(ticker, bar)
            if not fill or not fill.ok:
                continue
            meta = fill.meta or {}
            plan = meta.get("plan")
            ai_dec = meta.get("ai_dec") or {}
            regime = meta.get("regime", "")
            spike = float(meta.get("spike", 1.0))
            if plan is None:
                continue
            self._replay_book_entry_fill(
                ticker, plan, fill.fill_price, fill.filled_shares,
                limit_px=fill.quote_price,
                slippage_pct=fill.slippage_pct,
                regime_label=regime,
                ai_dec=ai_dec,
                spike=spike,
                partial=fill.partial,
            )

    def _service_shadow_positions(self) -> None:
        """Stochastic stop/target fills + full P&L learning (replaces instant shadow)."""
        try:
            from core.trading_copilot import maybe_refresh_copilot
            maybe_refresh_copilot(self)
        except Exception:
            pass
        self._service_replay_pending_entries()
        if not self.shadow_circuit.shadow_open:
            return
        sim = self._replay_fills
        if sim is None:
            super()._service_shadow_positions()
            return

        for ticker in list(self.shadow_circuit.shadow_open.keys()):
            pos = self.shadow_circuit.shadow_open.get(ticker)
            if not pos:
                continue
            bar = self._replay_bar_for(ticker)
            reason, quote_exit = sim.resolve_intrabar_trigger(
                bar, pos.stop, pos.target,
            )
            if not reason or quote_exit is None:
                continue

            exit_fill = sim.simulate_exit(
                ticker, quote_exit, pos.shares, bar, exit_kind=reason,
            )
            if not exit_fill.ok:
                continue

            fill_px = exit_fill.fill_price
            entry_fill = float(pos.entry)
            shares = float(pos.shares)
            pnl = (fill_px - entry_fill) * shares
            pnl -= shares * entry_fill * self.cfg.TRANSACTION_COST_PCT
            pnl -= shares * fill_px * self.cfg.TRANSACTION_COST_PCT
            pnl = round(pnl, 2)
            pnl_pct = pnl / (entry_fill * shares + 1e-9)
            meta = self._replay_meta.pop(ticker, {})
            entry_slip = float(meta.get("entry_slippage_pct", 0))
            hold_sec = time.time() - float(pos.opened_at)
            slot = self._position_slots.get(ticker, {})

            rec = {
                "source": "replay_live",
                "ticker": ticker,
                "entry": entry_fill,
                "entry_fill": entry_fill,
                "exit": fill_px,
                "exit_fill": fill_px,
                "quote_entry": float(meta.get("quote_entry", entry_fill)),
                "quote_exit": quote_exit,
                "shares": shares,
                "pnl_usd": pnl,
                "pnl_pct": pnl_pct,
                "result": "win" if pnl > 0 else "loss",
                "reason": reason,
                "exit_reason": reason,
                "regime": pos.regime or meta.get("regime", ""),
                "hold_sec": hold_sec,
                "entry_slippage_pct": entry_slip,
                "exit_slippage_pct": exit_fill.slippage_pct,
                "stop": pos.stop,
                "target": pos.target,
                "entry_mode": "replay_sim",
                "limit_px": slot.get("limit_px"),
                "peak_px": float(slot.get("peak", entry_fill)),
            }

            proceeds = shares * fill_px * (1 - self.cfg.TRANSACTION_COST_PCT)
            self.bot_cash += proceeds
            self._recalc_bot_nav()

            if ticker in self.shadow_circuit.shadow_open:
                self.shadow_circuit.shadow_closed.append({
                    "ticker": ticker,
                    "entry": entry_fill,
                    "exit": fill_px,
                    "shares": int(shares),
                    "pnl_usd": pnl,
                    "result": rec["result"],
                    "reason": reason,
                    "regime": rec["regime"],
                    "hold_sec": hold_sec,
                    "closed_at": time.time(),
                })
                del self.shadow_circuit.shadow_open[ticker]
                self.shadow_circuit._save()

            log.info(
                f"  🌑 REPLAY exit {ticker}: ${pnl:+.2f} ({reason}) "
                f"entry=${entry_fill:.4f} exit=${fill_px:.4f} "
                f"slip_in={entry_slip:+.3%} slip_out={exit_fill.slippage_pct:+.3%}"
            )
            try:
                self._apply_trade_close_learning(rec, ticker)
            except Exception as exc:
                log.warning(f"Replay close learning failed {ticker}: {exc}")
            self._clear_closed_position_state(ticker)
            self.risk.close_position()
            self._reset_profit_hunt_state()

    def _replay_shadow_entry(self, ticker, df_fast, ai_dec, market_ctx, current_px):
        from core.risk import TradePlan, compute_atr

        sim = self._replay_fills
        if sim is None:
            return super()._submit_ai_entry(ticker, df_fast, ai_dec, market_ctx, current_px)

        current_px = self._live_price_for(ticker, current_px)
        shares = int(ai_dec.get("shares", 0) or 0)
        if shares < 1:
            return "waiting"

        from core.market_regime import resolve_regime

        spike = float(getattr(self, "_last_spike_ratio", 1.0))
        vol_ratio = float(market_ctx.get("recent_volume", 0)) / (
            float(market_ctx.get("avg_volume", 0)) + 1e-9
        )
        regime_result, regime_label = resolve_regime(
            self.regime_detector, df_fast,
            spike_ratio=spike, vol_ratio=vol_ratio,
        )
        plan = TradePlan(
            side="LONG",
            entry_price=current_px,
            shares=float(shares),
            initial_stop_price=float(ai_dec["stop"]),
            take_profit_price=float(ai_dec["target"]),
            risk_usd=float(ai_dec.get("risk_usd", 50.0)),
            atr_at_entry=compute_atr(df_fast, period=5),
        )
        bar = self._replay_bar_for(ticker)
        log.warning(
            f"  🌑 REPLAY SIM — submitting {ticker} {shares}sh @ ${current_px:.4f} "
            f"(stochastic fill)"
        )
        fill = sim.maybe_queue_entry(
            ticker, current_px, shares, bar,
            meta={
                "plan": plan,
                "ai_dec": ai_dec,
                "regime": regime_label,
                "spike": spike,
            },
        )
        if fill.status == "pending":
            self._clear_pending_entry(ticker, cooldown_sec=5.0)
            return "waiting"
        if fill.rejected or not fill.ok:
            self._clear_pending_entry(ticker, cooldown_sec=30.0)
            return "rejected"
        result = self._replay_book_entry_fill(
            ticker, plan, fill.fill_price, fill.filled_shares,
            limit_px=current_px,
            slippage_pct=fill.slippage_pct,
            regime_label=regime_label,
            ai_dec=ai_dec,
            spike=spike,
            partial=fill.partial,
        )
        self._clear_pending_entry(ticker, cooldown_sec=15.0)
        return result if result != "entered" else "shadow"

    def _maybe_merge_lock_from_scanner(self, now: float) -> bool:
        return False

    def _replay_session_cap_reached(self) -> bool:
        """Wall-clock session limit — stop gracefully; next start resumes unconsumed bars."""
        raw = os.getenv("REPLAY_SESSION_MAX_MINUTES", "0").strip()
        try:
            max_min = float(raw)
        except ValueError:
            max_min = 0.0
        if max_min <= 0:
            return False
        elapsed_min = (time.time() - getattr(self, "_replay_session_start", time.time())) / 60.0
        if elapsed_min < max_min:
            return False
        if not getattr(self, "_replay_session_cap_logged", False):
            walked = getattr(self.replay_hub, "steps_walked", self.replay_hub._idx)
            total = len(self.replay_hub._timeline)
            log.info(
                f"⏱ Replay session cap ({max_min:.0f} min wall clock) — "
                f"stopping at step {walked:,}/{total:,}. "
                f"Start again to resume (trained bars trimmed on stop)."
            )
            self._replay_session_cap_logged = True
        return True

    def _shutdown_abort(self) -> bool:
        if self.replay_hub.finished:
            if not getattr(self, "_replay_done_logged", False):
                log.info("Replay timeline complete — shutting down ScalperRunner gracefully")
                self._replay_done_logged = True
            return True
        if self._replay_session_cap_reached():
            return True
        if super()._shutdown_abort():
            log.info("🛑 Replay stop requested (shutdown.request or signal)")
            return True
        return False

    def run(self):
        self._setup_replay_mode()
        activate()
        try:
            super().run()
        finally:
            self.replay_hub.stop()
            self._teardown_replay_mode()
            deactivate()


def run_replay_scalper(cfg: BotConfig) -> None:
    """Launch full ScalperRunner replay session (multi-ticker, council, PPO)."""
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    replay_model = os.getenv("REPLAY_MODEL_PATH", "").strip()
    if replay_model:
        cfg.MODEL_PATH = replay_model
    root = os.getenv("REPLAY_DATA_DIR", "").strip()
    tickers_env = os.getenv("REPLAY_TICKERS", "").strip()
    tickers = [t.strip().upper() for t in tickers_env.split(",") if t.strip()] or None

    hub = ReplayMarketHub(cfg, tickers=tickers)
    if not hub.tickers:
        raise FileNotFoundError(
            "No replay tickers. Run: python scripts/download_ib_replay_data.py"
        )

    pace = "real-time" if hub.realtime_pace else f"{hub.dilation_ms}ms/step"
    est_steps = len(hub._timeline)
    log.info("=" * 70)
    log.info("  REPLAY SCALPER — identical to live HANOON (CSV fake-live)")
    log.info(f"  Universe:  {', '.join(hub.tickers)}")
    log.info(f"  Steps:     {est_steps:,} synchronized market timestamps")
    log.info(f"  Pace:      {pace}")
    log.info(f"  Council:   {os.getenv('COUNCIL_ENABLED', 'true')}")
    log.info(f"  Model:     {os.getenv('REPLAY_MODEL_PATH', cfg.MODEL_PATH)}")
    from core.learning_coordinator import learning_mode_label
    log.info(f"  Learning:  {learning_mode_label(cfg)}")
    if hub.realtime_pace and est_steps > 1000:
        log.info(
            "  Tip: full multi-ticker real-time takes days. "
            "Default train pace: REPLAY_REALTIME_PACE=false REPLAY_TIME_DILATION_MS=50"
        )
    log.info("=" * 70)

    notifier = Notifier(cfg)
    connector = ReplayConnector(cfg, notifier, hub)
    if not connector.connect():
        raise RuntimeError("Replay connector failed")

    runner = ReplayScalperRunner(connector, cfg, notifier, hub)
    connector.attach_hub(hub)
    try:
        runner.run()
    except KeyboardInterrupt:
        log.info("Replay scalper stopped by user.")
    finally:
        connector.disconnect()
