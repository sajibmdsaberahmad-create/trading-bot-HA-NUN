#!/usr/bin/env python3
"""
core/war_account.py — Economic truth layer: virtual war ledger for paper + live.

The bot believes ONLY this ledger for sizing, settlement, fees, and mode — never
raw IB paper ~$900k. Modes: WAR_ACTIVE | LAB_ACTIVE | OBSERVE | LIVE_WAR.

Paper: war $1k + optional lab pool when war is T+1 dry (experience, not promotion).
Live: same rules; operating capital from WAR_LIVE_OPERATING_CAPITAL (not full IB NAV).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

_REPO = Path(__file__).resolve().parents[1]
STATE_PATH = _REPO / "models" / "war_account_state.json"
LEDGER_PATH = _REPO / "models" / "war_account_ledger.jsonl"

_MODES_WAR_ENTRY = frozenset({"WAR_ACTIVE", "LIVE_WAR"})
_MODES_LAB_ENTRY = frozenset({"LAB_ACTIVE"})


def war_account_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    env = os.getenv("WAR_ACCOUNT_ENABLED", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    return bool(getattr(cfg, "WAR_ACCOUNT_ENABLED", True))


def is_live_war(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    return not bool(getattr(cfg, "PAPER_TRADING", True))


def sniper_mode(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    return os.getenv("WAR_SNIPER_MODE", "true").lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def operating_capital_usd(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    if is_live_war(cfg):
        v = _env_float(
            "WAR_LIVE_OPERATING_CAPITAL",
            float(getattr(cfg, "WAR_LIVE_OPERATING_CAPITAL", 0) or 0),
        )
        if v > 0:
            return v
        return float(getattr(cfg, "INITIAL_CASH", 1000.0))
    return _env_float("WAR_CAPITAL_USD", float(getattr(cfg, "WAR_CAPITAL_USD", 1000.0)))


def lab_capital_usd(cfg: Optional[BotConfig] = None) -> float:
    return _env_float("WAR_LAB_CAPITAL_USD", float(getattr(cfg, "WAR_LAB_CAPITAL_USD", 2500.0)))


def lab_enabled(cfg: Optional[BotConfig] = None) -> bool:
    if not war_account_enabled(cfg):
        return False
    return os.getenv("WAR_LAB_ENABLED", "true").lower() in ("1", "true", "yes")


def _today_key() -> str:
    try:
        from core.market_hours import MARKET_TZ
        return datetime.now(MARKET_TZ).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _default_state(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    cap = operating_capital_usd(cfg)
    lab = lab_capital_usd(cfg) if lab_enabled(cfg) else 0.0
    live = is_live_war(cfg)
    return {
        "operating_capital": cap,
        "nav": cap,
        "cash": cap,
        "settled_cash": cap,
        "unsettled": [],
        "deployed_usd": 0.0,
        "bullets_total": _env_int("WAR_BULLETS", 5),
        "bullets_used_session": 0,
        "round_trips_today": 0,
        "entries_today": 0,
        "fee_drag_today": 0.0,
        "lab_capital": lab,
        "lab_nav": lab,
        "lab_cash": lab,
        "lab_settled": lab,
        "lab_round_trips_today": 0,
        "mode": "LIVE_WAR" if live else "WAR_ACTIVE",
        "is_live": live,
        "session_date": _today_key(),
        "open_war": None,
        "open_lab": None,
        "session_pnl_war": 0.0,
        "session_pnl_lab": 0.0,
        "ticker_session_pnl": {},
        "ticker_session_losses": {},
        "updated_at": time.time(),
    }


def load_state(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    if STATE_PATH.is_file():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("nav") is not None:
                return data
        except Exception:
            pass
    return _default_state(cfg)


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = time.time()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _append_ledger(row: Dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def _roll_session(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> None:
    today = _today_key()
    if state.get("session_date") == today:
        return
    cap = operating_capital_usd(cfg)
    lab = lab_capital_usd(cfg) if lab_enabled(cfg) else 0.0
    state.update({
        "session_date": today,
        "round_trips_today": 0,
        "entries_today": 0,
        "fee_drag_today": 0.0,
        "lab_round_trips_today": 0,
        "bullets_used_session": 0,
        "session_pnl_war": 0.0,
        "session_pnl_lab": 0.0,
        "ticker_session_pnl": {},
        "ticker_session_losses": {},
    })
    if not is_live_war(cfg):
        state["nav"] = cap
        state["cash"] = cap
        state["settled_cash"] = cap
        state["unsettled"] = []
        state["deployed_usd"] = 0.0
        if lab > 0:
            state["lab_nav"] = lab
            state["lab_cash"] = lab
            state["lab_settled"] = lab


def _apply_settlement(state: Dict[str, Any]) -> None:
    now = time.time()
    pending: List[Dict[str, Any]] = []
    released = 0.0
    for row in state.get("unsettled") or []:
        ts = float(row.get("settles_at_ts", 0))
        if ts > 0 and now >= ts:
            released += float(row.get("amount", 0))
        else:
            pending.append(row)
    if released > 0:
        state["settled_cash"] = float(state.get("settled_cash", 0)) + released
        state["cash"] = float(state.get("cash", 0)) + released
    state["unsettled"] = pending


def _schedule_settlement(state: Dict[str, Any], amount: float, cfg: Optional[BotConfig] = None) -> None:
    days = _env_int("WAR_SETTLEMENT_DAYS", 1)
    settle_ts = time.time() + max(1, days) * 86400
    if is_live_war(cfg) and days <= 0:
        state["settled_cash"] = float(state.get("settled_cash", 0)) + amount
        state["cash"] = float(state.get("cash", 0)) + amount
        return
    state.setdefault("unsettled", []).append({
        "amount": round(amount, 2),
        "settles_at_ts": settle_ts,
    })


def _commission_usd(cfg: Optional[BotConfig], notional: float) -> float:
    fixed = _env_float("WAR_COMMISSION_PER_SIDE_USD", 0.35)
    pct = float(getattr(cfg or BotConfig(), "TRANSACTION_COST_PCT", 0.001))
    return max(fixed, notional * pct * 0.5)


def apply_slippage_overlay(
    cfg: Optional[BotConfig],
    *,
    side: str,
    quote: float,
    shares: int,
    ticker: str = "",
    spread_pct: float = 0.0,
) -> Tuple[float, float]:
    """Return (virtual_fill, slippage_pct signed)."""
    q = float(quote or 0)
    if q <= 0:
        return q, 0.0
    base = _env_float("WAR_SLIPPAGE_BASE_PCT", 0.0012)
    penny = q < float(getattr(cfg or BotConfig(), "PENNY_PRICE_THRESHOLD", 1.0))
    penny_mult = 1.8 if penny else 1.0
    size_pen = min(0.008, max(0, int(shares) - 500) / 50000.0)
    slip = min(_env_float("WAR_MAX_SLIPPAGE_PCT", 0.012), (base + size_pen + spread_pct * 0.5) * penny_mult)
    if str(side).upper() in ("BUY", "BOT"):
        return q * (1.0 + slip), slip
    return q * (1.0 - slip), -slip


def _bullet_size(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> float:
    bullets = max(1, int(state.get("bullets_total", 5)))
    nav = float(state.get("nav", operating_capital_usd(cfg)))
    reserve = _env_float("WAR_CASH_RESERVE_PCT", 0.08)
    deployable = nav * (1.0 - reserve)
    return max(50.0, deployable / bullets)


def _recompute_mode(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> str:
    cfg = cfg or BotConfig()
    live = is_live_war(cfg)
    min_bullet = _bullet_size(state, cfg) * 0.85
    settled = float(state.get("settled_cash", 0))
    trips = int(state.get("round_trips_today", 0))
    max_trips = _env_int("WAR_MAX_ROUND_TRIPS_PER_DAY", 2)
    open_war = state.get("open_war")

    if open_war:
        return "LIVE_WAR" if live else "WAR_ACTIVE"

    if trips >= max_trips or settled < min_bullet:
        if lab_enabled(cfg) and float(state.get("lab_settled", 0)) >= min_bullet * 0.5:
            lab_trips = int(state.get("lab_round_trips_today", 0))
            lab_max = _env_int("WAR_LAB_MAX_ROUND_TRIPS_PER_DAY", 2)
            if lab_trips < lab_max and not state.get("open_lab"):
                return "LAB_ACTIVE"
        return "OBSERVE"

    return "LIVE_WAR" if live else "WAR_ACTIVE"


def ensure_war_account(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg):
        return {"ok": False, "reason": "disabled"}
    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state)
    state["mode"] = _recompute_mode(state, cfg)
    state["is_live"] = is_live_war(cfg)
    if not STATE_PATH.is_file():
        save_state(state)
    log.info(
        f"⚔️ War account — {'LIVE' if state['is_live'] else 'PAPER'} "
        f"nav=${float(state.get('nav', 0)):,.0f} settled=${float(state.get('settled_cash', 0)):,.0f} "
        f"mode={state['mode']} trips={int(state.get('round_trips_today', 0))}/"
        f"{_env_int('WAR_MAX_ROUND_TRIPS_PER_DAY', 2)} "
        f"fees_today=${float(state.get('fee_drag_today', 0)):,.2f}"
    )
    return {"ok": True, **state}


def current_mode(cfg: Optional[BotConfig] = None) -> str:
    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state)
    mode = _recompute_mode(state, cfg)
    state["mode"] = mode
    save_state(state)
    return mode


def war_effective_equity(cfg: Optional[BotConfig] = None) -> float:
    if not war_account_enabled(cfg):
        return 0.0
    state = load_state(cfg)
    _roll_session(state, cfg)
    return float(state.get("nav", operating_capital_usd(cfg)))


def war_settled_cash(cfg: Optional[BotConfig] = None) -> float:
    if not war_account_enabled(cfg):
        return 0.0
    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state)
    mode = state.get("mode") or _recompute_mode(state, cfg)
    if mode == "LAB_ACTIVE":
        return float(state.get("lab_settled", 0))
    return float(state.get("settled_cash", 0))


def mode_allows_entry(cfg: Optional[BotConfig] = None) -> bool:
    mode = current_mode(cfg)
    return mode in _MODES_WAR_ENTRY or mode in _MODES_LAB_ENTRY


def promotion_tag_for_mode(mode: str) -> bool:
    return mode in _MODES_WAR_ENTRY


def sniper_conf_bump(cfg: Optional[BotConfig] = None) -> float:
    if not sniper_mode(cfg):
        return 0.0
    return _env_float("WAR_SNIPER_CONF_BUMP", 0.06)


def war_account_context(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg):
        return {}
    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state)
    mode = _recompute_mode(state, cfg)
    state["mode"] = mode
    bullet = _bullet_size(state, cfg)
    max_trips = _env_int("WAR_MAX_ROUND_TRIPS_PER_DAY", 2)
    ctx = {
        "war_enabled": True,
        "war_mode": mode,
        "war_live": is_live_war(cfg),
        "war_nav": round(float(state.get("nav", 0)), 2),
        "war_settled_cash": round(float(state.get("settled_cash", 0)), 2),
        "war_lab_nav": round(float(state.get("lab_nav", 0)), 2),
        "war_bullet_usd": round(bullet, 2),
        "war_bullets_total": int(state.get("bullets_total", 5)),
        "war_bullets_used": int(state.get("bullets_used_session", 0)),
        "war_round_trips_today": int(state.get("round_trips_today", 0)),
        "war_round_trips_max": max_trips,
        "war_fee_drag_today": round(float(state.get("fee_drag_today", 0)), 2),
        "war_promotion": promotion_tag_for_mode(mode),
        "war_sniper": sniper_mode(cfg),
    }
    unsettled = sum(float(u.get("amount", 0)) for u in (state.get("unsettled") or []))
    ctx["war_unsettled_cash"] = round(unsettled, 2)
    return ctx


def war_context_line(cfg: Optional[BotConfig] = None) -> str:
    c = war_account_context(cfg)
    if not c:
        return ""
    return (
        f"WAR ACCOUNT [{c['war_mode']}]{' LIVE' if c.get('war_live') else ''}: "
        f"nav=${c['war_nav']:,.0f} settled=${c['war_settled_cash']:,.0f} "
        f"bullet≈${c['war_bullet_usd']:,.0f} "
        f"trips={c['war_round_trips_today']}/{c['war_round_trips_max']} "
        f"fees_today=${c['war_fee_drag_today']:,.2f} "
        f"promotion={'yes' if c['war_promotion'] else 'observe/lab'}"
    )


def check_entry_allowed(
    cfg: Optional[BotConfig],
    *,
    ticker: str = "",
    notional_usd: float = 0.0,
    pipeline: str = "",
) -> Optional[str]:
    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg):
        return None

    if pipeline:
        try:
            from core.war_entry_gates import war_entry_veto
            veto = war_entry_veto(cfg, pipeline=pipeline)
            if veto:
                return veto
        except Exception:
            pass

    try:
        from core.live_trade_guard import check_entry_allowed as guard_check
        g = guard_check(ticker, cfg)
        if g:
            return g
    except Exception:
        pass

    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state)
    mode = _recompute_mode(state, cfg)
    state["mode"] = mode
    save_state(state)

    if mode == "OBSERVE":
        return (
            f"war OBSERVE — war capital dry/settled out "
            f"(settled=${float(state.get('settled_cash', 0)):,.0f}); "
            f"logging only / lab exhausted"
        )

    use_lab = mode == "LAB_ACTIVE"
    settled = float(state.get("lab_settled" if use_lab else "settled_cash", 0))
    notional = float(notional_usd or 0)
    bullet = _bullet_size(state, cfg)
    max_notional = min(settled, bullet * 1.05)

    if notional <= 0:
        notional = bullet

    if notional > max_notional:
        return (
            f"war {mode}: need ${notional:,.0f} > settled/bullet "
            f"(${max_notional:,.0f})"
        )

    trips = int(state.get("lab_round_trips_today" if use_lab else "round_trips_today", 0))
    max_trips = _env_int(
        "WAR_LAB_MAX_ROUND_TRIPS_PER_DAY" if use_lab else "WAR_MAX_ROUND_TRIPS_PER_DAY",
        2 if use_lab else 2,
    )
    if trips >= max_trips:
        return f"war {mode}: round-trip cap {trips}/{max_trips}"

    comm = _commission_usd(cfg, notional) * 2
    min_edge = _env_float("WAR_MIN_NET_EDGE_USD", 0.75)
    if sniper_mode(cfg) and notional < min_edge * 3:
        pass  # size already bullet-limited

    if settled < notional + comm:
        return f"war GFV risk — settled ${settled:,.0f} < trade+fees ${notional + comm:,.0f}"

    return None


def rescale_decision_for_war(
    cfg: Optional[BotConfig],
    decision: Dict[str, Any],
    entry_px: float,
    *,
    ticker: str = "",
) -> Dict[str, Any]:
    """Clamp shares to war bullet + settled cash."""
    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg) or entry_px <= 0:
        return decision
    state = load_state(cfg)
    mode = state.get("mode") or _recompute_mode(state, cfg)
    use_lab = mode == "LAB_ACTIVE"
    settled = float(state.get("lab_settled" if use_lab else "settled_cash", 0))
    bullet = _bullet_size(state, cfg)
    cap_usd = min(settled, bullet)
    shares = int(decision.get("shares") or 0)
    if shares <= 0:
        deploy = float(decision.get("deploy_usd") or cap_usd)
        shares = int(deploy / entry_px)
    max_sh = max(1, int(cap_usd / entry_px))
    if shares > max_sh:
        shares = max_sh
    out = dict(decision)
    out["shares"] = shares
    out["deploy_usd"] = round(shares * entry_px, 2)
    out["war_mode"] = mode
    out["war_promotion"] = promotion_tag_for_mode(mode)
    out["war_rescaled"] = True
    return out


def record_entry(
    cfg: Optional[BotConfig],
    *,
    ticker: str,
    shares: int,
    ib_fill: float,
    quote: float,
    pipeline: str = "",
    spread_pct: float = 0.0,
) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    state = load_state(cfg)
    _roll_session(state, cfg)
    mode = state.get("mode") or _recompute_mode(state, cfg)
    use_lab = mode == "LAB_ACTIVE"

    v_fill, slip = apply_slippage_overlay(
        cfg, side="BUY", quote=ib_fill or quote, shares=shares, ticker=ticker,
        spread_pct=spread_pct,
    )
    notional = v_fill * int(shares)
    comm = _commission_usd(cfg, notional)

    if use_lab:
        state["lab_cash"] = float(state.get("lab_cash", 0)) - notional - comm
        state["lab_settled"] = float(state.get("lab_settled", 0)) - notional - comm
        state["open_lab"] = {
            "ticker": ticker.upper(), "shares": shares, "entry": v_fill,
            "ib_fill": ib_fill, "comm": comm, "ts": time.time(),
        }
    else:
        state["cash"] = float(state.get("cash", 0)) - notional - comm
        state["settled_cash"] = float(state.get("settled_cash", 0)) - notional - comm
        state["deployed_usd"] = float(state.get("deployed_usd", 0)) + notional
        state["bullets_used_session"] = int(state.get("bullets_used_session", 0)) + 1
        state["entries_today"] = int(state.get("entries_today", 0)) + 1
        state["fee_drag_today"] = float(state.get("fee_drag_today", 0)) + comm
        state["open_war"] = {
            "ticker": ticker.upper(), "shares": shares, "entry": v_fill,
            "ib_fill": ib_fill, "comm": comm, "pipeline": pipeline,
            "promotion": promotion_tag_for_mode(mode), "ts": time.time(),
        }

    state["mode"] = mode
    save_state(state)
    row = {
        "event": "war_entry", "ticker": ticker, "mode": mode,
        "shares": shares, "virtual_fill": v_fill, "ib_fill": ib_fill,
        "slippage_pct": slip, "commission": comm, "notional": notional,
        "promotion": promotion_tag_for_mode(mode), "pipeline": pipeline,
        "ts": time.time(),
    }
    _append_ledger(row)
    log.info(
        f"  ⚔️ WAR ENTRY {ticker} [{mode}]: {shares}sh @ ${v_fill:.4f} "
        f"(IB ${ib_fill:.4f} slip {slip:+.2%} fee ${comm:.2f})"
    )
    return row


def record_exit(
    cfg: Optional[BotConfig],
    *,
    ticker: str,
    shares: int,
    ib_fill: float,
    quote: float,
    pnl_usd_ib: float = 0.0,
    exit_reason: str = "",
    spread_pct: float = 0.0,
) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    state = load_state(cfg)
    t = ticker.upper()
    open_war = state.get("open_war") or {}
    open_lab = state.get("open_lab") or {}
    use_lab = open_lab.get("ticker") == t
    open_slot = open_lab if use_lab else open_war

    v_fill, slip = apply_slippage_overlay(
        cfg, side="SELL", quote=ib_fill or quote, shares=shares, ticker=ticker,
        spread_pct=spread_pct,
    )
    entry_v = float(open_slot.get("entry", 0) or 0)
    entry_comm = float(open_slot.get("comm", 0) or 0)
    sh = int(shares or open_slot.get("shares", 0) or 0)
    proceeds = v_fill * sh
    exit_comm = _commission_usd(cfg, proceeds)
    gross = (v_fill - entry_v) * sh if entry_v > 0 else float(pnl_usd_ib)
    net = gross - entry_comm - exit_comm

    if use_lab:
        state["lab_cash"] = float(state.get("lab_cash", 0)) + proceeds - exit_comm
        state["lab_settled"] = float(state.get("lab_settled", 0)) + proceeds - exit_comm
        state["lab_round_trips_today"] = int(state.get("lab_round_trips_today", 0)) + 1
        state["session_pnl_lab"] = float(state.get("session_pnl_lab", 0)) + net
        state["open_lab"] = None
        state["lab_nav"] = float(state.get("lab_cash", 0))
    else:
        state["cash"] = float(state.get("cash", 0)) + proceeds - exit_comm
        state["deployed_usd"] = max(0.0, float(state.get("deployed_usd", 0)) - entry_v * sh)
        _schedule_settlement(state, proceeds - exit_comm, cfg)
        state["round_trips_today"] = int(state.get("round_trips_today", 0)) + 1
        state["fee_drag_today"] = float(state.get("fee_drag_today", 0)) + exit_comm
        state["session_pnl_war"] = float(state.get("session_pnl_war", 0)) + net
        state["nav"] = float(state.get("cash", 0)) + float(state.get("deployed_usd", 0))
        state["open_war"] = None

    tp = state.setdefault("ticker_session_pnl", {})
    tp[t] = round(float(tp.get(t, 0)) + net, 2)
    if net < 0:
        tl = state.setdefault("ticker_session_losses", {})
        tl[t] = int(tl.get(t, 0)) + 1

    state["mode"] = _recompute_mode(state, cfg)
    save_state(state)

    try:
        from core.live_trade_guard import on_trade_closed
        on_trade_closed(t, net, cfg, exit_reason=exit_reason)
    except Exception:
        pass

    row = {
        "event": "war_exit", "ticker": t,
        "mode": "LAB_ACTIVE" if use_lab else state.get("mode"),
        "virtual_fill": v_fill, "ib_fill": ib_fill, "net_pnl": round(net, 2),
        "gross_pnl": round(gross, 2), "fees": round(entry_comm + exit_comm, 2),
        "promotion": promotion_tag_for_mode("WAR_ACTIVE" if not use_lab else "LAB_ACTIVE"),
        "exit_reason": exit_reason[:80], "ts": time.time(),
    }
    _append_ledger(row)
    log.info(
        f"  ⚔️ WAR EXIT {t}: net ${net:+.2f} (IB ${pnl_usd_ib:+.2f}) "
        f"fees ${entry_comm + exit_comm:.2f} | mode→{state['mode']}"
    )
    return row


def bullet_size_usd(cfg: Optional[BotConfig] = None) -> float:
    state = load_state(cfg)
    return _bullet_size(state, cfg)


def adjust_scan_score(
    cfg: Optional[BotConfig],
    ticker: str,
    base_score: float,
) -> float:
    """Session-aware lock ranking — deprioritize repeat losers."""
    if not war_account_enabled(cfg):
        return base_score
    state = load_state(cfg)
    t = str(ticker or "").upper()
    losses = int((state.get("ticker_session_losses") or {}).get(t, 0))
    pnl = float((state.get("ticker_session_pnl") or {}).get(t, 0))
    score = float(base_score)
    if losses >= 1:
        score -= min(25.0, 8.0 * losses)
    if pnl < -20:
        score -= min(20.0, abs(pnl) / 10.0)
    if pnl > 15:
        score += min(12.0, pnl / 15.0)
    try:
        from core.live_trade_guard import session_loss_count, ticker_cooldown_remaining
        if session_loss_count(t) >= 2:
            score -= 15.0
        if ticker_cooldown_remaining(t) > 0:
            score -= 30.0
    except Exception:
        pass
    return max(0.0, score)


def adjust_scan_results(
    cfg: Optional[BotConfig],
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not war_account_enabled(cfg) or not results:
        return results
    out = []
    for r in results:
        row = dict(r)
        tk = str(row.get("ticker", "")).upper()
        base = float(row.get("total_score", 0))
        adj = adjust_scan_score(cfg, tk, base)
        row["total_score"] = adj
        row["war_score_base"] = base
        if adj < base - 5:
            row["reasons"] = f"{row.get('reasons', '')} war_penalty".strip()
        out.append(row)
    out.sort(key=lambda x: float(x.get("total_score", 0)), reverse=True)
    return out


def should_evict_from_lock(cfg: Optional[BotConfig], ticker: str) -> bool:
    if not war_account_enabled(cfg):
        return False
    state = load_state(cfg)
    t = str(ticker or "").upper()
    losses = int((state.get("ticker_session_losses") or {}).get(t, 0))
    pnl = float((state.get("ticker_session_pnl") or {}).get(t, 0))
    if losses >= 3 or pnl < -40:
        return True
    try:
        from core.live_trade_guard import session_loss_count
        return session_loss_count(t) >= 3
    except Exception:
        return False


def filter_locked_pool(
    cfg: Optional[BotConfig],
    pool: List[Any],
) -> List[Any]:
    """Remove repeat losers from lock list when war sniper active."""
    if not war_account_enabled(cfg) or not sniper_mode(cfg):
        return pool
    kept = []
    for item in pool:
        tk = getattr(item, "ticker", None) or (item.get("ticker") if isinstance(item, dict) else "")
        if should_evict_from_lock(cfg, str(tk)):
            log.info(f"  ⚔️ WAR evict lock {tk} — repeat session loser")
            continue
        kept.append(item)
    return kept
