#!/usr/bin/env python3
"""Extracted from scalper_runner — scalper entry executor."""

from __future__ import annotations

from core.scalper_mixin_imports import *  # noqa: F403
from core.pilot_mode import get_ai_deploy_budget  # noqa: F401 — explicit for entry paths

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    pass


class ScalperEntryMixin:
    """Mixin — composed into ScalperRunner."""

    def _entry_parent_price(self, ticker: str, current_px: float) -> Optional[float]:
        """Deprecated — use _smart_entry_plan()."""
        bid, ask = self._get_bid_ask(ticker)
        limit_px, _ = self.broker.decide_smart_entry(current_px, bid, ask, 1, 0)
        return limit_px
    def _clamp_entry_shares(self, shares: int, price: float) -> int:
        max_shares = effective_max_shares_per_trade(self.cfg)
        shares = min(int(shares), max_shares)
        if getattr(self.cfg, "PAPER_TRADING", False):
            shares = min(shares, int(getattr(self.cfg, "PAPER_MAX_ENTRY_SHARES", 5000)))
        if price <= 0:
            return 0
        if not getattr(self.cfg, "USE_FIXED_DEPLOY_CAP", False):
            reserve_pct = effective_min_cash_reserve_pct(self.cfg)
            cash_cap = self._deployable_cash() * (1.0 - reserve_pct)
            cash_shares = int(cash_cap / price) if price > 0 else shares
            return max(1, min(shares, cash_shares))
        deploy_usd = min(
            get_deploy_usd(self.cfg, self.pilot),
            float(getattr(self.cfg, "MAX_TRADE_SIZE_USD", 1000.0)),
        )
        return max(1, min(shares, int(deploy_usd / price)))
    def _entry_price_mode(
        self,
        current_px: float,
        bid: float,
        ask: float,
        shares: int,
        avg_volume: float,
    ) -> Tuple[Optional[float], str]:
        """
        Paper RTH: MARKET by default. Extended hours: aggressive LIMIT only —
        IB paper parent MARKET orders stall in PreSubmitted outside RTH.
        """
        return entry_price_mode_for_session(
            self.cfg, self.broker, current_px, bid, ask, shares, avg_volume,
        )
    def _stuck_entry_limit_px(
        self,
        ticker: str,
        ref_px: float,
        shares: int,
    ) -> Tuple[float, str]:
        """Limit price for PreSubmitted recovery — never re-submit bare MARKET ext-hours."""
        bid, ask = self._get_bid_ask(ticker)
        live = self._live_price_for(ticker, ref_px)
        return stuck_entry_limit_px(self.cfg, self.broker, bid, ask, live, shares)
    def _ib_sync_enabled(self) -> bool:
        return require_ib_fill_sync(self.cfg)
    def _ib_position_shares(self, ticker: str) -> float:
        return ib_position_shares(self.ib, ticker)
    def _confirm_entry_fill_from_ib(
        self,
        ticker: str,
        st: Dict[str, Any],
        bracket: Any,
        shares: int,
        min_fill_ratio: float,
        quote_px: float,
    ) -> Tuple[float, float, bool, str]:
        return confirm_entry_fill_from_ib(
            self.ib,
            ticker=ticker,
            st=st,
            bracket=bracket,
            shares=shares,
            min_fill_ratio=min_fill_ratio,
            quote_px=quote_px,
            fill_cache=self._fill_cache(),
            ib_sync_enabled=self._ib_sync_enabled(),
        )
    def _clear_pending_entry(self, ticker: Optional[str] = None, cooldown_sec: float = 45.0):
        """Reset pending bracket state; optional per-ticker cooldown."""
        if ticker:
            self._entry_cooldown_until[ticker] = time.time() + cooldown_sec
            self._spike_skip_until[ticker] = time.time() + cooldown_sec
            self._pending_brackets_by_ticker.pop(ticker, None)
            self._entry_poll_states.pop(ticker, None)
            if self._pending_entry_ticker == ticker:
                self._pending_entry_ticker = (
                    next(iter(self._entry_poll_states), None)
                )
        else:
            self._pending_brackets_by_ticker.clear()
            self._entry_poll_states.clear()
            self._pending_entry_ticker = None
        if not self._entry_poll_states:
            self._pending_entry_ticker = None
            self._pending_entry_until = 0.0
    def _bracket_for_entry_fill(self, ticker: str) -> Optional[BracketHandle]:
        """Bracket for a specific pending/fresh fill — never another ticker's handle."""
        st = self._entry_poll_states.get(ticker) or {}
        if st.get("bracket"):
            return st["bracket"]
        return self._pending_brackets_by_ticker.get(ticker)
    def _attempt_scan_bootstrap_entry(self):
        """Enter on scanner-confirmed momentum right after lock (don't wait for a new tick spike)."""
        if not self._locked_targets:
            return
        if self._open_position_count() >= self._max_concurrent():
            return
        pick = self._locked_targets[0]
        if pick.ticker in self._held_tickers():
            return
        min_lock = effective_min_lock_score(self.cfg)
        if pick.rank_score < min_lock:
            return
        min_bars = self._min_bars_for(pick.ticker)
        df, live_px, _, forecast = self._resolve_live_bars(pick.ticker, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            return
        is_spike, spike_ratio = self._detect_volume_spike(df)
        vol_ratio = float(df["volume"].tail(3).mean()) / (float(df["volume"].tail(20).mean()) + 1e-9)
        if not is_spike and vol_ratio >= 1.15:
            is_spike, spike_ratio = True, vol_ratio
        is_spike, spike_ratio = apply_micro_spike_boost(
            is_spike, spike_ratio, forecast,
            cfg=self.cfg, scan_score=float(pick.rank_score),
        )
        if not is_spike:
            return
        self.top_pick = pick
        log.info(
            f"📊 SCAN MOMENTUM: {pick.ticker} score={pick.rank_score:.0f} vol={spike_ratio:.1f}x "
            f"micro={forecast.get('spike_likelihood', 0):.0%} pred→${(forecast.get('pred_1bar') or live_px):.2f}"
        )
        self._attempt_entry()
    def _attempt_hot_swap_entry(self):
        """Enter the best pre-scouted ticker immediately after an exit."""
        if self._open_position_count() >= self._max_concurrent():
            return
        if not getattr(self.cfg, "HOT_SWAP_ON_EXIT", True):
            return
        if self._pending_entry_ticker or self._entry_poll_states:
            return
        pick = self._next_best_pick or self.top_pick
        if not pick:
            return
        df = self._scan_data_cache.get(pick.ticker)
        if df is None or len(df) < 20:
            return
        px = float(df["close"].iloc[-1])
        if not only_uptrend(df.tail(60), px):
            return
        is_spike, vol = self._detect_volume_spike(df.tail(60))
        if not is_spike:
            vol = float(df["volume"].tail(3).mean()) / (float(df["volume"].tail(20).mean()) + 1e-9)
            if vol < 1.15:
                return
        self.top_pick = pick
        log.info(
            f"⚡ HOT SWAP: {pick.ticker} vol={vol:.1f}x score={pick.rank_score:.0f} "
            f"— entering right after exit"
        )
        self._attempt_entry()
    def _open_position_from_fill(
        self, ticker: str, shares: int, fill_px: float, plan: TradePlan,
    ) -> str:
        """Bookkeeping after IB confirms an entry fill."""
        from core.fill_tracker import _sane_fill_ratio, position_avg_cost

        planned_entry = float(plan.entry_price)
        fill_bracket = self._bracket_for_entry_fill(ticker)
        if not _sane_fill_ratio(fill_px, planned_entry):
            avg = position_avg_cost(self.ib, ticker)
            if avg > 0 and _sane_fill_ratio(avg, planned_entry):
                log.warning(
                    f"  🔧 Fill price corrected {ticker}: ${fill_px:.4f} → ${avg:.4f} "
                    f"(planned ${planned_entry:.4f})"
                )
                fill_px = avg
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
                f"  🛑 SLIPPAGE ABORT {ticker}: flattening {shares}sh @ ${fill_px:.4f} | "
                f"{adapt.reason}"
            )
            try:
                buffer_append({
                    "source": "fill_slippage_abort",
                    "ticker": ticker,
                    "action": "ABORT",
                    "reward": reward_from_bracket_reject(
                        self.cfg,
                        spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                        inverted=fill_px >= old_target,
                    ),
                    "reason": adapt.reason[:200],
                    "fill_px": fill_px,
                    "planned_entry": planned_entry,
                    "slippage_pct": adapt.slippage_pct,
                })
            except Exception:
                pass
            handle = fill_bracket
            try:
                self.broker.flatten_position(
                    int(shares), handle=handle, urgent=True, symbol=ticker,
                )
                self.ib.sleep(0.15)
            except Exception as exc:
                log.warning(f"  Flatten after slippage abort failed: {exc}")
            self.broker.cancel_open_orders_for_symbol(ticker)
            if self.bracket_handle is handle:
                self.bracket_handle = None
            self._clear_pending_entry(ticker, cooldown_sec=60.0)
            for task in ("entry_decision", "exit_decision", "position_manage"):
                self._ai_councils.pop(self._council_key(ticker, task), None)
            self._position_slots.pop(ticker, None)
            self._refresh_aggregate_position_state()
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
            handle = fill_bracket
            if handle and getattr(handle, "children_deferred", False):
                try:
                    self.broker.attach_bracket_children(
                        handle, adapt.stop, adapt.target, shares,
                    )
                except Exception as exc:
                    log.warning(
                        f"  Bracket attach failed {ticker}: {exc} — flattening"
                    )
                    try:
                        self.broker.flatten_position(
                            int(shares), handle=handle, urgent=True, symbol=ticker,
                        )
                        self.ib.sleep(0.15)
                    except Exception as flat_exc:
                        log.warning(f"  Flatten after bracket attach failed: {flat_exc}")
                    self.broker.cancel_open_orders_for_symbol(ticker)
                    self._clear_pending_entry(ticker, cooldown_sec=60.0)
                    return "aborted_slippage"
            elif handle and adapt.adjusted:
                try:
                    self.broker.update_stop_price(handle, adapt.stop)
                    self.broker.update_target_price(handle, adapt.target)
                    log.info(
                        f"  🔧 IB bracket updated for fill slip: "
                        f"stop ${adapt.stop:.4f} tp ${adapt.target:.4f}"
                    )
                except Exception as exc:
                    log.warning(f"  IB bracket re-anchor failed: {exc}")

        self._clear_pending_entry(ticker)
        opened_at = time.time()
        tel = getattr(self, "_last_entry_telemetry", {}) or {}
        limit_px = tel.get("limit_px")
        parent_trade = None
        if fill_bracket and fill_bracket.parent_trade:
            parent_trade = fill_bracket.parent_trade
        entry_fill = resolve_entry_fill(
            self.ib, symbol=ticker, parent_trade=parent_trade, quote_px=fill_px,
            max_wait=0.0, cache=self._fill_cache(),
        )
        if entry_fill > 0 and _sane_fill_ratio(entry_fill, planned_entry):
            fill_px = entry_fill
        cost = shares * fill_px * (1 + self.cfg.TRANSACTION_COST_PCT)
        self.bot_cash -= cost
        slippage_pct = 0.0
        if limit_px and float(limit_px) > 0:
            slippage_pct = (fill_px - float(limit_px)) / float(limit_px)
        vision_read = ""
        if self.ai_commander:
            try:
                vision_read = self.ai_commander.chart_read_for(
                    ticker, fill_px,
                    float(getattr(self, "_last_spike_ratio", 1.0)),
                    float(getattr(self, "_last_scan_score", 0.0)),
                )
            except Exception:
                pass
        slot = {
            "shares": float(shares),
            "session_shares": float(shares),
            "entry_price": fill_px,
            "entry_fill_px": fill_px,
            "ib_fill_confirmed": True,
            "limit_px": float(limit_px) if limit_px else None,
            "entry_slippage_pct": round(slippage_pct, 6),
            "entry_mode": str(tel.get("entry_mode", "market")),
            "regime": str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "risk_usd": float(plan.risk_usd or 0),
            "atr_at_entry": float(plan.atr_at_entry or 0),
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
            "vision_read": vision_read[:800],
            "horizon": "scalp",
            "capital_phase": "",
        }
        try:
            from core.capital_phase import capital_phase
            slot["capital_phase"] = capital_phase(self.cfg, self)
        except Exception:
            pass
        lot_meta = self._pending_lottery_meta.pop(ticker.upper(), {})
        if lot_meta.get("lottery_bank"):
            slot.update({
                k: lot_meta[k] for k in (
                    "lottery_bank", "lottery_tier", "lottery_conviction", "lottery_reason",
                ) if k in lot_meta
            })
        self._position_slots[ticker] = slot
        try:
            from core.war_account import record_entry, war_ledger_applies
            if war_ledger_applies(self.cfg):
                record_entry(
                    self.cfg,
                    ticker=ticker,
                    shares=int(shares),
                    ib_fill=float(fill_px),
                    quote=float(fill_px),
                    pipeline=str(getattr(self, "_last_entry_pipeline", "")),
                    spread_pct=abs(slippage_pct),
                )
        except Exception as exc:
            log.debug(f"War account entry: {exc}")
        if lot_meta.get("lottery_bank"):
            try:
                from core.lottery_bank import notify_lottery_event, record_entry
                row = record_entry(
                    self.cfg,
                    ticker=ticker,
                    shares=float(shares),
                    fill_px=float(fill_px),
                    meta=lot_meta,
                )
                notify_lottery_event(self.notifier, self.cfg, "lottery_entry", row)
            except Exception as exc:
                log.debug(f"Lottery bank entry record: {exc}")
        if fill_bracket:
            self._bracket_by_ticker[ticker] = fill_bracket
        self._load_position_context(ticker)
        self._sync_bot_nav_from_ib()
        try:
            self._refresh_account_balance()
        except Exception:
            pass
        self._ensure_position_stream(ticker)
        self._risk_plans[ticker] = plan
        self.risk.open_position(plan)
        self._reset_profit_hunt_state()
        self._active_stream_ticker = ticker
        slot["last_ai_position_manage"] = 0.0
        slot["last_position_pulse"] = 0.0
        self._last_ai_position_manage = 0.0
        self._last_position_pulse = 0.0
        if self.risk.plan:
            self.risk.plan.peak_price = max(self.risk.plan.peak_price, fill_px)
        try:
            self._update_trailing_stops(fill_px)
        except Exception:
            pass
        log.info(f"  📡 POST-ENTRY: live monitor + trailing armed on {ticker}")
        if not hasattr(self, "_active_positions"):
            self._active_positions = []
        self._active_positions.append({
            "ticker": ticker,
            "entry_price": fill_px,
            "shares": shares,
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "entry_time": time.time(),
        })
        try:
            from core.capital_discipline import capital_discipline_enabled
            from core.smart_stack import count_hourly_filled_entry
            if capital_discipline_enabled(self.cfg) and count_hourly_filled_entry(self.cfg):
                self._entries_this_hour = getattr(self, "_entries_this_hour", 0) + 1
        except Exception:
            pass
        self.trades_today += 1
        self.current_ticker = ticker
        log.info(
            f"🎯 ENTRY: {shares}x {ticker} @ ${fill_px:.2f} | "
            f"Stop ${plan.initial_stop_price:.2f} | TP ${plan.take_profit_price:.2f} | "
            f"Deployed: ${cost:,.0f}"
        )
        entry_ctx = {
            "ticker": ticker, "shares": shares, "entry": fill_px,
            "stop": plan.initial_stop_price, "target": plan.take_profit_price,
            "pilot_level": self.pilot.state.level,
            "deployed": cost,
        }
        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, "trade_opened",
                self._notify_context(entry_ctx, event_type="trade_opened"),
                f"🎯 ENTRY {shares}x {ticker} @ ${fill_px:.2f} | "
                f"Stop ${plan.initial_stop_price:.2f} | TP ${plan.take_profit_price:.2f}",
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info(
                f"🎯 HANOON ENTRY\nTicker: {ticker}\nQty: {shares}\n"
                f"Entry: ${fill_px:.2f}\nStop: ${plan.initial_stop_price:.2f}\n"
                f"Target: ${plan.take_profit_price:.2f}\nDeployed: ${cost:,.0f}"
            )
        from core.git_sync_defer import should_defer_git_push
        if not should_defer_git_push("trade"):
            push_trade(ticker, "BUY", fill_px, shares)
        append_fill_ledger({
            "event": "entry_fill",
            "ticker": ticker,
            "entry_fill": round(fill_px, 4),
            "limit_px": float(limit_px) if limit_px else None,
            "entry_slippage_pct": round(slippage_pct, 6),
            "shares": shares,
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "entry_mode": str(tel.get("entry_mode", "market")),
            "regime": str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
        })
        snap_parsed = {}
        if self.ai_commander:
            snap = self.ai_commander.ollama_audit_snapshot(ticker)
            snap_parsed = snap.get("parsed") or {}
        log_entry_execution(
            ticker=ticker,
            limit_px=float(limit_px) if limit_px else None,
            fill_px=fill_px,
            entry_mode=str(tel.get("entry_mode", "market")),
            shares=shares,
            stop=plan.initial_stop_price,
            target=plan.take_profit_price,
            regime=str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
            spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
            council_decision=tel.get("council"),
            ollama_raw=str(tel.get("ollama_raw", "")),
            ollama_parsed=snap_parsed,
            shadow=False,
        )
        self._last_entry_telemetry["slippage_pct"] = slippage_pct
        self._last_entry_telemetry["atr"] = float(plan.atr_at_entry or 0)
        try:
            buffer_append({
                "source": "live_entry",
                "ticker": ticker,
                "action": "BUY",
                "entry_price": fill_px,
                "shares": shares,
                "stop": plan.initial_stop_price,
                "target": plan.take_profit_price,
                "reward": reward_from_trade(
                    0.0, self.cfg,
                    slippage_pct=slippage_pct,
                    spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                ),
                "regime": getattr(self, "_last_entry_regime", ""),
                "confidence": getattr(self, "_last_ai_confidence", 0.5),
                "features": snapshot_features(self._feature_buffer, self.cfg),
                "spike_ratio": float(getattr(self, "_last_spike_ratio", 1.0)),
                "scan_score": float(getattr(self, "_last_scan_score", 0)),
                "volume_ratio": float(
                    getattr(self, "_last_market_ctx", {}).get("recent_volume", 0)
                    / (getattr(self, "_last_market_ctx", {}).get("avg_volume", 1) + 1e-9)
                ),
                "slippage_pct": round(slippage_pct, 6),
                "cash_ratio": self.bot_cash / (self.bot_cash + self.shares * fill_px + 1e-9),
                "pos_ratio": (self.shares * fill_px) / (self.bot_cash + self.shares * fill_px + 1e-9),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        try:
            from core.ppo_entry_learning import on_entry_fill
            from core.pilot_mode import snapshot_features

            features = snapshot_features(self._feature_buffer, self.cfg)
            council = tel.get("council") or {}
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
                council_decision=council,
                spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                scan_score=float(getattr(self, "_last_scan_score", 0)),
                slippage_pct=slippage_pct,
                regime=str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
                model=self.model,
                obs=obs,
            )
            if ticker in self._position_slots:
                self._position_slots[ticker]["ppo_entry_id"] = entry_id
        except Exception as exc:
            log.debug(f"PPO entry learning: {exc}")
        if self.ai_commander:
            council = tel.get("council") or {}
            try:
                self.ai_commander.ring_post_fill_learning(
                    ticker,
                    fill_px,
                    float(getattr(self, "_last_spike_ratio", 1.0)),
                    float(getattr(self, "_last_scan_score", 0)),
                    council,
                    account=self._account_context_for_ai(),
                    market_ctx=getattr(self, "_last_market_ctx", None),
                    df=self._scan_data_cache.get(ticker),
                )
            except Exception as exc:
                log.debug(f"Post-fill distill ring: {exc}")
        return "entered"
    def _service_shadow_positions(self) -> None:
        """Mark shadow trades to stop/target while IB routing is blocked."""
        if not self.shadow_circuit.in_shadow or not self.shadow_circuit.shadow_open:
            return
        for ticker in list(self.shadow_circuit.shadow_open.keys()):
            df = self._scan_data_cache.get(ticker)
            if df is None or len(df) < 1:
                continue
            try:
                px = self._live_price_for(ticker, float(df["close"].iloc[-1]))
                rec = self.shadow_circuit.update_shadow_price(ticker, px)
                if not rec:
                    continue
                log_exit_postmortem(
                    ticker=ticker,
                    entry=float(rec.get("entry", 0)),
                    exit_px=float(rec.get("exit", px)),
                    shares=float(rec.get("shares", 0)),
                    pnl_usd=float(rec.get("pnl_usd", 0)),
                    pnl_pct=0.0,
                    result=str(rec.get("result", "")),
                    regime=str(rec.get("regime", "")),
                    hold_sec=float(rec.get("hold_sec", 0)),
                    exit_reason=str(rec.get("reason", "")),
                    shadow=True,
                )
                buffer_append({
                    "source": "shadow_trade",
                    "ticker": ticker,
                    "pnl_usd": rec.get("pnl_usd", 0),
                    "win": 1 if float(rec.get("pnl_usd", 0)) > 0 else 0,
                    "reward": reward_from_trade(float(rec.get("pnl_usd", 0)), self.cfg),
                    "regime": rec.get("regime", ""),
                })
            except Exception:
                pass
    def _service_pending_entry(self):
        """Non-blocking IB fill polls — one pass per pending ticker."""
        if not self._entry_poll_states:
            return
        for ticker in list(self._entry_poll_states.keys()):
            self._service_one_pending_entry(ticker)
    def _service_one_pending_entry(self, ticker: str):
        """Poll a single ticker's pending bracket fill."""
        st = self._entry_poll_states.get(ticker)
        bracket = (st or {}).get("bracket") or self._pending_brackets_by_ticker.get(ticker)
        if not st or not bracket:
            self._entry_poll_states.pop(ticker, None)
            return
        shares = int(st["shares"])
        plan: TradePlan = st["plan"]
        fill_px = float(st["fill_px"])
        min_fill_ratio = float(st["min_fill_ratio"])
        fail_cd = float(st["fail_cd"])
        self.ib.sleep(0.05)
        parent_trade = getattr(bracket, "parent_trade", None)
        parent_id = bracket.parent_order_id
        parent_status = (
            parent_trade.orderStatus.status
            if parent_trade and parent_trade.orderStatus else "Unknown"
        )
        ierr = self.conn.pop_order_error(parent_id)
        if ierr:
            st["last_ib_error"] = ierr
        if ierr and ierr.get("code") == 2161:
            log.warning(f"  IB 2161 regulatory cap on {ticker} — will retry smaller limit")
            self._observe_runtime(
                "ib_failure",
                ticker=ticker,
                reason=str((ierr or {}).get("message", ""))[:200],
                ib_code=2161,
                price_cap=(ierr or {}).get("price_cap"),
                parent_status=parent_status,
                market_state=get_market_state(self.cfg),
            )
        if parent_status in ("Cancelled", "Inactive", "ApiCancelled"):
            block_reason = parse_ib_order_block(ierr)
            if block_reason:
                self._entry_poll_states.pop(ticker, None)
                self._ai_skip_ticker_permanent(ticker, block_reason)
                return
            if (
                st["attempt"] == 0
                and getattr(self.cfg, "ENTRY_RETRY_ON_IB2161", True)
                and (ierr or {}).get("code") == 2161
            ):
                self.broker.cancel_open_orders_for_symbol(ticker)
                st["attempt"] = 1
                st["polls"] = 0
                cap = (ierr or {}).get("price_cap")
                retry_sh = max(1, shares // 2)
                st["shares"] = retry_sh
                plan = TradePlan(
                    side="LONG", entry_price=fill_px, shares=float(retry_sh),
                    initial_stop_price=plan.initial_stop_price,
                    take_profit_price=plan.take_profit_price,
                    risk_usd=plan.risk_usd,
                    atr_at_entry=plan.atr_at_entry,
                )
                st["plan"] = plan
                entry_px = cap if cap and cap > 0 else None
                new_bracket = self.broker.place_bracket_buy(
                    quantity=retry_sh, limit_or_market_price=entry_px,
                    stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                    symbol=ticker,
                )
                st["bracket"] = new_bracket
                self._pending_brackets_by_ticker[ticker] = new_bracket
                log.info(f"  🔄 IB2161 retry: {retry_sh} sh limit @ ${entry_px or fill_px:.4f}")
                return
            log.warning(f"Entry order rejected by IB ({parent_status}) — not opening position")
            self._observe_runtime(
                "order_canceled",
                ticker=ticker,
                reason=str((ierr or {}).get("message", parent_status)),
                ib_code=(ierr or {}).get("code"),
                parent_status=parent_status,
                market_state=get_market_state(self.cfg),
            )
            self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
            return
        filled_shares = 0.0
        fill_px = float(st["fill_px"])
        filled, avg_px, confirmed, fill_src = self._confirm_entry_fill_from_ib(
            ticker, st, bracket, shares, min_fill_ratio, fill_px,
        )
        if confirmed:
            filled_shares = filled
            if avg_px > 0:
                fill_px = avg_px
                st["fill_px"] = fill_px
            log.info(
                f"  ✅ IB entry confirmed {ticker}: {int(filled_shares)}sh "
                f"@ ${fill_px:.4f} ({fill_src})"
            )
        if confirmed and filled_shares >= shares * min_fill_ratio:
            self._open_position_from_fill(ticker, int(filled_shares), fill_px, plan)
            return
        if parent_status in ("PendingSubmit", "PreSubmitted"):
            since = st.get("order_stuck_since")
            if since is None:
                st["order_stuck_since"] = time.time()
                since = st["order_stuck_since"]
            max_stuck = float(getattr(self.cfg, "PENDING_SUBMIT_MAX_SEC", 4.0))
            max_stuck_retries = int(getattr(self.cfg, "ENTRY_STUCK_MAX_RETRIES", 2))
            stuck_retries = int(st.get("stuck_retries", 0))
            if (time.time() - since) >= max_stuck and stuck_retries < max_stuck_retries:
                st["stuck_retries"] = stuck_retries + 1
                use_limit = should_defer_bracket_children(self.cfg) or stuck_retries >= 1
                log.warning(
                    f"  ⚡ {ticker} stuck {parent_status} >{max_stuck:.0f}s — "
                    f"cancel + retry #{st['stuck_retries']} "
                    f"({'LIMIT' if use_limit else 'MARKET'})"
                )
                self.broker.cancel_open_orders_for_symbol(ticker)
                self.ib.sleep(0.3)
                retry_sh = int(shares)
                if getattr(self.cfg, "PAPER_TRADING", False):
                    retry_sh = min(
                        retry_sh,
                        int(getattr(self.cfg, "PAPER_MAX_ENTRY_SHARES", 5000)),
                    )
                try:
                    if use_limit:
                        entry_px, retry_mode = self._stuck_entry_limit_px(
                            ticker, fill_px, retry_sh,
                        )
                        mode_note = f"limit@{entry_px:.4f} ({retry_mode})"
                    else:
                        entry_px = None
                        mode_note = "MARKET"
                    new_bracket = self.broker.place_bracket_buy(
                        quantity=retry_sh,
                        limit_or_market_price=entry_px,
                        stop_price=plan.initial_stop_price,
                        target_price=plan.take_profit_price,
                        symbol=ticker,
                    )
                    st["bracket"] = new_bracket
                    self._pending_brackets_by_ticker[ticker] = new_bracket
                    st["shares"] = retry_sh
                    st["polls"] = 0
                    st["order_stuck_since"] = None
                    st["limit_px"] = entry_px
                    log.info(f"  🔄 Stuck-entry retry {ticker}: {mode_note}")
                except Exception as exc:
                    log.warning(f"  Stuck-entry retry failed for {ticker}: {exc}")
                    self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
                return
        else:
            st.pop("order_stuck_since", None)
        st["polls"] = int(st.get("polls", 0)) + 1
        polls = st["polls"]
        max_polls = int(st["max_polls"])
        now_ts = time.time()
        last_hb = float(st.get("last_heartbeat", 0))
        if polls == 1 or polls % 5 == 0 or (now_ts - last_hb) >= 3.0:
            st["last_heartbeat"] = now_ts
            live_px = self._live_price_for(ticker, fill_px)
            limit_px = st.get("limit_px")
            px_note = (
                f"limit ${float(limit_px):.4f}"
                if limit_px is not None and float(limit_px) > 0
                else "MARKET"
            )
            elapsed = now_ts - float(st.get("started_at", now_ts))
            log.info(
                f"  ⏳ PENDING ENTRY {ticker}: {px_note} | "
                f"market ${live_px:.4f} | poll {polls}/{max_polls} "
                f"({parent_status}) | {elapsed:.1f}s"
            )
        chase_pct = float(getattr(self.cfg, "ENTRY_LIMIT_CHASE_PCT", 0.006))
        if polls >= 5 and parent_trade and parent_trade.order:
            live_px = self._live_price_for(ticker, fill_px)
            limit_px = float(getattr(parent_trade.order, "lmtPrice", 0) or st.get("limit_px") or 0)
            if live_px > 0 and limit_px > 0 and live_px > limit_px * (1 + chase_pct):
                new_limit = round(live_px * (1 + chase_pct * 0.5), 4)
                try:
                    parent_trade.order.lmtPrice = new_limit
                    self.ib.placeOrder(parent_trade.contract, parent_trade.order)
                    st["limit_px"] = new_limit
                    log.info(
                        f"  🏃 CHASE LIMIT {ticker}: ${limit_px:.4f} → ${new_limit:.4f} "
                        f"(market ${live_px:.4f})"
                    )
                except Exception as exc:
                    log.debug(f"Limit chase failed: {exc}")
        if polls >= max_polls:
            if filled_shares >= 1:
                log.warning(
                    f"Partial fill {int(filled_shares)}/{shares} below "
                    f"{min_fill_ratio:.0%} — flattening and skipping entry"
                )
                self.broker.flatten_position(
                    int(filled_shares), handle=bracket, urgent=True, symbol=ticker,
                )
                self.ib.sleep(0.1)
            elif parent_status in ("Submitted", "PreSubmitted", "PendingSubmit"):
                log.info(f"Entry order timed out for {ticker} ({parent_status})")
                self._observe_runtime(
                    "order_timeout",
                    ticker=ticker,
                    reason=parent_status,
                    ib_code=(st.get("last_ib_error") or {}).get("code"),
                    parent_status=parent_status,
                    market_state=get_market_state(self.cfg),
                )
            else:
                log.info(f"Entry not filled for {ticker} (status={parent_status})")
            self.broker.cancel_open_orders_for_symbol(ticker)
            self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
    def _resolve_entry_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if ticker in self._contract_blacklist:
            self._ai_councils.pop(key, None)
            return
        if ticker in self._held_tickers():
            if self.ai_commander and deferred_learning_enabled(self.cfg):
                executed = {
                    "enter": True,
                    "pipeline": "ppo:executed_before_council",
                    "reason": "position already open",
                }
                self.ai_commander._deferred.schedule(
                    ticker=ticker,
                    task="entry_decision",
                    fingerprint=str(st.get("fingerprint", "")),
                    executed=executed,
                    ppo_signal=int(st.get("ppo_action", 0)),
                    ppo_conf=float(st.get("ppo_conf", 0.5)),
                    ppo_reason=str(st.get("ppo_reason", "")),
                    market_ctx=st.get("market_ctx") or {},
                )
            self._ai_councils.pop(key, None)
            return
        if self._open_position_count() >= self._max_concurrent():
            return
        if self._pending_entry_ticker and time.time() < self._pending_entry_until:
            return
        df_fast = self._scan_data_cache.get(ticker)
        min_bars = self._min_bars_for(ticker)
        if df_fast is None or len(df_fast) < min_bars:
            dm = self._target_monitors.get(ticker)
            if dm and should_spike_fast_entry(
                self.cfg,
                float(st.get("spike_ratio", 0) or 0),
                float(st.get("scan_score", 0) or 0),
            ):
                df_fast = coalesce_bars(
                    dm.get_fast_bar_dataframe(n=24) if dm else None,
                    dm.get_bar_dataframe() if dm else None,
                    min_len=3,
                )
            if df_fast is None or len(df_fast) < max(3, min_bars // 2):
                return
        current_px = self._live_price_for(ticker, float(df_fast["close"].iloc[-1]))
        st["current_px"] = current_px
        st["account"] = self._account_context_for_ai()
        micro_fc = st.get("micro_forecast") or self._last_micro_forecast.get(ticker, {})
        from core.entry_quality import assess_entry_quality
        st["account"]["entry_quality"] = assess_entry_quality(
            self.cfg, micro_fc,
            spike_ratio=float(st.get("spike_ratio", 1.0)),
            scan_score=float(st.get("scan_score", 0)),
            ppo_action=int(st.get("ppo_action", 0)),
            ppo_conf=float(st.get("ppo_conf", 0.5)),
            live_px=current_px,
        )
        st["micro_forecast"] = micro_fc
        ai_dec = self.ai_commander.poll_entry_council(st, df=df_fast)
        if ai_dec.get("pending"):
            return
        self._ai_councils.pop(key, None)
        pipeline = str(ai_dec.get("pipeline", ""))
        if "timeout" in pipeline:
            self._observe_runtime(
                "council_timeout",
                ticker=ticker,
                pipeline=pipeline,
                reason=(ai_dec.get("reason") or "")[:200],
                spike_ratio=float(st.get("spike_ratio", 0) or 0),
                scan_score=float(st.get("scan_score", 0) or 0),
                confidence=float(ai_dec.get("confidence", 0) or 0),
                market_state=get_market_state(self.cfg),
            )
        if not ai_dec.get("enter"):
            log.info(
                f"  🧠 COUNCIL skip {ticker}: {(ai_dec.get('reason') or '')[:80]} | {pipeline}"
            )
            if "timeout" in pipeline:
                self._spike_attempt_until[ticker] = 0.0
            return
        log.info(
            f"  🧠 COUNCIL enter {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
            f"conf={float(ai_dec.get('confidence', 0)):.0%} | {pipeline}"
        )
        self._last_ai_confidence = float(ai_dec.get("confidence", 0.5))
        self._submit_ai_entry(
            ticker, df_fast, ai_dec, st.get("market_ctx") or {}, current_px,
        )
    def _apply_war_sizing(
        self,
        ticker: str,
        decision: Dict[str, Any],
        entry_px: float,
    ) -> Dict[str, Any]:
        try:
            from core.ppo_deploy_tiers import apply_deploy_tier_to_decision, ppo_deploy_tiers_enabled
            if ppo_deploy_tiers_enabled(self.cfg):
                decision = apply_deploy_tier_to_decision(
                    self.cfg,
                    decision,
                    entry_px,
                    ppo_action=int(decision.get("ppo_action", 1) or 1),
                    ppo_conf=float(decision.get("ppo_conf", decision.get("confidence", 0.5)) or 0.5),
                    spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0) or 1.0),
                    scan_score=float(getattr(self, "_last_scan_score", 0) or 0),
                )
        except Exception as exc:
            log.debug(f"PPO deploy tier: {exc}")
        try:
            from core.war_account import rescale_decision_for_war, war_ledger_applies
            if war_ledger_applies(self.cfg):
                return rescale_decision_for_war(
                    self.cfg, decision, entry_px, ticker=ticker,
                )
        except Exception as exc:
            log.debug(f"War sizing: {exc}")
        return decision
    def _apply_lottery_bank_sizing(
        self,
        ticker: str,
        decision: Dict[str, Any],
        entry_px: float,
        df_fast: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """Cap lottery setups to virtual $1k bank; main paper account unchanged."""
        try:
            from core.lottery_bank import (
                assess_lottery_setup,
                lottery_bank_enabled,
                rescale_entry_for_lottery_bank,
            )
            if not lottery_bank_enabled(self.cfg):
                return decision
            forecast = dict(self._last_micro_forecast.get(ticker, {}))
            assess = assess_lottery_setup(
                self.cfg,
                scan_score=float(getattr(self, "_last_scan_score", 0)),
                spike_ratio=float(getattr(self, "_last_spike_ratio", 0)),
                forecast=forecast,
            )
            if not assess.eligible:
                return decision
            out = rescale_entry_for_lottery_bank(self.cfg, decision, entry_px, assess)
            self._pending_lottery_meta[ticker.upper()] = out
            return out
        except Exception as exc:
            log.debug(f"Lottery bank sizing: {exc}")
            return decision
    def _submit_ai_entry(
        self,
        ticker: str,
        df_fast: pd.DataFrame,
        ai_dec: Dict[str, Any],
        market_ctx: Dict[str, Any],
        current_px: float,
    ) -> str:
        """Place bracket entry from an AI/council decision (non-blocking poll path)."""
        try:
            from core.swing_executor import scalp_blocked_by_swing
            if scalp_blocked_by_swing(self, ticker):
                log.debug(f"  Scalp skip {ticker}: swing line open on IB")
                return "swing_block"
        except Exception:
            pass
        bid = market_ctx.get("bid")
        ask = market_ctx.get("ask")
        avg_volume = float(market_ctx.get("avg_volume", 0))
        current_px = self._live_price_for(ticker, current_px)
        gate_dec = dict(ai_dec)
        gate_dec["ticker"] = ticker
        gate_dec["entry"] = current_px
        ok, gate_dec, err = validate_decision_bracket(
            self.cfg, gate_dec, fallback_entry=current_px,
        )
        if not ok:
            if learn_dont_block(self.cfg):
                from core.bracket_validator import compute_atr_bracket
                from core.pilot_mode import get_ai_deploy_budget

                atr = compute_atr(df_fast, period=5)
                deploy = get_ai_deploy_budget(
                    self.cfg,
                    self.pilot,
                    float(self.account_equity),
                    self._deployable_cash(),
                    int(self._open_position_count()),
                )
                reb = compute_atr_bracket(
                    self.cfg,
                    current_px,
                    atr,
                    equity=float(self.account_equity),
                    cash=self._deployable_cash(),
                    deploy_cap=deploy,
                    shares_hint=int(ai_dec.get("shares", 0) or 0),
                )
                if reb.ok:
                    gate_dec = {
                        **ai_dec,
                        "entry": current_px,
                        "stop": reb.stop,
                        "target": reb.target,
                        "shares": reb.shares,
                        "risk_usd": reb.risk_usd,
                        "reward_risk": reb.reward_risk,
                    }
                    ok, gate_dec, err = validate_decision_bracket(
                        self.cfg, gate_dec, fallback_entry=current_px,
                    )
                    if ok:
                        log.info(f"  🔧 BRACKET REPAIRED {ticker} — ATR math (learn mode)")
        if not ok:
            log.warning(f"  🛑 BRACKET REJECTED {ticker}: {err}")
            spike = float(getattr(self, "_last_spike_ratio", 1.0))
            snap = (
                self.ai_commander.ollama_audit_snapshot(ticker)
                if self.ai_commander else {}
            )
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=err,
                entry=current_px,
                stop=float(gate_dec.get("stop", ai_dec.get("stop", 0))),
                target=float(gate_dec.get("target", ai_dec.get("target", 0))),
                shares=int(gate_dec.get("shares", ai_dec.get("shares", 0))),
                council_decision=ai_dec,
                ollama_raw=snap.get("raw", ""),
                ollama_parsed=snap.get("parsed"),
                spike_ratio=spike,
                pipeline="pre_broker_gate",
            )
            try:
                buffer_append({
                    "source": "bracket_reject",
                    "ticker": ticker,
                    "action": "REJECT",
                    "reward": reward_from_bracket_reject(
                        self.cfg, spike_ratio=spike,
                        inverted="INVERTED" in err.upper(),
                    ),
                    "reason": err[:200],
                    "spike_ratio": spike,
                    "ollama_had_prices": bool(snap.get("parsed", {}).get("stop")),
                })
            except Exception:
                pass
            self._observe_runtime(
                "bracket_reject",
                ticker=ticker,
                reason=err[:200],
                spike_ratio=spike,
                market_state=get_market_state(self.cfg),
            )
            self._clear_pending_entry(ticker, cooldown_sec=30.0)
            return "waiting"
        ai_dec = self._apply_war_sizing(ticker, gate_dec, current_px)
        ai_dec = self._apply_lottery_bank_sizing(ticker, ai_dec, current_px, df_fast)
        shares = int(ai_dec["shares"])
        shares = self._liquidity_cap_shares(shares, current_px, df_fast)
        shares = self._clamp_entry_shares(shares, current_px)
        if shares < 1:
            return "waiting"
        spread_pct = (ask - bid) / current_px if bid and ask and current_px > 0 else 0.0
        max_spread = float(getattr(self.cfg, "MAX_ENTRY_SPREAD_PCT", 0.05))
        if spread_pct > max_spread and not learn_dont_block(self.cfg):
            log.info(f"  ⏭ Skip {ticker}: spread {spread_pct:.1%} > {max_spread:.0%} (IB 2161 risk)")
            self._clear_pending_entry(ticker, cooldown_sec=60.0)
            return "waiting"
        now = time.time()
        fail_cd = float(getattr(self.cfg, "ENTRY_FAILURE_COOLDOWN_SEC", 30.0))
        fill_wait = entry_fill_poll_sec(self.cfg)
        max_wait = float(getattr(self.cfg, "ENTRY_FILL_MAX_WAIT_SEC", 30.0))
        fill_polls = max(5, int(max_wait / fill_wait))
        n_cancelled = self.broker.cancel_open_orders_for_symbol(ticker)
        if n_cancelled:
            log.info(f"  🧹 Cleared {n_cancelled} stale {ticker} order(s) before entry")
        self._pending_entry_ticker = ticker
        block_sec = entry_pending_block_sec(self.cfg)
        if ai_fast_execution(self.cfg):
            block_sec = min(block_sec, 20.0)
        self._pending_entry_until = now + block_sec
        regime_result, regime_label = resolve_regime(
            self.regime_detector, df_fast,
            spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
            vol_ratio=float(market_ctx.get("recent_volume", 0)) / (avg_volume + 1e-9),
        )
        vix_level = 0.0
        try:
            ctx = summarize_market_context()
            vix_level = float(ctx.get("vix_level", 0.0))
        except Exception:
            pass
        self.pilot.start_flight(ticker, current_px, regime_result, 0.5, vix_level=vix_level)
        spike = float(getattr(self, "_last_spike_ratio", 1.0))
        vol_ratio = float(market_ctx.get("recent_volume", 0)) / (avg_volume + 1e-9)
        self._last_entry_regime = regime_label
        snap = (
            self.ai_commander.ollama_audit_snapshot(ticker)
            if self.ai_commander else {}
        )
        plan = TradePlan(
            side="LONG", entry_price=current_px, shares=float(shares),
            initial_stop_price=float(ai_dec["stop"]),
            take_profit_price=float(ai_dec["target"]),
            risk_usd=float(ai_dec.get("risk_usd", 50.0)),
            atr_at_entry=compute_atr(df_fast, period=5),
        )
        entry_parent_px, entry_mode = self._entry_price_mode(
            current_px, bid, ask, shares, avg_volume,
        )
        plan, ai_dec = self._reanchor_bracket_to_limit(
            plan, ai_dec, entry_parent_px, df_fast, shares, current_px,
        )
        if self.shadow_circuit.block_broker():
            log.warning(
                f"  🌑 SHADOW — simulating {ticker} entry (NO IB order — no mobile notification)"
            )
            self.shadow_circuit.open_shadow_trade(
                ticker, current_px, plan.initial_stop_price,
                plan.take_profit_price, shares, regime=regime_label,
            )
            log_entry_execution(
                ticker=ticker, limit_px=entry_parent_px, fill_px=current_px,
                entry_mode=entry_mode, shares=shares,
                stop=plan.initial_stop_price, target=plan.take_profit_price,
                regime=regime_label, spike_ratio=spike,
                council_decision=ai_dec,
                ollama_raw=snap.get("raw", ""),
                ollama_parsed=snap.get("parsed"),
                shadow=True,
            )
            self._last_entry_telemetry = {
                "limit_px": entry_parent_px, "slippage_pct": 0.0, "shadow": True,
            }
            self._clear_pending_entry(ticker, cooldown_sec=15.0)
            return "shadow"
        min_fill_ratio = float(getattr(self.cfg, "MIN_ENTRY_FILL_RATIO", 0.85))
        last_ib_error = None
        for attempt in range(2):
            if attempt > 0:
                cap = (last_ib_error or {}).get("price_cap")
                if cap and cap > 0:
                    entry_parent_px = cap
                    entry_mode = "limit_ib_cap"
                shares = max(1, shares // 2)
                plan = TradePlan(
                    side="LONG", entry_price=current_px, shares=float(shares),
                    initial_stop_price=float(ai_dec["stop"]),
                    take_profit_price=float(ai_dec["target"]),
                    risk_usd=float(ai_dec.get("risk_usd", 50.0)),
                    atr_at_entry=plan.atr_at_entry,
                )
                plan, ai_dec = self._reanchor_bracket_to_limit(
                    plan, ai_dec, entry_parent_px, df_fast, shares, current_px,
                )
                log.info(f"  🔄 IB2161 retry: {shares} sh limit @ ${entry_parent_px:.4f}")
            else:
                entry_parent_px, entry_mode = self._entry_price_mode(
                    current_px, bid, ask, shares, avg_volume,
                )
                plan, ai_dec = self._reanchor_bracket_to_limit(
                    plan, ai_dec, entry_parent_px, df_fast, shares, current_px,
                )
            self._last_entry_telemetry = {
                "limit_px": entry_parent_px,
                "entry_mode": entry_mode,
                "council": ai_dec,
                "ollama_raw": snap.get("raw", ""),
                "regime": regime_label,
                "atr": float(plan.atr_at_entry or 0),
            }
            bracket = self.broker.place_bracket_buy(
                quantity=shares, limit_or_market_price=entry_parent_px,
                stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                symbol=ticker,
            )
            self._pending_brackets_by_ticker[ticker] = bracket
            try:
                from core.smart_stack import count_hourly_filled_entry
                count_on_submit = not count_hourly_filled_entry(self.cfg)
            except Exception:
                count_on_submit = True
            if capital_discipline_enabled(self.cfg) and count_on_submit:
                self._entries_this_hour = getattr(self, "_entries_this_hour", 0) + 1
            if not self._position_slots:
                self.bracket_handle = bracket
            mode_label = "MARKET" if entry_parent_px is None else f"LIMIT@${entry_parent_px:.4f}"
            log.info(f"  📥 Entry mode: {entry_mode} ({mode_label}) | {shares} sh @ ~${current_px:.4f}")
            if getattr(self.cfg, "PARALLEL_ENTRY_EXIT", True):
                self._entry_poll_states[ticker] = new_entry_poll_state(
                    ticker=ticker,
                    shares=shares,
                    plan=plan,
                    current_px=current_px,
                    entry_parent_px=entry_parent_px,
                    fill_polls=fill_polls,
                    min_fill_ratio=min_fill_ratio,
                    fail_cd=fail_cd,
                    attempt=attempt,
                    last_ib_error=last_ib_error,
                    bracket=bracket,
                    ib=self.ib,
                )
                log.info(
                    f"  ⏳ Awaiting IB fill {ticker}: {shares} sh "
                    f"parent#{bracket.parent_order_id} ({mode_label})"
                )
                return "waiting"
        return "waiting"
    def _attempt_entry(self) -> str:
        """
        Attempt entry on self.top_pick.
        Returns: 'entered', 'permanent_skip', or 'waiting'
        """
        can_trade, market_state = can_trade_now(self.cfg)
        if not can_trade:
            return "waiting"

        if (
            not self.conn.is_connected()
            or self.conn.in_connectivity_outage()
            or self._ib_connectivity_paused
        ):
            return "waiting"

        if not self.top_pick:
            return 'waiting'
        ticker = self.top_pick.ticker

        if ticker in self._contract_blacklist:
            return "waiting"

        if self._has_ai_council(ticker, "entry_decision"):
            return "waiting"

        if ticker in self._held_tickers():
            return 'waiting'
        now = time.time()
        if ticker in self._entry_poll_states:
            return "waiting"
        if self._pending_entry_ticker == ticker and now < self._pending_entry_until:
            return "waiting"
        if now < self._entry_cooldown_until.get(ticker, 0):
            return 'waiting'

        try:
            from core.live_trade_guard import check_ticker_cooldown
            cd_block = check_ticker_cooldown(ticker)
            if cd_block:
                if now - getattr(self, "_last_quality_watch_log", 0) >= 45.0:
                    self._last_quality_watch_log = now
                    log.info(f"  👁 {cd_block}")
                return "waiting"
        except Exception:
            pass

        if self.risk.is_halted():
            return 'waiting'

        if self._open_position_count() >= self._max_concurrent():
            return 'waiting'

        if now - getattr(self, "_hour_window_start", 0) >= 3600:
            self._hour_window_start = now
            self._entries_this_hour = 0
        rate_ok, rate_msg = check_entry_rate_limit(
            getattr(self, "_entries_this_hour", 0),
            getattr(self, "_hour_window_start", now),
            self.cfg,
        )
        if not rate_ok:
            if now - getattr(self, "_last_quality_watch_log", 0) >= float(
                getattr(self.cfg, "QUALITY_WATCH_HEARTBEAT_SEC", 45)
            ):
                self._last_quality_watch_log = now
                log.info(f"  👁 {rate_msg}")
            return "waiting"
        
        try:
            self.cfg.TICKER = ticker
            min_bars = self._min_bars_for(ticker)

            scan_score = self.top_pick.rank_score if self.top_pick else 0.0
            df_fast, current_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=min_bars)
            tick_burst_ratio = 0.0
            if df_fast is None or len(df_fast) < min_bars:
                if dm:
                    burst, burst_ratio = self._detect_tick_volume_burst(
                        dm, df_fast if df_fast is not None else pd.DataFrame(),
                    )
                    from core.capital_discipline import is_strong_spike_setup
                    tick_ok = burst and (
                        should_spike_fast_entry(self.cfg, burst_ratio, scan_score)
                        or is_strong_spike_setup(self.cfg, scan_score, burst_ratio)
                    )
                    if tick_ok:
                        tick_burst_ratio = burst_ratio
                        df_fast = dm.get_fast_bar_dataframe(n=24)
                        current_px = float(dm.get_latest_price() or 0)
                        if df_fast is None or len(df_fast) < 3 or current_px <= 0:
                            return 'waiting'
                        min_bars = min(min_bars, 3)
                    else:
                        return 'waiting'
                else:
                    return 'waiting'
            if not forecast:
                forecast = dict(self._last_micro_forecast.get(ticker, {}))
            avg_volume = float(df_fast["volume"].tail(20).mean())
            bid, ask = self._get_bid_ask(ticker)
            spread_pct = (ask - bid) / current_px if bid and ask and current_px > 0 else 0.0
            market_ctx = {
                "bid": bid, "ask": ask, "spread_pct": spread_pct,
                "avg_volume": avg_volume,
                "recent_volume": float(df_fast["volume"].iloc[-1]),
            }

            is_spike, spike_ratio = self._detect_volume_spike(df_fast)
            vol_ratio = float(df_fast["volume"].tail(3).mean()) / (
                float(df_fast["volume"].tail(20).mean()) + 1e-9
            )
            if not is_spike and vol_ratio >= 1.15:
                is_spike, spike_ratio = True, vol_ratio
            if dm:
                burst, burst_ratio = self._detect_tick_volume_burst(dm, df_fast)
                if burst:
                    is_spike, spike_ratio = True, max(spike_ratio, burst_ratio)
            elif tick_burst_ratio > 0:
                is_spike, spike_ratio = True, max(spike_ratio, tick_burst_ratio)
            is_spike, spike_ratio = apply_micro_spike_boost(
                is_spike, spike_ratio, forecast, cfg=self.cfg, scan_score=scan_score,
                live_px=float(current_px or 0),
            )

            try:
                from core.commander_replay import shadow_would_skip_entry
                from core.slow_coach import coach_lane_enabled, log_shadow_skip
                if coach_lane_enabled(self.cfg):
                    prob = float(forecast.get("profit_probability", 0) or 0)
                    fade = float(
                        forecast.get("fakeout_risk", 0)
                        or forecast.get("fade_risk", 0)
                        or 0
                    )
                    would_skip, shadow_reason = shadow_would_skip_entry(
                        self.cfg,
                        ticker=ticker,
                        scan_score=scan_score,
                        spike_ratio=spike_ratio,
                        profit_probability=prob,
                        fakeout_risk=fade,
                    )
                    if would_skip:
                        log_shadow_skip(
                            self.cfg,
                            ticker=ticker,
                            reason=shadow_reason,
                            scan_score=scan_score,
                            spike_ratio=spike_ratio,
                        )
            except Exception:
                pass

            from core.smart_stack import evaluate_pre_entry_advisories
            gate_ok, gate_msg, gate_adv = evaluate_pre_entry_advisories(
                self.cfg,
                scan_score=scan_score,
                spike_ratio=spike_ratio,
                forecast=forecast,
                live_px=float(current_px or 0),
            )
            if gate_adv:
                self._smart_gate_context[ticker.upper()] = {
                    **self._smart_gate_context.get(ticker.upper(), {}),
                    **gate_adv,
                }
            if not gate_ok:
                cd = entry_cooldown_after_skip(self.cfg)
                self._spike_skip_until[ticker] = now + cd
                if gate_msg and now - getattr(self, "_last_quality_watch_log", 0) >= float(
                    getattr(self.cfg, "QUALITY_WATCH_HEARTBEAT_SEC", 45)
                ):
                    self._last_quality_watch_log = now
                    log.info(f"  👁 WATCH {ticker}: {gate_msg}")
                return "waiting"

            if not is_ai_unlimited(self.cfg) or capital_discipline_enabled(self.cfg):
                from core.capital_discipline import is_strong_spike_setup
                from core.sniper_execution import sniper_vol_flash
                from core.green_trade_doctrine import green_entry_mandatory
                uptrend_ok = only_uptrend(df_fast, current_px, min_bars=min_bars)
                if not uptrend_ok and not green_entry_mandatory(self.cfg) and not (
                    should_spike_fast_entry(self.cfg, spike_ratio, scan_score)
                    or is_strong_spike_setup(self.cfg, scan_score, spike_ratio)
                    or sniper_vol_flash(self.cfg, scan_score, spike_ratio)
                ):
                    log.debug(f"Entry skip {ticker}: not uptrend")
                    return 'waiting'

            if (
                forecast.get("dir", 0) < 0
                and forecast.get("spike_likelihood", 0) < 0.55
                and not forecast.get("breakout")
            ):
                log.debug(f"Entry skip {ticker}: micro bearish forecast")
                return 'waiting'
            if not is_spike and (not is_ai_unlimited(self.cfg) or capital_discipline_enabled(self.cfg)):
                log.debug(f"Entry skip {ticker}: no volume spike (ratio={spike_ratio:.2f})")
                return 'waiting'
            
            self._ai_update_buffers(df_fast, current_px)
            self._last_spike_ratio = spike_ratio
            self._last_scan_score = scan_score
            self._last_market_ctx = market_ctx

            if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                obs = None
                if len(self._feature_buffer) >= self.cfg.WINDOW_SIZE:
                    window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
                    total = self.bot_cash + self.shares * current_px
                    c_rat = self.bot_cash / (total + 1e-9)
                    p_rat = (self.shares * current_px) / (total + 1e-9) if self.shares > 0 else 0.0
                    obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
                bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
                ai_dec = self.ai_commander.decide_entry(
                    ticker, df_fast, current_px, spike_ratio, scan_score,
                    account={
                        **self._account_context_for_ai(),
                        "micro_forecast": forecast,
                    },
                    obs=obs, bar_df=bar_df, pilot=self.pilot, market_ctx=market_ctx,
                )
                if ai_dec.get("pending"):
                    ppo_a = int(ai_dec.get("ppo_action", 0))
                    ppo_c = float(ai_dec.get("ppo_conf", 0.5))
                    min_c = float(ai_dec.get("min_conf", 0.55))
                    ppo_lead = (
                        allows_ppo_lead_while_pending(
                            self.cfg,
                            scan_score=scan_score,
                            spike_ratio=spike_ratio,
                        )
                        and ai_fast_execution(self.cfg)
                        and int(getattr(self.risk, "_consecutive_losses", 0) or 0)
                        < int(os.getenv("LOSS_STREAK_BLOCK_BYPASS_AT", "2"))
                        and (
                            (ppo_a == 1 and ppo_c >= min_c * 0.72)
                            or (
                                ppo_a == 1
                                and should_spike_fast_entry(
                                    self.cfg, spike_ratio, scan_score, ppo_a, ppo_c,
                                )
                            )
                        )
                    )
                    if ppo_lead:
                        lead = self.ai_commander.execute_ppo_led_entry_while_pending(
                            ticker, df_fast, current_px, spike_ratio, scan_score,
                            account={
                                **self._account_context_for_ai(),
                                "micro_forecast": forecast,
                            },
                            ppo_action=ppo_a, ppo_conf=ppo_c,
                            ppo_reason=str(ai_dec.get("ppo_reason", "")),
                            min_conf=min_c, pilot=self.pilot, market_ctx=market_ctx,
                            fingerprint=str(ai_dec.get("fingerprint", "")),
                            micro=forecast,
                        )
                        if lead.get("enter"):
                            log.info(
                                f"  ⚡ PPO ENTER {ticker} (council still thinking — logging async)"
                            )
                            self._last_ai_confidence = float(lead.get("confidence", 0.5))
                            return self._submit_ai_entry(
                                ticker, df_fast, lead, market_ctx, current_px,
                            )
                    self._set_ai_council(ticker, "entry_decision", {
                        "fingerprint": ai_dec["fingerprint"],
                        "ppo_action": ai_dec["ppo_action"],
                        "ppo_conf": ai_dec["ppo_conf"],
                        "ppo_reason": ai_dec["ppo_reason"],
                        "min_conf": ai_dec["min_conf"],
                        "spike_ratio": spike_ratio,
                        "scan_score": scan_score,
                        "market_ctx": market_ctx,
                        "micro_forecast": forecast,
                        "pilot": self.pilot,
                        "started_at": now,
                        "local_only": bool(ai_dec.get("local_only", False)),
                        "teacher_rung": bool(ai_dec.get("teacher_rung", True)),
                    })
                    log.info(
                        f"  🧠 COUNCIL {ticker}: {(ai_dec.get('reason') or 'deliberating')[:100]} | "
                        f"{ai_dec.get('pipeline', '')}"
                    )
                    return "waiting"
                if not ai_dec.get("enter"):
                    reason = (ai_dec.get("reason") or "")[:80]
                    pipeline = ai_dec.get("pipeline", "")
                    log.info(
                        f"  🧠 AI skip {ticker}: {reason}"
                        + (f" | {pipeline}" if pipeline else "")
                    )
                    if not is_ai_unlimited(self.cfg) or capital_discipline_enabled(self.cfg):
                        from core.smart_stack import smart_stack_enabled
                        if (
                            not smart_stack_enabled(self.cfg)
                            and pipeline == "sniper:ppo_hold_skip"
                        ):
                            from core.sniper_execution import sniper_ppo_hold_skip_sec
                            cd = sniper_ppo_hold_skip_sec(self.cfg)
                        else:
                            cd = entry_cooldown_after_skip(self.cfg)
                        self._spike_skip_until[ticker] = time.time() + cd
                    return "waiting"
                pipeline = str(ai_dec.get("pipeline", ""))
                if "timeout" in pipeline:
                    self._observe_runtime(
                        "council_timeout",
                        ticker=ticker,
                        pipeline=pipeline,
                        reason=(ai_dec.get("reason") or "")[:200],
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                        confidence=float(ai_dec.get("confidence", 0) or 0),
                        market_state=get_market_state(self.cfg),
                    )
                self._last_ai_confidence = float(ai_dec.get("confidence", 0.5))
                try:
                    from core.green_trade_doctrine import require_green_entry, green_entry_mandatory
                    if green_entry_mandatory(self.cfg) and ai_dec.get("enter"):
                        block = require_green_entry(
                            self.cfg,
                            ticker=ticker,
                            df=df_fast,
                            current_px=current_px,
                            micro=forecast,
                            spike_ratio=spike_ratio,
                            scan_score=scan_score,
                            ppo_action=int(ai_dec.get("ppo_action", 0) or 0),
                            ppo_conf=float(ai_dec.get("ppo_conf", 0.5) or 0.5),
                            decision=ai_dec,
                        )
                        if block:
                            log.info(f"  🟢 GREEN veto {ticker}: {block[:100]}")
                            self._spike_skip_until[ticker] = time.time() + entry_cooldown_after_skip(self.cfg)
                            return "waiting"
                except Exception:
                    pass
                return self._submit_ai_entry(ticker, df_fast, ai_dec, market_ctx, current_px)
            else:
                inst = self.institutional.scan()
                override, reason = self.institutional.should_override_buy()
                if override:
                    log.debug(f"Entry skip {ticker}: institutional override — {reason}")
                    return 'waiting'
                if self.autopilot:
                    allowed, cog_reason, _ = self.autopilot.should_trade(
                        self._build_ai_context(df_fast, current_px)
                    )
                    if not allowed:
                        log.debug(f"Entry skip {ticker}: cognitive — {cog_reason}")
                        return 'waiting'
                if self.cfg.USE_ENHANCED_AI and self.model is not None:
                    should_enter, ai_conf, ai_reason = self._ai_gate_entry(
                        ticker, current_px, spike_ratio=spike_ratio, scan_score=scan_score,
                    )
                    if not should_enter:
                        log.info(f"  🧠 AI gate skip {ticker}: conf={ai_conf:.0%} — {(ai_reason or '')[:80]}")
                        return 'waiting'
                deploy_usd = get_deploy_usd(self.cfg, self.pilot)
                shares = int(deploy_usd / current_px)
                if shares < 1:
                    log.debug(f"Entry skip {ticker}: shares={shares} < 1")
                    return 'waiting'
                stop_usd = get_trade_risk_usd(self.cfg, self.account_equity)
                stop_dist = stop_usd / shares
                stop_dist = max(stop_dist, current_px * self.cfg.SCALP_MIN_STOP_PCT)
                tp_dist = stop_dist * 2.5
                tp_dist = min(tp_dist, current_px * 0.05)
                ai_dec = {
                    "shares": shares,
                    "stop": round(current_px - stop_dist, 4),
                    "target": round(current_px + tp_dist, 4),
                    "risk_usd": stop_usd,
                }

            ai_dec = self._apply_war_sizing(ticker, ai_dec, current_px)
            ai_dec = self._apply_lottery_bank_sizing(ticker, ai_dec, current_px, df_fast)

            shares = int(ai_dec["shares"])
            if shares < 1:
                return 'waiting'

            current_px = self._live_price_for(ticker, current_px)
            shares = self._liquidity_cap_shares(shares, current_px, df_fast)
            shares = self._clamp_entry_shares(shares, current_px)
            if shares < 1:
                return 'waiting'

            spread_pct = (ask - bid) / current_px if bid and ask and current_px > 0 else 0.0
            max_spread = float(getattr(self.cfg, "MAX_ENTRY_SPREAD_PCT", 0.05))
            if spread_pct > max_spread:
                log.info(f"  ⏭ Skip {ticker}: spread {spread_pct:.1%} > {max_spread:.0%} (IB 2161 risk)")
                self._clear_pending_entry(ticker, cooldown_sec=60.0)
                return 'waiting'

            fail_cd = float(getattr(self.cfg, "ENTRY_FAILURE_COOLDOWN_SEC", 30.0))
            fill_wait = entry_fill_poll_sec(self.cfg)
            max_wait = float(getattr(self.cfg, "ENTRY_FILL_MAX_WAIT_SEC", 30.0))
            fill_polls = max(5, int(max_wait / fill_wait))

            # One bracket per symbol — cancel any resting orders before submit
            n_cancelled = self.broker.cancel_open_orders_for_symbol(ticker)
            if n_cancelled:
                log.info(f"  🧹 Cleared {n_cancelled} stale {ticker} order(s) before entry")
            self._pending_entry_ticker = ticker
            block_sec = entry_pending_block_sec(self.cfg)
            if ai_fast_execution(self.cfg):
                block_sec = min(block_sec, 20.0)
            self._pending_entry_until = now + block_sec

            # Start pilot flight tracking
            regime_result, _ = resolve_regime(
                self.regime_detector, df_fast,
                spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                vol_ratio=1.0,
            )
            vix_level = 0.0
            try:
                ctx = summarize_market_context()
                vix_level = float(ctx.get('vix_level', 0.0))
            except Exception:
                pass
            self.pilot.start_flight(ticker, current_px, regime_result, 0.5, vix_level=vix_level)

            plan = TradePlan(
                side="LONG", entry_price=current_px, shares=float(shares),
                initial_stop_price=float(ai_dec["stop"]),
                take_profit_price=float(ai_dec["target"]),
                risk_usd=float(ai_dec.get("risk_usd", 50.0)),
                atr_at_entry=compute_atr(df_fast, period=5),
            )

            filled_shares = 0.0
            fill_px = current_px
            min_fill_ratio = float(getattr(self.cfg, "MIN_ENTRY_FILL_RATIO", 0.85))
            entry_parent_px = None
            entry_mode = "market"
            parent_trade = None
            last_ib_error = None

            for attempt in range(2):
                if attempt > 0:
                    cap = (last_ib_error or {}).get("price_cap")
                    if cap and cap > 0:
                        entry_parent_px = cap
                        entry_mode = "limit_ib_cap"
                    shares = max(1, shares // 2)
                    plan = TradePlan(
                        side="LONG", entry_price=current_px, shares=float(shares),
                        initial_stop_price=float(ai_dec["stop"]),
                        take_profit_price=float(ai_dec["target"]),
                        risk_usd=float(ai_dec.get("risk_usd", 50.0)),
                        atr_at_entry=plan.atr_at_entry,
                    )
                    log.info(f"  🔄 IB2161 retry: {shares} sh limit @ ${entry_parent_px:.4f}")
                else:
                    entry_parent_px, entry_mode = self.broker.decide_smart_entry(
                        current_px, bid, ask, shares, avg_volume,
                    )

                bracket = self.broker.place_bracket_buy(
                    quantity=shares, limit_or_market_price=entry_parent_px,
                    stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                    symbol=ticker,
                )
                self._pending_brackets_by_ticker[ticker] = bracket
                if not self._position_slots:
                    self.bracket_handle = bracket
                mode_label = "MARKET" if entry_parent_px is None else f"LIMIT@${entry_parent_px:.4f}"
                log.info(f"  📥 Entry mode: {entry_mode} ({mode_label}) | {shares} sh @ ~${current_px:.4f}")

                if getattr(self.cfg, "PARALLEL_ENTRY_EXIT", True):
                    self._entry_poll_states[ticker] = new_entry_poll_state(
                        ticker=ticker,
                        shares=shares,
                        plan=plan,
                        current_px=current_px,
                        entry_parent_px=entry_parent_px,
                        fill_polls=fill_polls,
                        min_fill_ratio=min_fill_ratio,
                        fail_cd=fail_cd,
                        attempt=attempt,
                        last_ib_error=last_ib_error,
                        bracket=bracket,
                        ib=self.ib,
                    )
                    log.info(
                        f"  ⏳ Awaiting IB fill {ticker}: {shares} sh "
                        f"parent#{bracket.parent_order_id} ({mode_label})"
                    )
                    return "waiting"

                poll_st = {
                    "ib_pos_baseline": self._ib_position_shares(ticker),
                    "started_at": time.time(),
                    "fill_px": current_px,
                }
                filled_shares = 0.0
                parent_trade = getattr(bracket, "parent_trade", None)
                parent_id = bracket.parent_order_id
                cancelled = False
                for _ in range(fill_polls):
                    self.ib.sleep(fill_wait)
                    parent_trade = getattr(bracket, "parent_trade", None)
                    parent_status = (
                        parent_trade.orderStatus.status
                        if parent_trade and parent_trade.orderStatus else "Unknown"
                    )
                    ierr = self.conn.pop_order_error(parent_id)
                    if ierr:
                        last_ib_error = ierr
                    if ierr and ierr.get("code") == 2161:
                        log.warning(
                            f"  IB 2161 regulatory cap on {ticker} — "
                            f"will retry smaller limit"
                        )
                    if parent_status in ("Cancelled", "Inactive", "ApiCancelled"):
                        cancelled = True
                        block_reason = parse_ib_order_block(ierr)
                        if block_reason:
                            return self._ai_skip_ticker_permanent(ticker, block_reason)
                        if (
                            attempt == 0
                            and getattr(self.cfg, "ENTRY_RETRY_ON_IB2161", True)
                            and (ierr or {}).get("code") == 2161
                        ):
                            self.broker.cancel_open_orders_for_symbol(ticker)
                            break
                        log.warning(f"Entry order rejected by IB ({parent_status}) — not opening position")
                        self._pending_brackets_by_ticker.pop(ticker, None)
                        self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
                        return 'waiting'
                    filled, avg_px, confirmed, fill_src = self._confirm_entry_fill_from_ib(
                        ticker, poll_st, bracket, shares, min_fill_ratio, fill_px,
                    )
                    if confirmed:
                        filled_shares = filled
                        if avg_px > 0:
                            fill_px = avg_px
                        cancelled = False
                        break

                if filled_shares >= shares * min_fill_ratio:
                    break
                if cancelled and attempt == 0 and getattr(self.cfg, "ENTRY_RETRY_ON_IB2161", True):
                    self.broker.cancel_open_orders_for_symbol(ticker)
                    self._pending_brackets_by_ticker.pop(ticker, None)
                    continue
                break

            if filled_shares < shares * min_fill_ratio:
                parent_status = (
                    parent_trade.orderStatus.status
                    if parent_trade and parent_trade.orderStatus else "Unknown"
                )
                block_reason = parse_ib_order_block(last_ib_error)
                if block_reason:
                    return self._ai_skip_ticker_permanent(ticker, block_reason)
                if filled_shares >= 1:
                    log.warning(
                        f"Partial fill {int(filled_shares)}/{shares} below "
                        f"{min_fill_ratio:.0%} — flattening and skipping entry"
                    )
                    self.broker.flatten_position(
                        int(filled_shares), handle=bracket,
                        urgent=True, symbol=ticker,
                    )
                    self.ib.sleep(0.5)
                elif parent_status in ("Submitted", "PreSubmitted", "PendingSubmit"):
                    log.info(f"Entry order pending for {ticker} ({parent_status}) — waiting for IB fill")
                else:
                    log.info(f"Entry not filled for {ticker} (status={parent_status})")
                self.broker.cancel_open_orders_for_symbol(ticker)
                self._pending_brackets_by_ticker.pop(ticker, None)
                self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
                return 'waiting'

            shares = int(filled_shares)
            return self._open_position_from_fill(ticker, shares, fill_px, plan)
        except Exception as exc:
            log.error(f"Entry error on {ticker}: {exc}")
            return 'waiting'
    def _build_ai_context(self, df: pd.DataFrame, current_px: float) -> Dict:
        """Build market context dict for cognitive autopilot decisions."""
        regime_label = "slow_grind"
        trend_strength = 0.5
        volatility = 0.5
        try:
            _, regime_label = resolve_regime(
                self.regime_detector, df,
                spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                vol_ratio=1.0,
            )
            rr = self.regime_detector.classify(df) if df is not None and len(df) >= 5 else None
            if rr is not None:
                trend_strength = abs(float(getattr(rr, "trend_strength", 0.0) or 0.0))
                vol_pct = float(getattr(rr, "volatility_percentile", 50.0) or 50.0)
                volatility = vol_pct / 100.0 if vol_pct > 1.0 else vol_pct
        except Exception:
            pass
        active = getattr(self, "_active_positions", [])
        return {
            "regime": str(regime_label).lower().replace("marketregime.", ""),
            "volatility": volatility,
            "trend_strength": max(trend_strength, 0.1),
            "desired_positions": len(active) + 1,
            "price": current_px,
        }
    def _ai_gate_entry(self, ticker: str, current_px: float,
                      spike_ratio: float = 1.0, scan_score: float = 0.0) -> Tuple[bool, float, str]:
        """
        Use full enhanced AI pipeline to decide if entry is justified.
        Strong technical setups (volume spike + scan score) can override uncertain AI.
        
        Returns:
            (should_enter, confidence, reasoning)
        """
        if not self.cfg.USE_ENHANCED_AI or not self.ai_components:
            return True, 0.5, "AI disabled"
        if self.model is None:
            return True, 0.5, "No model"
        if self._model_fresh:
            return True, 0.5, "Fresh model — bypassing AI gate (rule-based only)"
        if len(self._feature_buffer) < self.cfg.WINDOW_SIZE:
            return True, 0.5, "Warming up"
        
        try:
            from core.agent import predict_with_reasoning
            
            window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
            total = self.bot_cash + self.shares * current_px
            c_rat = self.bot_cash / (total + 1e-9)
            p_rat = (self.shares * current_px) / (total + 1e-9) if self.shares > 0 else 0.0
            obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
            
            bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
            
            action, confidence, reasoning = predict_with_reasoning(
                self.model, obs, self.cfg, self.ai_components,
                bar_df=bar_df,
                recent_rewards=getattr(self.perf, 'recent_rewards', None) if hasattr(self, 'perf') else None,
                for_entry=True,
            )
            
            threshold = get_effective_confidence_threshold(self.cfg, self.pilot)
            should_enter = (action == 1 and confidence >= threshold)

            # Technical momentum override — disabled when council owns decisions
            if not should_enter and action != 2 and not is_ai_council_mode(self.cfg):
                if spike_ratio >= 1.5 and scan_score >= 35:
                    should_enter = True
                    confidence = max(confidence, 0.55)
                    reasoning = (
                        f"Technical override: spike={spike_ratio:.1f}x score={scan_score:.0f} | "
                        f"{reasoning or 'momentum confirm'}"
                    )
                elif action == 1 and confidence >= threshold * 0.85 and spike_ratio >= 1.3:
                    should_enter = True
                    reasoning = f"Moderate AI+vol: conf={confidence:.0%} spike={spike_ratio:.1f}x"

            self._last_ai_confidence = confidence
            return should_enter, confidence, reasoning or "AI evaluation"
        except Exception as exc:
            log.debug(f"AI gate entry error: {exc}")
            return True, 0.5, f"AI error: {exc}"
