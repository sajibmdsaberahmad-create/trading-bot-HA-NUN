#!/usr/bin/env python3
"""
core/replay_fill_simulator.py — Stochastic IB-like fills for replay-live training.

Simulates latency, partial fills, adaptive slippage, and intrabar stop/target
ambiguity so PPO learns from realistic P&L — not perfect bar-close shadow fills.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from core.notify import log


@dataclass
class ReplayFillConfig:
    fill_probability: float = 0.93
    partial_fill_probability: float = 0.14
    partial_fill_min_ratio: float = 0.55
    partial_fill_max_ratio: float = 0.94
    slippage_model: str = "adaptive"
    fixed_slippage_pct: float = 0.0018
    max_slippage_pct: float = 0.015
    exit_slippage_mult: float = 1.2
    stop_first_bias: float = 0.58
    latency_bars_probability: float = 0.08
    latency_bars_max: int = 2
    seed: Optional[int] = None

    @classmethod
    def from_env(cls) -> "ReplayFillConfig":
        return cls(
            fill_probability=float(os.getenv("REPLAY_FILL_PROB", "0.93")),
            partial_fill_probability=float(os.getenv("REPLAY_PARTIAL_FILL_PROB", "0.14")),
            partial_fill_min_ratio=float(os.getenv("REPLAY_PARTIAL_MIN_RATIO", "0.55")),
            partial_fill_max_ratio=float(os.getenv("REPLAY_PARTIAL_MAX_RATIO", "0.94")),
            slippage_model=os.getenv("REPLAY_SLIPPAGE_MODEL", "adaptive").strip().lower(),
            fixed_slippage_pct=float(os.getenv("REPLAY_FIXED_SLIPPAGE_PCT", "0.0018")),
            max_slippage_pct=float(os.getenv("REPLAY_MAX_SLIPPAGE_PCT", "0.015")),
            exit_slippage_mult=float(os.getenv("REPLAY_EXIT_SLIPPAGE_MULT", "1.2")),
            stop_first_bias=float(os.getenv("REPLAY_STOP_FIRST_BIAS", "0.58")),
            latency_bars_probability=float(os.getenv("REPLAY_LATENCY_PROB", "0.08")),
            latency_bars_max=int(os.getenv("REPLAY_LATENCY_BARS_MAX", "2")),
            seed=int(os["REPLAY_FILL_SEED"]) if os.getenv("REPLAY_FILL_SEED") else None,
        )


@dataclass
class SimulatedFill:
    status: str
    fill_price: float
    filled_shares: int
    requested_shares: int
    slippage_pct: float
    quote_price: float
    reason: str = ""
    execute_after_bars: int = 0
    partial: bool = False
    rejected: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("filled", "partial") and self.filled_shares > 0 and self.fill_price > 0


@dataclass
class PendingReplayOrder:
    ticker: str
    side: str
    quote_price: float
    shares: int
    bars_remaining: int
    submitted_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)


class ReplayFillSimulator:
    """Random, bar-aware fill engine for replay shadow path."""

    def __init__(self, cfg: Optional[ReplayFillConfig] = None):
        self.cfg = cfg or ReplayFillConfig.from_env()
        self._rng = random.Random(self.cfg.seed or int(time.time() * 1000) % 2_000_000_000)
        self._pending: Dict[str, PendingReplayOrder] = {}
        self._fills_total = 0
        self._rejects = 0
        self._partials = 0

    def stats(self) -> Dict[str, Any]:
        return {
            "fills": self._fills_total,
            "rejects": self._rejects,
            "partials": self._partials,
            "pending": len(self._pending),
        }

    def _slippage_amount(self, base_price: float, side: str, bar: Dict[str, float]) -> float:
        high = float(bar.get("high", base_price))
        low = float(bar.get("low", base_price))
        close = float(bar.get("close", base_price))
        vol = float(bar.get("volume", 0))

        if self.cfg.slippage_model == "fixed":
            slip = base_price * self.cfg.fixed_slippage_pct
        elif self.cfg.slippage_model == "random":
            slip = base_price * (0.0008 + self._rng.random() * 0.009)
        else:
            bar_range = max(high - low, 0.0) / (close + 1e-9)
            vol_noise = min(0.004, max(0.0005, 1.0 / (vol / 5000.0 + 1.0)))
            slip = base_price * max(0.0006, min(self.cfg.max_slippage_pct, bar_range * 0.45 + vol_noise))

        jitter = base_price * (self._rng.random() - 0.5) * 0.0012
        slip = max(0.0, slip + abs(jitter))
        cap = base_price * self.cfg.max_slippage_pct
        return min(slip, cap)

    def _apply_side_slip(self, quote: float, side: str, slip: float, bar: Dict[str, float]) -> float:
        high = float(bar.get("high", quote))
        low = float(bar.get("low", quote))
        if side.upper() in ("BUY", "LONG"):
            px = quote + slip
            px = min(px, high * 1.002)
        else:
            px = quote - slip
            px = max(px, low * 0.998)
        return round(px, 4)

    def maybe_queue_entry(
        self,
        ticker: str,
        quote_price: float,
        shares: int,
        bar: Dict[str, float],
        meta: Optional[Dict[str, Any]] = None,
    ) -> SimulatedFill:
        """Entry with optional 1–2 bar latency before fill attempt."""
        if (
            self.cfg.latency_bars_max > 0
            and self._rng.random() < self.cfg.latency_bars_probability
        ):
            delay = self._rng.randint(1, self.cfg.latency_bars_max)
            self._pending[ticker.upper()] = PendingReplayOrder(
                ticker=ticker.upper(),
                side="BUY",
                quote_price=quote_price,
                shares=shares,
                bars_remaining=delay,
                meta=dict(meta or {}),
            )
            log.info(
                f"  📋 REPLAY order queued {ticker}: {shares}sh @ ${quote_price:.4f} "
                f"— fill in {delay} bar(s)"
            )
            return SimulatedFill(
                status="pending",
                fill_price=0.0,
                filled_shares=0,
                requested_shares=shares,
                slippage_pct=0.0,
                quote_price=quote_price,
                reason=f"latency_{delay}bars",
                execute_after_bars=delay,
            )
        return self.simulate_entry(ticker, quote_price, shares, bar)

    def simulate_entry(
        self,
        ticker: str,
        quote_price: float,
        shares: int,
        bar: Dict[str, float],
    ) -> SimulatedFill:
        if quote_price <= 0 or shares < 1:
            return SimulatedFill(
                status="rejected", fill_price=0.0, filled_shares=0,
                requested_shares=shares, slippage_pct=0.0, quote_price=quote_price,
                reason="invalid_order", rejected=True,
            )

        if self._rng.random() > self.cfg.fill_probability:
            self._rejects += 1
            reason = self._rng.choice(["no_liquidity", "quote_stale", "route_busy"])
            log.warning(
                f"  ❌ REPLAY entry rejected {ticker}: {reason} "
                f"({shares}sh @ ${quote_price:.4f})"
            )
            return SimulatedFill(
                status="rejected", fill_price=0.0, filled_shares=0,
                requested_shares=shares, slippage_pct=0.0, quote_price=quote_price,
                reason=reason, rejected=True,
            )

        slip = self._slippage_amount(quote_price, "BUY", bar)
        fill_px = self._apply_side_slip(quote_price, "BUY", slip, bar)
        slip_pct = (fill_px - quote_price) / quote_price if quote_price > 0 else 0.0

        filled = shares
        partial = False
        if self._rng.random() < self.cfg.partial_fill_probability and shares > 1:
            ratio = self._rng.uniform(
                self.cfg.partial_fill_min_ratio,
                self.cfg.partial_fill_max_ratio,
            )
            filled = max(1, int(shares * ratio))
            partial = filled < shares
            self._partials += 1

        status = "partial" if partial else "filled"
        self._fills_total += 1
        log.info(
            f"  ✅ REPLAY entry fill {ticker}: {filled}/{shares}sh @ ${fill_px:.4f} "
            f"(quote ${quote_price:.4f} slip {slip_pct:+.3%})"
            + (" PARTIAL" if partial else "")
        )
        return SimulatedFill(
            status=status,
            fill_price=fill_px,
            filled_shares=filled,
            requested_shares=shares,
            slippage_pct=round(slip_pct, 6),
            quote_price=quote_price,
            reason="partial_fill" if partial else "filled",
            partial=partial,
        )

    def simulate_exit(
        self,
        ticker: str,
        quote_price: float,
        shares: int,
        bar: Dict[str, float],
        *,
        exit_kind: str = "market",
    ) -> SimulatedFill:
        if quote_price <= 0 or shares < 1:
            return SimulatedFill(
                status="rejected", fill_price=0.0, filled_shares=0,
                requested_shares=shares, slippage_pct=0.0, quote_price=quote_price,
                reason="invalid_exit", rejected=True,
            )

        slip = self._slippage_amount(quote_price, "SELL", bar)
        slip *= self.cfg.exit_slippage_mult
        if exit_kind in ("replay_stop", "shadow_stop", "stop"):
            slip *= 1.0 + self._rng.random() * 0.35

        fill_px = self._apply_side_slip(quote_price, "SELL", slip, bar)
        slip_pct = (fill_px - quote_price) / quote_price if quote_price > 0 else 0.0

        if self._rng.random() > min(0.99, self.cfg.fill_probability + 0.04):
            self._rejects += 1
            fill_px = self._apply_side_slip(quote_price, "SELL", slip * 1.5, bar)
            slip_pct = (fill_px - quote_price) / quote_price if quote_price > 0 else 0.0

        self._fills_total += 1
        log.info(
            f"  ✅ REPLAY exit fill {ticker}: {shares}sh @ ${fill_px:.4f} "
            f"(quote ${quote_price:.4f} slip {slip_pct:+.3%} | {exit_kind})"
        )
        return SimulatedFill(
            status="filled",
            fill_price=fill_px,
            filled_shares=shares,
            requested_shares=shares,
            slippage_pct=round(slip_pct, 6),
            quote_price=quote_price,
            reason=exit_kind,
        )

    def resolve_intrabar_trigger(
        self,
        bar: Dict[str, float],
        stop: float,
        target: float,
    ) -> Tuple[Optional[str], Optional[float]]:
        """Return (reason, quote_exit) when stop/target touched inside bar range."""
        low = float(bar.get("low", 0))
        high = float(bar.get("high", 0))
        if low <= 0 or high <= 0:
            return None, None

        stop_hit = stop > 0 and low <= stop
        target_hit = target > 0 and high >= target
        if stop_hit and target_hit:
            if self._rng.random() < self.cfg.stop_first_bias:
                return "replay_stop", stop
            return "replay_target", target
        if stop_hit:
            return "replay_stop", stop
        if target_hit:
            return "replay_target", target
        return None, None

    def advance_pending(self, ticker: str, bar: Dict[str, float]) -> Optional[SimulatedFill]:
        """Tick pending orders — returns fill when latency elapsed."""
        key = ticker.upper()
        order = self._pending.get(key)
        if not order:
            return None
        order.bars_remaining -= 1
        if order.bars_remaining > 0:
            return None
        del self._pending[key]
        fill = self.simulate_entry(
            order.ticker, order.quote_price, order.shares, bar,
        )
        fill.meta = order.meta
        return fill

    def cancel_pending(self, ticker: str) -> None:
        self._pending.pop(ticker.upper(), None)

    def bar_dict_from_row(self, row: Any) -> Dict[str, float]:
        return {
            "open": float(row.get("open", row.get("close", 0))),
            "high": float(row.get("high", row.get("close", 0))),
            "low": float(row.get("low", row.get("close", 0))),
            "close": float(row.get("close", 0)),
            "volume": float(row.get("volume", 0)),
        }
