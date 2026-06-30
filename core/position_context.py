"""Per-ticker position slot helpers — isolate multi-position monitor state."""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.risk import RiskManager, TradePlan


def slot_entry_price(slot: Dict[str, Any]) -> float:
    fill = float(slot.get("entry_fill_px") or 0)
    planned = float(slot.get("entry_price") or 0)
    return fill if fill > 0 else planned


def bind_risk_plan_for_ticker(
    ticker: str,
    *,
    position_slots: Dict[str, Dict[str, Any]],
    risk_plans: Dict[str, TradePlan],
    risk: RiskManager,
) -> bool:
    """Attach this ticker's risk plan — never leave another symbol's plan active."""
    slot = position_slots.get(ticker)
    if not slot:
        risk.close_position()
        return False
    entry = slot_entry_price(slot)
    if entry <= 0:
        risk.close_position()
        return False
    plan = risk_plans.get(ticker)
    if plan is None or abs(plan.entry_price - entry) / entry > 0.05:
        stop = float(slot.get("stop") or slot.get("hard_floor") or entry * 0.98)
        target = float(slot.get("target") or entry * 1.02)
        sh = float(slot.get("shares") or 0)
        risk_usd = float(slot.get("risk_usd") or abs(entry - stop) * sh)
        atr = float(slot.get("atr_at_entry") or max(entry * 0.01, 0.01))
        plan = TradePlan(
            side="LONG",
            entry_price=entry,
            shares=sh,
            initial_stop_price=float(slot.get("hard_floor") or stop),
            take_profit_price=target,
            risk_usd=risk_usd,
            atr_at_entry=atr,
        )
        plan.peak_price = float(slot.get("peak") or entry)
        plan.current_stop_price = stop
        risk_plans[ticker] = plan
    else:
        plan.peak_price = max(plan.peak_price, float(slot.get("peak") or entry))
        plan.current_stop_price = float(slot.get("stop") or plan.current_stop_price)
    risk.open_position(plan)
    return True


def slot_price_sane(entry_price: float, current_px: float, *, max_dev: float = 0.35) -> bool:
    """Reject cross-ticker or stale quotes before mechanical profit exits."""
    if entry_price <= 0 or current_px <= 0:
        return False
    return abs(current_px / entry_price - 1.0) <= max_dev


def risk_plan_sane_for_tick(
    plan: Optional[TradePlan],
    *,
    entry_price: float,
    shares: float,
    current_px: float,
) -> bool:
    if plan is None or entry_price <= 0 or plan.entry_price <= 0:
        return False
    if abs(plan.entry_price - entry_price) / entry_price > 0.05:
        return False
    if abs(plan.shares - shares) > max(1.0, 0.01 * max(shares, 1.0)):
        return False
    if abs(current_px / entry_price - 1.0) > 0.35:
        return False
    return True
