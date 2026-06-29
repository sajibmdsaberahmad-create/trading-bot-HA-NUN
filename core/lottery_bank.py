#!/usr/bin/env python3
"""
core/lottery_bank.py — Virtual $1k lottery bank for paper/replay learning.

Paper IB still uses the full account for orders. When a setup qualifies as
commander "calculated lottery" (80–97% conviction), sizing is capped to this
virtual bank so Halim/PPO learn what a real $1k cash account could do — without
the once-per-day settlement limit on paper.

State persists in models/lottery_bank_state.json + lottery_bank_ledger.jsonl.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig
from core.notify import log

_REPO = Path(__file__).resolve().parents[1]
STATE_PATH = _REPO / "models" / "lottery_bank_state.json"
LEDGER_PATH = _REPO / "models" / "lottery_bank_ledger.jsonl"
REPORT_PATH = _REPO / "models" / "lottery_bank_daily.jsonl"


def lottery_bank_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    env = os.getenv("LOTTERY_BANK_ENABLED", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    # Paper + replay only — live $1k cash uses real IB balance
    if not bool(getattr(cfg, "PAPER_TRADING", True)):
        return False
    return bool(getattr(cfg, "LOTTERY_BANK_ENABLED", True))


def lottery_bank_initial(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    return float(getattr(cfg, "LOTTERY_BANK_INITIAL_USD", 1000.0))


def _default_state(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    initial = lottery_bank_initial(cfg)
    return {
        "initial_usd": initial,
        "cash": initial,
        "nav": initial,
        "peak_nav": initial,
        "total_pnl_usd": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_streak": 0,
        "loss_streak": 0,
        "open": None,
        "last_reset": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def load_state(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    if STATE_PATH.is_file():
        try:
            with open(STATE_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("nav") is not None:
                return data
        except Exception:
            pass
    return _default_state(cfg)


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _append_ledger(row: Dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def ensure_lottery_bank(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Idempotent startup — create state file if missing."""
    cfg = cfg or BotConfig()
    if not lottery_bank_enabled(cfg):
        return {"ok": False, "reason": "disabled"}
    state = load_state(cfg)
    if not STATE_PATH.is_file():
        save_state(state)
        log.info(
            f"🎰 Lottery bank ready — ${state['nav']:,.2f} virtual "
            f"(calculated lottery sizing on paper/replay)"
        )
    return {"ok": True, "nav": state.get("nav"), "cash": state.get("cash")}


@dataclass
class LotteryAssessment:
    eligible: bool
    conviction: float
    tier: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lottery_bank": self.eligible,
            "lottery_conviction": self.conviction,
            "lottery_tier": self.tier,
            "lottery_reason": self.reason,
        }


def assess_lottery_setup(
    cfg: Optional[BotConfig],
    *,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
    forecast: Optional[Dict[str, Any]] = None,
) -> LotteryAssessment:
    """True when setup meets commander calculated-lottery floors (80%+)."""
    cfg = cfg or BotConfig()
    if not lottery_bank_enabled(cfg):
        return LotteryAssessment(False, 0.0, "", "lottery bank disabled")

    forecast = forecast or {}
    profit_prob = float(forecast.get("profit_probability", 0) or 0)
    fakeout = float(forecast.get("fakeout_risk", forecast.get("fakeout", 0.5)) or 0.5)

    floors: Dict[str, float] = {}
    try:
        from core.commander_runtime import commander_entry_floors, commander_runtime_enabled
        if commander_runtime_enabled(cfg):
            floors = commander_entry_floors(cfg)
    except Exception:
        floors = {
            "min_profit_probability": 0.80,
            "min_spike_ratio": 2.0,
            "min_scan_score": 70.0,
            "max_fakeout_risk": 0.25,
        }

    min_prob = floors.get("min_profit_probability", 0.80)
    min_spike = floors.get("min_spike_ratio", 2.0)
    min_scan = floors.get("min_scan_score", 70.0)
    max_fake = floors.get("max_fakeout_risk", 0.25)

    # Tier mapping (commander doctrine)
    if (
        spike_ratio >= 2.8 and scan_score >= 82
        and profit_prob >= 0.90 and fakeout <= 0.18
    ):
        tier, conv = "97", 0.97
    elif (
        spike_ratio >= 2.4 and scan_score >= 78
        and profit_prob >= 0.85 and fakeout <= 0.22
    ):
        tier, conv = "90", 0.90
    elif (
        spike_ratio >= min_spike and scan_score >= min_scan
        and profit_prob >= min_prob and fakeout <= max_fake
    ):
        tier, conv = "80", max(0.80, profit_prob)
    else:
        reasons = []
        if spike_ratio < min_spike:
            reasons.append(f"spike {spike_ratio:.2f}x<{min_spike:.1f}x")
        if scan_score < min_scan:
            reasons.append(f"scan {scan_score:.0f}<{min_scan:.0f}")
        if profit_prob < min_prob:
            reasons.append(f"prob {profit_prob:.0%}<{min_prob:.0%}")
        if fakeout > max_fake:
            reasons.append(f"fakeout {fakeout:.0%}>{max_fake:.0%}")
        return LotteryAssessment(
            False, profit_prob, "",
            "below lottery floor: " + ", ".join(reasons) if reasons else "weak setup",
        )

    return LotteryAssessment(
        True, conv, tier,
        f"calculated lottery tier {tier}% — spike={spike_ratio:.2f}x scan={scan_score:.0f} "
        f"prob={profit_prob:.0%}",
    )


def rescale_entry_for_lottery_bank(
    cfg: BotConfig,
    decision: Dict[str, Any],
    entry_px: float,
    assessment: LotteryAssessment,
) -> Dict[str, Any]:
    """Cap shares/risk to virtual lottery bank NAV (paper learns on $1k)."""
    if not assessment.eligible or entry_px <= 0:
        return decision

    state = load_state(cfg)
    equity = float(state.get("nav", lottery_bank_initial(cfg)))
    cash = float(state.get("cash", equity))
    fee = float(getattr(cfg, "TRANSACTION_COST_PCT", 0.001))

    risk_usd = cfg.risk_amount_usd(equity)
    max_deploy = min(cash * (1.0 - fee), equity * float(getattr(cfg, "DEFAULT_MAX_POSITION_PCT", 0.95)))
    max_shares_deploy = int(max_deploy / entry_px) if entry_px > 0 else 0

    stop = float(decision.get("stop", entry_px * 0.97))
    stop_dist = max(entry_px - stop, entry_px * float(getattr(cfg, "MIN_STOP_DISTANCE_PCT", 0.003)))
    max_shares_risk = int(risk_usd / stop_dist) if stop_dist > 0 else 0

    shares = int(decision.get("shares", 0) or 0)
    shares = min(shares, max_shares_deploy, max_shares_risk, int(getattr(cfg, "MAX_SHARES_PER_TRADE", 999999)))
    shares = max(0, shares)

    out = {
        **decision,
        "shares": shares,
        "risk_usd": min(risk_usd, shares * stop_dist),
        **assessment.to_dict(),
    }
    if shares < int(decision.get("shares", 0) or 0):
        log.info(
            f"  🎰 Lottery bank sizing: {shares} sh @ ${entry_px:.2f} "
            f"(bank NAV ${equity:,.2f} | tier {assessment.tier}%)"
        )
    return out


def record_entry(
    cfg: BotConfig,
    *,
    ticker: str,
    shares: float,
    fill_px: float,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Virtual debit when a lottery-bank trade opens."""
    if not lottery_bank_enabled(cfg):
        return {}
    state = load_state(cfg)
    fee = float(getattr(cfg, "TRANSACTION_COST_PCT", 0.001))
    cost = float(shares) * float(fill_px) * (1 + fee)
    cash = float(state.get("cash", 0))
    if cost > cash + 1e-6:
        log.warning(f"🎰 Lottery bank over-deploy ${cost:.2f} > cash ${cash:.2f} — clamping")
        cost = min(cost, cash)

    state["cash"] = round(cash - cost, 2)
    state["open"] = {
        "ticker": ticker.upper(),
        "shares": float(shares),
        "entry_px": float(fill_px),
        "cost_usd": round(cost, 2),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        **(meta or {}),
    }
    save_state(state)

    row = {
        "event": "lottery_entry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker.upper(),
        "shares": float(shares),
        "fill_px": float(fill_px),
        "cost_usd": round(cost, 2),
        "bank_cash": state["cash"],
        "bank_nav": state["cash"],
        **(meta or {}),
    }
    _append_ledger(row)
    return row


def record_exit(
    cfg: BotConfig,
    *,
    ticker: str,
    shares: float,
    entry_px: float,
    exit_px: float,
    pnl_usd: float,
    pnl_pct: float,
    reason: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Virtual credit + stats when a lottery-bank trade closes."""
    if not lottery_bank_enabled(cfg):
        return {}

    state = load_state(cfg)
    fee = float(getattr(cfg, "TRANSACTION_COST_PCT", 0.001))
    proceeds = float(shares) * float(exit_px) * (1 - fee)
    state["cash"] = round(float(state.get("cash", 0)) + proceeds, 2)
    state["nav"] = state["cash"]
    state["peak_nav"] = max(float(state.get("peak_nav", state["nav"])), state["nav"])
    state["total_pnl_usd"] = round(float(state.get("total_pnl_usd", 0)) + pnl_usd, 2)
    state["trades"] = int(state.get("trades", 0)) + 1
    win = pnl_usd > 0
    if win:
        state["wins"] = int(state.get("wins", 0)) + 1
        state["win_streak"] = int(state.get("win_streak", 0)) + 1
        state["loss_streak"] = 0
    else:
        state["losses"] = int(state.get("losses", 0)) + 1
        state["loss_streak"] = int(state.get("loss_streak", 0)) + 1
        state["win_streak"] = 0
    state["open"] = None
    save_state(state)

    initial = float(state.get("initial_usd", lottery_bank_initial(cfg)))
    return_pct = (state["nav"] / initial - 1.0) * 100.0 if initial > 0 else 0.0

    row = {
        "event": "lottery_exit",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker.upper(),
        "shares": float(shares),
        "entry_px": float(entry_px),
        "exit_px": float(exit_px),
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 4),
        "result": "win" if win else "loss",
        "bank_nav": state["nav"],
        "bank_return_pct": round(return_pct, 2),
        "reason": (reason or "")[:200],
        **(meta or {}),
    }
    _append_ledger(row)
    _append_daily_snapshot(state, row)
    return row


def _append_daily_snapshot(state: Dict[str, Any], last_exit: Dict[str, Any]) -> None:
    if not bool(os.getenv("LOTTERY_BANK_REPORT_ENABLED", "true").lower() in ("1", "true", "yes")):
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = {
        "day": day,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nav": state.get("nav"),
        "total_pnl_usd": state.get("total_pnl_usd"),
        "trades": state.get("trades"),
        "wins": state.get("wins"),
        "losses": state.get("losses"),
        "last_ticker": last_exit.get("ticker"),
        "last_pnl_usd": last_exit.get("pnl_usd"),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, separators=(",", ":")) + "\n")


def format_status(cfg: Optional[BotConfig] = None) -> str:
    cfg = cfg or BotConfig()
    state = load_state(cfg)
    initial = float(state.get("initial_usd", lottery_bank_initial(cfg)))
    nav = float(state.get("nav", initial))
    ret = (nav / initial - 1.0) * 100.0 if initial > 0 else 0.0
    trades = int(state.get("trades", 0))
    wins = int(state.get("wins", 0))
    wr = wins / max(trades, 1) * 100.0
    open_pos = state.get("open") or {}
    open_line = ""
    if open_pos:
        open_line = f"\nOpen: {open_pos.get('ticker')} {open_pos.get('shares')}sh @ ${open_pos.get('entry_px')}"
    return (
        f"🎰 LOTTERY BANK (virtual ${initial:,.0f})\n"
        f"NAV ${nav:,.2f} ({ret:+.2f}%) | P&L ${float(state.get('total_pnl_usd', 0)):+,.2f}\n"
        f"Trades {trades} | WR {wr:.0f}% | W/L streak {state.get('win_streak', 0)}/{state.get('loss_streak', 0)}"
        f"{open_line}"
    )


def format_exit_notification(row: Dict[str, Any], state: Optional[Dict[str, Any]] = None) -> str:
    state = state or load_state()
    initial = float(state.get("initial_usd", 1000))
    nav = float(state.get("nav", initial))
    ret = (nav / initial - 1.0) * 100.0 if initial > 0 else 0.0
    emoji = "✅" if row.get("result") == "win" else "❌"
    return (
        f"{emoji} LOTTERY BANK {row.get('ticker')}\n"
        f"Trip P&L ${row.get('pnl_usd', 0):+,.2f} ({float(row.get('pnl_pct', 0)) * 100:+.2f}%)\n"
        f"Bank NAV ${nav:,.2f} ({ret:+.2f}% since ${initial:,.0f})\n"
        f"Session: {state.get('trades', 0)} trips | "
        f"total ${float(state.get('total_pnl_usd', 0)):+,.2f}"
    )


def notify_lottery_event(
    notifier: Any,
    cfg: BotConfig,
    event: str,
    row: Dict[str, Any],
) -> None:
    if not bool(getattr(cfg, "LOTTERY_BANK_NOTIFY", True)):
        return
    if notifier is None:
        return
    try:
        if event == "lottery_exit":
            msg = format_exit_notification(row)
        elif event == "lottery_entry":
            tier = row.get("lottery_tier", "?")
            msg = (
                f"🎰 LOTTERY ENTER {row.get('ticker')} | tier {tier}%\n"
                f"{int(row.get('shares', 0))}sh @ ${float(row.get('fill_px', 0)):.4f} | "
                f"bank cash ${float(row.get('bank_cash', 0)):,.2f}"
            )
        else:
            msg = format_status(cfg)
        notifier.info(msg)
    except Exception as exc:
        log.debug(f"Lottery notify: {exc}")


def on_trade_closed(
    cfg: BotConfig,
    notifier: Any,
    trade_rec: Dict[str, Any],
    slot: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Hook from ScalperRunner after IB-confirmed exit."""
    slot = slot or {}
    if not slot.get("lottery_bank") and not trade_rec.get("lottery_bank"):
        return None
    ticker = str(trade_rec.get("ticker", ""))
    shares = float(trade_rec.get("shares", 0))
    entry_px = float(trade_rec.get("entry_fill") or trade_rec.get("entry", 0))
    exit_px = float(trade_rec.get("exit_fill") or trade_rec.get("exit", 0))
    pnl = float(trade_rec.get("pnl_usd", 0))
    pnl_pct = float(trade_rec.get("pnl_pct", 0))
    meta = {
        "lottery_tier": slot.get("lottery_tier"),
        "lottery_conviction": slot.get("lottery_conviction"),
    }
    row = record_exit(
        cfg,
        ticker=ticker,
        shares=shares,
        entry_px=entry_px,
        exit_px=exit_px,
        pnl_usd=pnl,
        pnl_pct=pnl_pct,
        reason=str(trade_rec.get("exit_reason", "")),
        meta=meta,
    )
    if row:
        notify_lottery_event(notifier, cfg, "lottery_exit", row)
        try:
            from core.experience_buffer import append as buffer_append
            buffer_append({
                "source": "lottery_bank",
                "ticker": ticker,
                "action": "TRADE",
                "pnl_usd": pnl,
                "win": pnl > 0,
                "reward": max(-1.0, min(1.0, pnl / 50.0)),
                "outcome_label": "calculated_lottery_win" if pnl > 0 else "held_too_long",
                "confidence": float(slot.get("lottery_conviction", 0.85)),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
    return row
