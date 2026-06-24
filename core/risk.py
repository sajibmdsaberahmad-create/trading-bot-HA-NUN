#!/usr/bin/env python3
"""
core/risk.py — The risk engine. Read this file before you trade real money.

This is a hardcoded safety layer. The PPO agent is NEVER trusted to size
its own positions or manage its own exits — it only ever proposes
HOLD / BUY / SELL. Everything about HOW MUCH to risk and WHERE to exit
is computed here, deterministically, from volatility and account size.

═══════════════════════════════════════════════════════════════════════
THE FOUR EXIT MECHANISMS, AND WHY THERE ARE FOUR OF THEM
═══════════════════════════════════════════════════════════════════════
1. HARD STOP-LOSS    — an IB bracket order sitting on IB's servers from
                        the moment of entry. Fires even if this bot's
                        process is dead, your Mac is asleep, or your VPS
                        loses internet. This is the loss ceiling.

2. TRAILING STOP-LOSS — once a trade is in profit, the stop is walked
                        up behind price (ATR-based distance). Turns a
                        winning trade that reverses into a smaller win
                        or breakeven instead of round-tripping to a loss.
                        Re-submitted as a new IB stop order whenever it
                        moves, so it is ALSO resting on IB's servers.

3. HARD TAKE-PROFIT   — an IB bracket order on the other side of entry,
                        sized from recent volatility and momentum (the
                        "predictive" target). Worst-case-still-good exit
                        if the bot goes offline mid-trade.

4. TRAILING PROFIT    — once unrealised gain passes an activation
                        threshold, a profit floor arms and ratchets up,
                        allowing a controlled give-back so a strong move
                        can keep running instead of being capped at the
                        fixed take-profit. Checked every tick in software
                        AND mirrored to IB as a moving stop order.

═══════════════════════════════════════════════════════════════════════
WHY ATR-BASED, NOT FIXED-PERCENT
═══════════════════════════════════════════════════════════════════════
A fixed 1% stop is too tight in a volatile market (you get stopped out
by noise) and too loose in a calm one (you give back more than
necessary). ATR (Average True Range) measures how much the stock
actually moves bar-to-bar right now, so the stop distance adapts
automatically. MIN/MAX_STOP_DISTANCE_PCT then clamp it so a single
freak volatility spike can't produce an absurd stop.

═══════════════════════════════════════════════════════════════════════
WHY RISK-BASED POSITION SIZING (NOT % OF CASH)
═══════════════════════════════════════════════════════════════════════
"Max $50 loss on $1,000" is a statement about DOLLARS, not about
percent of cash deployed. The only way to guarantee it is:

    risk_usd      = min(equity * RISK_PER_TRADE_PCT, MAX_RISK_PER_TRADE_USD)
    stop_distance = entry_price - stop_price   (in dollars per share)
    shares        = risk_usd / stop_distance

This is computed BEFORE the trade, then capped by available cash,
MAX_POSITION_PCT, and MAX_SHARES_PER_TRADE as secondary safety limits.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log


# ─────────────────────────────────────────────────────────────────────────────
# ATR helper (works on any OHLC dataframe — fast bars or decision bars)
# ─────────────────────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Latest ATR value from an OHLC dataframe. Returns 0.0 if not enough data."""
    if len(df) < period + 1:
        return 0.0
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else 0.0


def safe_vwap(prices: np.ndarray, volumes: np.ndarray) -> float:
    """Volume-weighted average that survives pre-market / zero-volume bars."""
    px = np.asarray(prices, dtype=float)
    vol = np.asarray(volumes, dtype=float)
    if len(px) == 0:
        return 0.0
    if vol.sum() <= 0:
        return float(np.mean(px))
    return float(np.average(px, weights=vol))


def compute_momentum_score(df: pd.DataFrame, lookback: int = 10) -> float:
    """
    Simple normalised momentum score in [-1, 1] used to bias the
    predictive take-profit distance: strong existing momentum in the
    trade's direction justifies a slightly more ambitious target.
    """
    if len(df) < lookback + 1:
        return 0.0
    closes = df["close"].values[-(lookback + 1):]
    ret = np.log(closes[-1] / closes[0])
    # Normalise against typical ATR-scale move; clip to [-1, 1]
    atr = compute_atr(df, period=min(14, len(df) - 1))
    if atr <= 0:
        return 0.0
    score = ret / (atr / closes[0] * lookback + 1e-9)
    return float(np.clip(score, -1.0, 1.0))


# ─────────────────────────────────────────────────────────────────────────────
# Trade plan — computed once at entry, then updated tick-by-tick
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradePlan:
    """Everything about one open position's risk management state."""
    side:               str             # "LONG" (this bot is long-only for now)
    entry_price:        float
    shares:             float
    initial_stop_price: float
    take_profit_price:  float
    risk_usd:           float
    atr_at_entry:       float

    peak_price:         float = field(init=False)
    current_stop_price: float = field(init=False)

    trailing_stop_armed:   bool = field(default=False, init=False)
    trailing_profit_armed: bool = field(default=False, init=False)
    profit_floor_price:    Optional[float] = field(default=None, init=False)

    def __post_init__(self):
        self.peak_price = self.entry_price
        self.current_stop_price = self.initial_stop_price


# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class RiskManager:
    """
    Hardcoded safety layer. Computes position size and exit prices,
    enforces account-level circuit breakers, and evaluates every tick
    against the active trade plan.

    Rules enforced (priority order, highest first):
    1. Daily / weekly drawdown circuit  -> halt + flatten
    2. Consecutive-loss cool-off        -> halt for COOL_OFF_MINUTES
    3. Hard stop-loss (tick-level)      -> force exit
    4. Trailing stop-loss (tick-level)  -> force exit
    5. Hard take-profit (tick-level)    -> force exit
    6. Trailing profit-taker (tick-level) -> force exit
    7. Cash reserve floor               -> block new BUY
    """

    def __init__(self, cfg: BotConfig, initial_equity: float, notifier=None):
        self.cfg = cfg
        self.notifier = notifier

        self.start_of_day_equity  = initial_equity
        self.start_of_week_equity = initial_equity
        self._halted = False
        self._halt_reason = ""
        self._halt_until_ts: Optional[pd.Timestamp] = None
        self._consecutive_losses = 0

        self.plan: Optional[TradePlan] = None

    # ── Daily/weekly bookkeeping ─────────────────────────────────────────────

    def new_day(self, current_equity: float):
        self.start_of_day_equity = current_equity
        if self._halt_until_ts is None:  # don't clear an active cool-off early
            self._halted = False
        log.info(f"Risk: new trading day. Start equity = ${current_equity:,.2f}")

    def new_week(self, current_equity: float):
        self.start_of_week_equity = current_equity
        log.info(f"Risk: new trading week. Start equity = ${current_equity:,.2f}")

    def record_trade_result(self, pnl_usd: float):
        if pnl_usd < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if self._consecutive_losses >= self.cfg.MAX_CONSECUTIVE_LOSSES:
            reason = (
                f"{self._consecutive_losses} consecutive losses. "
                f"Cooling off for {self.cfg.COOL_OFF_MINUTES_AFTER_HALT} minutes."
            )
            log.warning(f"RISK HALT: {reason}")
            self._halted = True
            self._halt_reason = reason
            self._halt_until_ts = pd.Timestamp.utcnow() + pd.Timedelta(
                minutes=self.cfg.COOL_OFF_MINUTES_AFTER_HALT
            )
            if self.notifier:
                self.notifier.risk_halt(reason)

    def is_halted(self, now_ts: Optional[pd.Timestamp] = None) -> bool:
        if not self._halted:
            return False
        if self._halt_until_ts is not None:
            now_ts = now_ts or pd.Timestamp.utcnow()
            if now_ts >= self._halt_until_ts:
                log.info("Risk: cool-off period elapsed. Trading re-armed.")
                self._halted = False
                self._halt_until_ts = None
                return False
        return True

    # ── Position sizing & entry plan ─────────────────────────────────────────

    def compute_trade_plan(self, equity: float, cash: float, entry_price: float,
                            atr: float, momentum_score: float = 0.0) -> Optional[TradePlan]:
        """
        Compute share quantity and stop/target prices for a new LONG entry.
        Returns None if no valid trade can be sized (e.g. stop too wide
        for available risk budget, or insufficient cash).
        """
        if entry_price <= 0 or atr <= 0:
            return None

        sizing_mode = getattr(self.cfg, "SIZING_MODE", "risk_based")
        risk_usd = self.cfg.risk_amount_usd(equity)

        if sizing_mode == "full_cash":
            # Deploy max possible cash to capture momentum waves
            # Use explicit order size from config, or max available cash if not set
            order_size_usd = self.cfg.FULL_CASH_ORDER_SIZE_USD or cash
            if order_size_usd <= 0:
                log.debug("Risk: trade plan rejected in full_cash mode — order size <= 0")
                return None

            # Calculate shares based on explicit order size
            shares = float(np.floor(order_size_usd / entry_price))

            if shares < 1:
                log.debug(f"Risk: trade plan rejected in full_cash mode — sized to <1 share (order_size_usd=${order_size_usd:.2f})")
                return None

            # Dynamically set stop distance to match exact risk budget (e.g., $50) with this full sizing
            # Ensure risk_usd is not 0 to avoid division by zero
            if risk_usd <= 0:
                log.warning("Risk: risk_usd is zero, cannot calculate stop distance in full_cash mode.")
                return None

            stop_distance_raw = risk_usd / shares
            # Enforce safety boundaries so we don't end up with an absurdly tight or wide stop
            min_dist = entry_price * self.cfg.MIN_STOP_DISTANCE_PCT
            max_dist = entry_price * self.cfg.MAX_STOP_DISTANCE_PCT
            stop_distance = float(np.clip(stop_distance_raw, min_dist, max_dist))
            stop_price = entry_price - stop_distance

            if stop_distance != stop_distance_raw:
                log.warning(
                    f"Risk: full_cash stop_distance of ${stop_distance_raw:.2f} was clamped to "
                    f"${stop_distance:.2f} (entry={entry_price}, min_pct={self.cfg.MIN_STOP_DISTANCE_PCT}, "
                    f"max_pct={self.cfg.MAX_STOP_DISTANCE_PCT}). Actual risk may differ from target."
                )

        else: # risk_based mode
            # Traditional volatility-based stop distance first, then size positions from that
            stop_distance = atr * self.cfg.STOP_ATR_MULTIPLIER
            min_dist = entry_price * self.cfg.MIN_STOP_DISTANCE_PCT
            max_dist = entry_price * self.cfg.MAX_STOP_DISTANCE_PCT
            stop_distance = float(np.clip(stop_distance, min_dist, max_dist))
            stop_price = entry_price - stop_distance

            shares_from_risk = risk_usd / stop_distance
            max_shares_by_cash = (cash * self.cfg.DEFAULT_MAX_POSITION_PCT) / entry_price
            shares = min(shares_from_risk, max_shares_by_cash, self.cfg.MAX_SHARES_PER_TRADE)
            shares = float(np.floor(shares))
            order_size_usd = shares * entry_price

            if shares < 1:
                log.debug(
                    "Risk: trade plan rejected in risk_based mode — sized to <1 share "
                    f"(risk_usd=${risk_usd:.2f}, stop_dist=${stop_distance:.4f}, "
                    f"cash=${cash:.2f})"
                )
                return None

        # ── Predictive take-profit: ATR target, nudged by momentum ──────────
        tp_distance = atr * self.cfg.TAKE_PROFIT_ATR_MULTIPLIER
        # momentum_score in [-1,1]; positive momentum (favourable) extends target up to +25%
        tp_distance *= (1.0 + 0.25 * max(0.0, momentum_score))
        min_tp_distance = stop_distance * self.cfg.MIN_REWARD_RISK_RATIO
        tp_distance = max(tp_distance, min_tp_distance)
        take_profit_price = entry_price + tp_distance

        actual_risk_usd = shares * stop_distance

        plan = TradePlan(
            side="LONG",
            entry_price=entry_price,
            shares=shares,
            initial_stop_price=round(stop_price, 4),
            take_profit_price=round(take_profit_price, 4),
            risk_usd=round(actual_risk_usd, 2),
            atr_at_entry=atr,
        )

        sizing_info = f"Sizing Mode: {sizing_mode.upper()} | Order Size: ${order_size_usd:.2f} (Configured)" if sizing_mode == "full_cash" and self.cfg.FULL_CASH_ORDER_SIZE_USD else f"Sizing Mode: {sizing_mode.upper()} | Max Position Pct: {self.cfg.DEFAULT_MAX_POSITION_PCT:.0%}"

        log.info(
            f"Trade plan: {shares:.0f} sh @ ${entry_price:.2f} | "
            f"Stop ${plan.initial_stop_price:.2f} (-{stop_distance/entry_price:.2%}) | "
            f"Target ${plan.take_profit_price:.2f} (+{tp_distance/entry_price:.2%}) | "
            f"Risking ${actual_risk_usd:.2f} | "
            f"{sizing_info}"
        )
        return plan

    def open_position(self, plan: TradePlan):
        self.plan = plan

    def close_position(self):
        self.plan = None

    # ── Tick-by-tick exit evaluation ─────────────────────────────────────────

    def update_ai_dynamic_trailing(self,
                                   ai_confidence: float = 0.5,
                                   regime_trend_strength: float = 0.0,
                                   regime_label: str = "unknown",
                                   observation: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """
        Apply AI-driven dynamic adjustments to the active trade plan's
        trailing-profit and early-loss parameters.

        The AI itself does not override the ultimate max-loss ($50). Instead
        it influences how tight/loose the trailing-profit floor and
        early-loss threshold are, by reading:
          - AI confidence
          - Market regime (trend strength, direction)
          - Recent feature geometry from the observation vector

        Args:
            ai_confidence: PPO/ensemble confidence in [0, 1]
            regime_trend_strength: ADX-like trend strength in [0, 100]
            regime_label: regime name
            observation: latest 422-dim observation vector (optional)

        Returns:
            Dict of applied parameter overrides for this bar/tick window.
        """
        overrides: Dict[str, Any] = {
            "trailing_profit_giveback_pct": self.cfg.TRAILING_PROFIT_GIVEBACK_PCT,
            "early_loss_exit_threshold_usd": None,
        }

        if not (getattr(self.cfg, "DYNAMIC_TRAILING_ENABLED", False) and self.plan is not None):
            return overrides

        # ── 1. Dynamic trailing-profit giveback ────────────────────────────
        # Strong trend + high confidence = let winners run more
        # Weak trend + high volatility = tighten giveback (lock gains faster)
        giveback = float(self.cfg.TRAILING_PROFIT_GIVEBACK_PCT)
        if getattr(self.cfg, "DYNAMIC_PROFIT_GIVEBACK_MAX", None) is not None:
            giveback = float(np.clip(
                giveback,
                getattr(self.cfg, "DYNAMIC_PROFIT_GIVEBACK_MIN", 0.2),
                getattr(self.cfg, "DYNAMIC_PROFIT_GIVEBACK_MAX", 0.5),
            ))

        # Baseline: moderate giveback when confidence is middling
        confidence_factor = float(np.clip(ai_confidence, 0.0, 1.0))
        # High trend strength + high confidence -> wider giveback
        trend_bonus = 0.0
        if regime_trend_strength > 30:
            trend_bonus += 0.05
        if regime_trend_strength > 50:
            trend_bonus += 0.05
        if regime_label in ("trending_up", "trending_down", "high_volatility"):
            trend_bonus += 0.05

        # Calm/ranging/mixed -> tighten (protect gains)
        if regime_label in ("ranging", "low_volatility", "unknown"):
            trend_bonus -= 0.10

        giveback = float(np.clip(
            giveback + confidence_factor * 0.20 + trend_bonus,
            getattr(self.cfg, "DYNAMIC_PROFIT_GIVEBACK_MIN", 0.2),
            getattr(self.cfg, "DYNAMIC_PROFIT_GIVEBACK_MAX", 0.5),
        ))

        # Final safety clamp: never exceed original configured max
        if hasattr(self.cfg, "TRAILING_PROFIT_GIVEBACK_PCT"):
            giveback = min(giveback, float(self.cfg.TRAILING_PROFIT_GIVEBACK_PCT))

        overrides["trailing_profit_giveback_pct"] = giveback

        if self.plan.trailing_profit_armed and self.plan.profit_floor_price is not None:
            # Recompute the floor using the dynamic giveback
            new_floor = self.plan.entry_price + (
                (self.plan.peak_price - self.plan.entry_price) * (1.0 - giveback)
            )
            self.plan.profit_floor_price = max(self.plan.profit_floor_price, new_floor)
            overrides["profit_floor_price"] = round(self.plan.profit_floor_price, 4)

        # ── 2. Early loss exit (pre-stop) ─────────────────────────────────
        # Exit earlier than the hard stop when AI + regime agree this trade
        # is degrading. The hard $50 cap in risk.py still caps the *actual*
        # realized loss at trade close / stop fill.
        if getattr(self.cfg, "EARLY_LOSS_EXIT_ENABLED", False):
            if self.plan.risk_usd > 0:
                # Use observation features to detect early degradation
                early_penalty = 0.0
                if observation is not None and len(observation) >= self.cfg.N_FEATURES:
                    features = observation[: self.cfg.N_FEATURES]
                    # Index mapping from features_enhanced.py:
                    # 0 log_return, 1 volatility_10, 2 rsi_14, 3 macd_signal,
                    # 4 bb_pct, 5 volume_z, 6 volume_accel, 7 price_momentum_5,
                    # 8 vwap_deviation, 9 atr_norm, 10 obv_z, 11 trend_strength,
                    # 12 mean_reversion_z, 13 realized_vol_ratio
                    vol_expanding = features[13] > 1.0 if len(features) > 13 else False
                    trend_weak = features[11] < 0.2 if len(features) > 11 else False
                    momentum_bearish = (features[0] < -0.002) if len(features) > 0 else False
                    if vol_expanding:
                        early_penalty += 0.05
                    if trend_weak:
                        early_penalty += 0.05
                    if momentum_bearish:
                        early_penalty += 0.05

                early_threshold = getattr(self.cfg, "EARLY_LOSS_RISK_PCT_THRESHOLD", 0.30)
                effective_threshold = float(np.clip(early_threshold + early_penalty, 0.15, 0.50))
                overrides["early_loss_exit_threshold_pct"] = round(effective_threshold, 3)

        return overrides

    def evaluate_tick(self, price: float) -> Tuple[bool, str]:
        """
        Call on every tick while a position is open. Returns
        (should_exit, reason) where reason is one of:
        "hard_stop", "trailing_stop", "hard_take_profit", "trailing_profit", ""
        """
        if self.plan is None or price <= 0:
            return False, ""

        plan = self.plan

        if price > plan.peak_price:
            plan.peak_price = price

        # ── Trailing stop-loss: arms once in sufficient profit, then ratchets ──
        if self.cfg.TRAILING_STOP_ENABLED:
            gain_pct = (price - plan.entry_price) / plan.entry_price
            if not plan.trailing_stop_armed and gain_pct >= self.cfg.TRAILING_STOP_ACTIVATE_PCT:
                plan.trailing_stop_armed = True
                log.info(f"Trailing stop armed at +{gain_pct:.2%}")

            if plan.trailing_stop_armed:
                trail_dist = plan.atr_at_entry * self.cfg.TRAILING_STOP_ATR_MULTIPLIER
                trail_dist = max(trail_dist, plan.entry_price * self.cfg.MIN_STOP_DISTANCE_PCT)
                new_trail_stop = plan.peak_price - trail_dist
                if new_trail_stop > plan.current_stop_price:
                    plan.current_stop_price = new_trail_stop

        # ── Early loss exit (pre-stop) ────────────────────────────────────
        # Only trigger when the AI+regime logic flags a degrading setup.
        # The AI does not choose size; it only asks for an earlier exit.
        early_exit = False
        if getattr(self.cfg, "EARLY_LOSS_EXIT_ENABLED", False) and getattr(self, "_early_loss_threshold_pct", None) is not None:
            loss_per_share = price - plan.entry_price
            unrealised_loss_usd = loss_per_share * plan.shares
            if unrealised_loss_usd < 0:
                loss_pct_of_risk = abs(unrealised_loss_usd) / (plan.risk_usd + 1e-9)
                if loss_pct_of_risk >= float(self._early_loss_threshold_pct):
                    early_exit = True
                    reason_early = "early_loss_exit"
                    # Do not allow early exit to breach the hard $50 max loss.
                    # risk.py owns the hard failure state; early exit is an early warning only.
                    log.warning(
                        f"Early loss exit triggered: loss ${abs(unrealised_loss_usd):.2f} "
                        f"= {loss_pct_of_risk:.0%} of risk budget ${plan.risk_usd:.2f}"
                    )
                    return True, reason_early

        # ── Hard / trailing stop check (current_stop_price covers both) ────────
        if price <= plan.current_stop_price:
            reason = "trailing_stop" if plan.trailing_stop_armed else "hard_stop"
            return True, reason

        # ── Hard take-profit ─────────────────────────────────────────────────
        if price >= plan.take_profit_price and not self.cfg.TRAILING_PROFIT_ENABLED:
            return True, "hard_take_profit"

        # ── Trailing profit-taker ────────────────────────────────────────────
        if self.cfg.TRAILING_PROFIT_ENABLED:
            giveback = getattr(
                self,
                "_dynamic_profit_giveback_pct",
                getattr(self.cfg, "TRAILING_PROFIT_GIVEBACK_PCT", 0.40),
            )
            gain_pct = (plan.peak_price - plan.entry_price) / plan.entry_price
            if not plan.trailing_profit_armed and gain_pct >= self.cfg.TRAILING_PROFIT_ACTIVATE_PCT:
                plan.trailing_profit_armed = True
                plan.profit_floor_price = plan.entry_price + (
                    (plan.peak_price - plan.entry_price) * (1 - giveback)
                )
                log.info(f"Trailing profit armed at +{gain_pct:.2%}, floor ${plan.profit_floor_price:.2f} (giveback {giveback:.0%})")

            if plan.trailing_profit_armed:
                new_floor = plan.entry_price + (
                    (plan.peak_price - plan.entry_price) * (1 - giveback)
                )
                if plan.profit_floor_price is None or new_floor > plan.profit_floor_price:
                    plan.profit_floor_price = new_floor

                if price <= plan.profit_floor_price:
                    return True, "trailing_profit"

            # still respect the hard take-profit as an absolute ceiling exit
            if price >= plan.take_profit_price * 1.15:
                # price ran 15% past the original predictive target without
                # the trailing-profit logic catching it (gap move) — take it
                return True, "hard_take_profit"

        return False, ""

    # ── Action-level validation (called once per decision bar, not per tick) ──

    def validate_action(self, action: int, equity: float, cash: float,
                         shares: float, now_ts: Optional[pd.Timestamp] = None) -> int:
        """
        Validate the PPO agent's proposed HOLD/BUY/SELL against
        account-level circuit breakers. Tick-level stop/target exits are
        handled separately by evaluate_tick() since they must react
        faster than once per decision bar.
        """
        daily_dd = (self.start_of_day_equity - equity) / (self.start_of_day_equity + 1e-9)
        if daily_dd > self.cfg.MAX_DAILY_LOSS_PCT:
            reason = f"Daily drawdown {daily_dd:.1%} exceeds limit {self.cfg.MAX_DAILY_LOSS_PCT:.1%}"
            if not self._halted:
                log.warning(f"RISK HALT: {reason}")
                self._halted = True
                self._halt_reason = reason
                if self.notifier:
                    self.notifier.risk_halt(reason)
            return 2 if shares > 0 else 0

        weekly_dd = (self.start_of_week_equity - equity) / (self.start_of_week_equity + 1e-9)
        if weekly_dd > self.cfg.MAX_WEEKLY_LOSS_PCT:
            reason = f"Weekly drawdown {weekly_dd:.1%} exceeds limit {self.cfg.MAX_WEEKLY_LOSS_PCT:.1%}"
            if not self._halted:
                log.warning(f"RISK HALT: {reason}")
                self._halted = True
                self._halt_reason = reason
                if self.notifier:
                    self.notifier.risk_halt(reason)
            return 2 if shares > 0 else 0

        if self.is_halted(now_ts):
            return 0

        if action == 1:  # BUY
            min_reserve = equity * self.cfg.MIN_CASH_RESERVE_PCT
            if cash <= min_reserve:
                log.debug("RISK: Cash below reserve — blocking BUY -> HOLD")
                return 0
            if shares > 0:
                return 0  # already in a position; MAX_CONCURRENT_POSITIONS=1

        return action
