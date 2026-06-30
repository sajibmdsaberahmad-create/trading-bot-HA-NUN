#!/usr/bin/env python3
"""
core/war_account.py — Virtual war ledger for sizing/settlement on a $1k pool.

War pool sizes entries and trip caps — never raw IB paper ~$900k NAV.
Positions and session PnL sync from IB Gateway via core/ib_truth.py + war_ib_sync.
Modes: WAR_ACTIVE | LAB_ACTIVE | OBSERVE | LIVE_WAR.
"""

from __future__ import annotations

import json
import os
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

_REPO = Path(__file__).resolve().parents[1]
STATE_PATH = _REPO / "models" / "war_account_state.json"
LEDGER_PATH = _REPO / "models" / "war_account_ledger.jsonl"
_state_lock = threading.RLock()

_MODES_WAR_ENTRY = frozenset({"WAR_ACTIVE", "LIVE_WAR"})


def _normalize_open_positions(state: Dict[str, Any]) -> None:
    """Migrate legacy single open_war/open_lab → per-ticker dicts."""
    wars: Dict[str, Any] = {}
    raw_wars = state.get("open_wars")
    if isinstance(raw_wars, dict):
        for k, v in raw_wars.items():
            if isinstance(v, dict):
                wars[str(k).upper()] = v
    legacy_war = state.get("open_war")
    if isinstance(legacy_war, dict) and legacy_war.get("ticker"):
        wars.setdefault(str(legacy_war["ticker"]).upper(), legacy_war)
    state["open_wars"] = wars

    labs: Dict[str, Any] = {}
    raw_labs = state.get("open_labs")
    if isinstance(raw_labs, dict):
        for k, v in raw_labs.items():
            if isinstance(v, dict):
                labs[str(k).upper()] = v
    legacy_lab = state.get("open_lab")
    if isinstance(legacy_lab, dict) and legacy_lab.get("ticker"):
        labs.setdefault(str(legacy_lab["ticker"]).upper(), legacy_lab)
    state["open_labs"] = labs

    state["open_war"] = next(iter(wars.values()), None) if wars else None
    state["open_lab"] = next(iter(labs.values()), None) if labs else None


def _has_open_positions(state: Dict[str, Any]) -> bool:
    _normalize_open_positions(state)
    return bool(state.get("open_wars")) or bool(state.get("open_labs"))


def _resolve_open_slot(state: Dict[str, Any], ticker: str) -> Tuple[bool, Dict[str, Any]]:
    _normalize_open_positions(state)
    t = ticker.upper()
    labs = state.get("open_labs") or {}
    if t in labs:
        return True, labs[t]
    wars = state.get("open_wars") or {}
    if t in wars:
        return False, wars[t]
    return False, {}


def _clear_open_slot(state: Dict[str, Any], ticker: str, *, use_lab: bool) -> None:
    _normalize_open_positions(state)
    t = ticker.upper()
    if use_lab:
        labs = state.get("open_labs") or {}
        labs.pop(t, None)
        state["open_labs"] = labs
        state["open_lab"] = next(iter(labs.values()), None) if labs else None
    else:
        wars = state.get("open_wars") or {}
        wars.pop(t, None)
        state["open_wars"] = wars
        state["open_war"] = next(iter(wars.values()), None) if wars else None
_MODES_LAB_ENTRY = frozenset({"LAB_ACTIVE"})


def is_replay_session() -> bool:
    """True when CSV/replay is driving the loop — never the live war ledger."""
    if os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes"):
        return True
    try:
        from core.replay_clock import replay_now_et
        if replay_now_et() is not None:
            return True
    except Exception:
        pass
    return False


def war_account_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    if is_replay_session():
        return False
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


def _settlement_days(cfg: Optional[BotConfig] = None) -> int:
    """Paper defaults to instant settlement so multiple war round-trips can run same day."""
    cfg = cfg or BotConfig()
    if is_live_war(cfg):
        return _env_int("WAR_SETTLEMENT_DAYS", 1)
    return _env_int(
        "WAR_PAPER_SETTLEMENT_DAYS",
        _env_int("WAR_SETTLEMENT_DAYS", 0),
    )


def max_war_round_trips_per_day(
    cfg: Optional[BotConfig] = None,
    *,
    use_lab: bool = False,
) -> int:
    """Live stays tight; paper allows more round-trips for war learning."""
    cfg = cfg or BotConfig()
    if use_lab:
        if is_live_war(cfg):
            return _env_int("WAR_LAB_MAX_ROUND_TRIPS_PER_DAY", 2)
        return _env_int(
            "WAR_PAPER_LAB_MAX_ROUND_TRIPS_PER_DAY",
            _env_int("WAR_LAB_MAX_ROUND_TRIPS_PER_DAY", 4),
        )
    if is_live_war(cfg):
        return _env_int("WAR_MAX_ROUND_TRIPS_PER_DAY", 2)
    bullets = _env_int("WAR_BULLETS", 8)
    return _env_int(
        "WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY",
        _env_int("WAR_MAX_ROUND_TRIPS_PER_DAY", bullets),
    )


def max_war_entries_per_hour(cfg: Optional[BotConfig] = None) -> int:
    cfg = cfg or BotConfig()
    if is_live_war(cfg):
        return _env_int("WAR_MAX_ENTRIES_PER_HOUR", 2)
    return _env_int(
        "WAR_PAPER_MAX_ENTRIES_PER_HOUR",
        _env_int("WAR_MAX_ENTRIES_PER_HOUR", 5),
    )


def war_ai_sizing_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """
    AI chooses deploy size and bullet count — war pool is not sliced into fixed
    mechanical bullets. Settled cash is the hard limit; bullets_total is advisory.
    """
    cfg = cfg or BotConfig()
    env = os.getenv("WAR_AI_SIZING", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    try:
        from core.pilot_mode import ai_full_capital_access
        return ai_full_capital_access(cfg)
    except Exception:
        return os.getenv("AI_UNLIMITED_MODE", "true").lower() not in ("0", "false", "no")


def balance_driven_trips_enabled(
    cfg: Optional[BotConfig] = None,
    *,
    use_lab: bool = False,
) -> bool:
    """
    When true, war/lab entries are limited by settled cash (bullets remaining),
    not a fixed daily round-trip counter.
    Paper default on; live default off (fixed cap for safety).
    """
    cfg = cfg or BotConfig()
    if use_lab:
        lab_env = os.getenv("WAR_BALANCE_DRIVEN_LAB", "").strip().lower()
        if lab_env in ("0", "false", "no"):
            return False
        if lab_env in ("1", "true", "yes"):
            return True
    if is_live_war(cfg):
        return os.getenv("WAR_BALANCE_DRIVEN_TRIPS", "false").lower() in ("1", "true", "yes")
    return os.getenv("WAR_BALANCE_DRIVEN_TRIPS", "true").lower() not in ("0", "false", "no")


def _min_entry_settled(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> float:
    """Minimum settled cash required to fund one war/lab entry."""
    cfg = cfg or BotConfig()
    if war_ai_sizing_enabled(cfg):
        return max(50.0, _commission_usd(cfg, 100.0) * 2 + 1.0)
    return _bullet_size(state, cfg) * 0.85


def war_bullets_remaining(
    state: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
    *,
    use_lab: bool = False,
) -> int:
    """Deployment slots remaining — advisory under AI sizing, slice-based otherwise."""
    cfg = cfg or BotConfig()
    settled = float(state.get("lab_settled" if use_lab else "settled_cash", 0))
    min_entry = _min_entry_settled(state, cfg)
    if min_entry <= 0 or settled < min_entry:
        return 0
    if war_ai_sizing_enabled(cfg):
        total = max(0, int(state.get("bullets_total", 0)))
        used = int(state.get("bullets_used_session", 0))
        if total > 0:
            return max(0, total - used)
        return 1
    return int(settled // min_entry)


def war_trip_display(
    state: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
    *,
    use_lab: bool = False,
) -> Tuple[int, int]:
    """
    (round_trips_used, cap_for_display).
    Balance-driven: cap = used + bullets_remaining from settled cash.
    Fixed-cap: cap = env max round trips per day.
    """
    cfg = cfg or BotConfig()
    trips = int(state.get("lab_round_trips_today" if use_lab else "round_trips_today", 0))
    if balance_driven_trips_enabled(cfg, use_lab=use_lab):
        remaining = war_bullets_remaining(state, cfg, use_lab=use_lab)
        return trips, trips + remaining
    return trips, max_war_round_trips_per_day(cfg, use_lab=use_lab)


def _trip_cap_blocks(
    state: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
    *,
    use_lab: bool = False,
) -> bool:
    """True when no more entries allowed due to trip/balance limits."""
    cfg = cfg or BotConfig()
    settled = float(state.get("lab_settled" if use_lab else "settled_cash", 0))
    if settled < _min_entry_settled(state, cfg):
        return True
    if war_ai_sizing_enabled(cfg) and balance_driven_trips_enabled(cfg, use_lab=use_lab):
        return False
    if balance_driven_trips_enabled(cfg, use_lab=use_lab):
        return war_bullets_remaining(state, cfg, use_lab=use_lab) <= 0
    trips = int(state.get("lab_round_trips_today" if use_lab else "round_trips_today", 0))
    return trips >= max_war_round_trips_per_day(cfg, use_lab=use_lab)


def _entry_deploy_cap(
    state: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
    *,
    use_lab: bool = False,
) -> float:
    """Max USD for next entry — full settled pool under AI sizing, else bullet slice."""
    cfg = cfg or BotConfig()
    settled = float(state.get("lab_settled" if use_lab else "settled_cash", 0))
    bullet = _bullet_size(state, cfg)
    comm_buf = _commission_usd(cfg, max(bullet, settled * 0.1)) * 2 + 0.5
    if war_ai_sizing_enabled(cfg):
        reserve = _env_float("WAR_CASH_RESERVE_PCT", 0.05)
        deployable = settled * (1.0 - reserve) if reserve > 0 else settled
        return max(50.0, deployable - comm_buf)
    if balance_driven_trips_enabled(cfg, use_lab=use_lab):
        return max(50.0, min(bullet, settled - comm_buf))
    return min(settled, bullet)


def war_account_state(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Snapshot for posture/Halim — merges ledger state + derived balance fields."""
    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg):
        return {}
    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state, cfg)
    ctx = war_account_context(cfg)
    ctx.update({
        "round_trips_today": int(state.get("round_trips_today", 0)),
        "lab_round_trips_today": int(state.get("lab_round_trips_today", 0)),
        "trips_today": int(state.get("round_trips_today", 0)),
        "settled_cash": float(state.get("settled_cash", 0)),
        "lab_settled": float(state.get("lab_settled", 0)),
    })
    return ctx


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


def swing_paper_capital_usd(cfg: Optional[BotConfig] = None) -> float:
    """Virtual swing paper pool — IB marks, no live orders."""
    return _env_float("WAR_SWING_PAPER_USD", 2000.0)


def lab_enabled(cfg: Optional[BotConfig] = None) -> bool:
    if not war_account_enabled(cfg):
        return False
    return os.getenv("WAR_LAB_ENABLED", "true").lower() in ("1", "true", "yes")


def _today_key() -> str:
    """US Eastern session date — never device locale or UTC."""
    from core.market_hours import now_et
    return now_et().strftime("%Y-%m-%d")


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
        "rth_rolled_date": None,
        "open_war": None,
        "open_lab": None,
        "open_wars": {},
        "open_labs": {},
        "session_pnl_war": 0.0,
        "session_pnl_lab": 0.0,
        "ticker_session_pnl": {},
        "ticker_session_losses": {},
        "updated_at": time.time(),
    }


def _war_open_deployed_notional(state: Dict[str, Any]) -> float:
    _normalize_open_positions(state)
    total = 0.0
    for slot in (state.get("open_wars") or {}).values():
        if not isinstance(slot, dict):
            continue
        total += int(slot.get("shares", 0) or 0) * float(slot.get("entry", 0) or 0)
    return total


def _reconcile_war_cash_from_positions(
    state: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
) -> None:
    """Align deployed/settled with open_wars — never leave settled_cash negative."""
    cfg = cfg or BotConfig()
    nav = float(
        state.get("nav", state.get("operating_capital", 0))
        or operating_capital_usd(cfg)
    )
    deployed = _war_open_deployed_notional(state)
    state["deployed_usd"] = round(deployed, 2)
    settled = max(0.0, nav - deployed)
    state["settled_cash"] = round(settled, 2)
    state["cash"] = round(settled, 2)


def _heal_war_cash_ledger(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> bool:
    """One-shot repair when IB recover used record_entry and overdrew settled."""
    cfg = cfg or BotConfig()
    settled = float(state.get("settled_cash", 0))
    cash = float(state.get("cash", 0))
    if settled >= -0.01 and cash >= -0.01:
        return False
    before = settled
    _reconcile_war_cash_from_positions(state, cfg)
    log.warning(
        f"  ⚠️ War cash heal: settled ${before:,.0f} → "
        f"${float(state.get('settled_cash', 0)):,.0f} "
        f"(deployed=${float(state.get('deployed_usd', 0)):,.0f} nav=${float(state.get('nav', 0)):,.0f})"
    )
    return True


def load_state(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    with _state_lock:
        if STATE_PATH.is_file():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("nav") is not None:
                    _normalize_open_positions(data)
                    if _heal_war_cash_ledger(data, cfg):
                        save_state(data)
                    return data
            except Exception:
                pass
        return _default_state(cfg)


def save_state(state: Dict[str, Any]) -> None:
    if is_replay_session():
        log.debug("War state save skipped — replay is not a live account")
        return
    state["updated_at"] = time.time()
    with _state_lock:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _append_ledger(row: Dict[str, Any]) -> None:
    if is_replay_session():
        return
    try:
        from core.trade_horizon import active_order_horizon, tag_record
        tag_record(row, row.get("horizon") or active_order_horizon())
    except Exception:
        pass
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def _reset_session_counters(state: Dict[str, Any]) -> None:
    state.update({
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


def _apply_fresh_session_capital(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> None:
    """Restore war/lab pools to configured operating capital (paper $1k / live $1k)."""
    cfg = cfg or BotConfig()
    cap = operating_capital_usd(cfg)
    lab = lab_capital_usd(cfg) if lab_enabled(cfg) else 0.0
    state["operating_capital"] = cap
    state["nav"] = cap
    state["cash"] = cap
    state["settled_cash"] = cap
    state["unsettled"] = []
    state["deployed_usd"] = 0.0
    state["bullets_total"] = _env_int("WAR_BULLETS", int(state.get("bullets_total", 5)))
    if lab > 0:
        state["lab_capital"] = lab
        state["lab_nav"] = lab
        state["lab_cash"] = lab
        state["lab_settled"] = lab


def _war_auto_reset_at_rth_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("WAR_AUTO_RESET_AT_RTH", "true").lower() in ("1", "true", "yes")


def _roll_rth_session(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> bool:
    """
    Fresh war/lab budget + zero trip counters at 09:30 ET each session day.

    Premarket exhausts round-trip caps; RTH open gets a clean pool without manual reset.
    Paper uses WAR_CAPITAL_USD (default $1k); live uses WAR_LIVE_OPERATING_CAPITAL ($1k).
    """
    cfg = cfg or BotConfig()
    if not _war_auto_reset_at_rth_enabled(cfg):
        return False
    if is_replay_session():
        return False
    try:
        from core.rth_session import is_rth
        if not is_rth(cfg):
            return False
    except Exception:
        return False
    today = _today_key()
    if state.get("rth_rolled_date") == today:
        return False
    if _has_open_positions(state):
        log.info(
            "⚔️ War RTH reset skipped — open position carried from extended hours"
        )
        state["rth_rolled_date"] = today
        return False

    _reset_session_counters(state)
    _apply_fresh_session_capital(state, cfg)
    state["rth_rolled_date"] = today
    state["mode"] = "LIVE_WAR" if is_live_war(cfg) else "WAR_ACTIVE"
    _append_ledger({
        "event": "rth_session_reset",
        "session_date": today,
        "mode": state.get("mode"),
        "nav": state.get("nav"),
        "lab_settled": state.get("lab_settled"),
        "ts": time.time(),
    })
    log.info(
        f"⚔️ War account RTH reset (ET) — mode={state['mode']} "
        f"nav=${float(state.get('nav', 0)):,.0f} settled=${float(state.get('settled_cash', 0)):,.0f} "
        f"lab=${float(state.get('lab_settled', 0)):,.0f} trips=0"
    )
    return True


def _roll_session(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> None:
    changed = False
    today = _today_key()
    if state.get("session_date") != today:
        cap = operating_capital_usd(cfg)
        lab = lab_capital_usd(cfg) if lab_enabled(cfg) else 0.0
        state.update({
            "session_date": today,
            "rth_rolled_date": None,
        })
        _reset_session_counters(state)
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
        _append_ledger({
            "event": "calendar_session_roll",
            "session_date": today,
            "mode": state.get("mode"),
            "nav": state.get("nav"),
            "ts": time.time(),
        })
        log.info(
            f"⚔️ War calendar roll (ET midnight) — session={today} "
            f"nav=${float(state.get('nav', 0)):,.0f} settled=${float(state.get('settled_cash', 0)):,.0f}"
        )
        changed = True
    if _roll_rth_session(state, cfg):
        changed = True
    if changed and not is_replay_session():
        save_state(state)


def _apply_settlement(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> None:
    cfg = cfg or BotConfig()
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
    # Paper instant settlement — release any legacy T+1 holds so more war trips can run same day
    if not is_live_war(cfg) and _settlement_days(cfg) <= 0 and state.get("unsettled"):
        extra = sum(float(row.get("amount", 0)) for row in state.get("unsettled") or [])
        if extra > 0:
            state["settled_cash"] = float(state.get("settled_cash", 0)) + extra
            state["cash"] = float(state.get("cash", 0)) + extra
        state["unsettled"] = []


def _schedule_settlement(state: Dict[str, Any], amount: float, cfg: Optional[BotConfig] = None) -> None:
    days = _settlement_days(cfg)
    if days <= 0:
        state["settled_cash"] = float(state.get("settled_cash", 0)) + amount
        state["cash"] = float(state.get("cash", 0)) + amount
        return
    settle_ts = time.time() + max(1, days) * 86400
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
    min_bullet = _min_entry_settled(state, cfg)
    settled = float(state.get("settled_cash", 0))
    open_war = state.get("open_wars") or {}
    if open_war:
        return "LIVE_WAR" if live else "WAR_ACTIVE"

    war_trips_blocked = _trip_cap_blocks(state, cfg, use_lab=False)
    if war_trips_blocked or settled < min_bullet:
        if lab_enabled(cfg) and float(state.get("lab_settled", 0)) >= min_bullet * 0.5:
            if not state.get("open_labs") and not _trip_cap_blocks(state, cfg, use_lab=True):
                return "LAB_ACTIVE"
        return "OBSERVE"

    return "LIVE_WAR" if live else "WAR_ACTIVE"


def _reset_war_trip_counters(state: Dict[str, Any]) -> None:
    state["round_trips_today"] = 0
    state["bullets_used_session"] = 0


def _fresh_trips_on_hanoon_start_enabled(cfg: Optional[BotConfig] = None) -> bool:
    """Paper: unblock war entries on HANOON restart when settled cash remains."""
    cfg = cfg or BotConfig()
    if is_live_war(cfg):
        return os.getenv("WAR_FRESH_TRIPS_ON_START", "false").lower() in ("1", "true", "yes")
    return os.getenv("WAR_FRESH_TRIPS_ON_START", "true").lower() not in ("0", "false", "no")


def _maybe_refresh_trips_if_settled(
    state: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
) -> bool:
    """
    Trip cap blocks entries even when settled cash remains (fixed-cap mode only).
    On HANOON startup, refresh war trip counters if pool still has bullets left.
    """
    cfg = cfg or BotConfig()
    if balance_driven_trips_enabled(cfg):
        return False
    if not _fresh_trips_on_hanoon_start_enabled(cfg):
        return False
    if _has_open_positions(state):
        return False
    trips = int(state.get("round_trips_today", 0))
    max_trips = max_war_round_trips_per_day(cfg)
    if trips < max_trips:
        return False
    settled = float(state.get("settled_cash", 0))
    min_bullet = _bullet_size(state, cfg) * 0.85
    if settled < min_bullet:
        return False
    old_trips = trips
    _reset_war_trip_counters(state)
    log.info(
        f"⚔️ War trips refreshed on HANOON start — settled=${settled:,.0f} "
        f"was trip-capped at {old_trips}/{max_trips}; war entries re-enabled"
    )
    return True


def _sync_paper_war_config(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> None:
    """Apply env capital/bullet updates on paper restart (same session day)."""
    cfg = cfg or BotConfig()
    if is_live_war(cfg) or _has_open_positions(state):
        return
    cap = operating_capital_usd(cfg)
    old_cap = float(state.get("operating_capital", 0) or 0)
    if cap > old_cap > 0:
        bump = cap - old_cap
        state["operating_capital"] = cap
        state["nav"] = float(state.get("nav", 0)) + bump
        state["cash"] = float(state.get("cash", 0)) + bump
        state["settled_cash"] = float(state.get("settled_cash", 0)) + bump
    lab = lab_capital_usd(cfg) if lab_enabled(cfg) else 0.0
    old_lab = float(state.get("lab_capital", 0) or 0)
    if lab > old_lab > 0:
        lb = lab - old_lab
        state["lab_capital"] = lab
        state["lab_nav"] = float(state.get("lab_nav", 0)) + lb
        state["lab_cash"] = float(state.get("lab_cash", 0)) + lb
        state["lab_settled"] = float(state.get("lab_settled", 0)) + lb
    bullets = _env_int("WAR_BULLETS", int(state.get("bullets_total", 5)))
    if bullets > 0:
        state["bullets_total"] = bullets


def ensure_war_account(cfg: Optional[BotConfig] = None, ib=None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    if is_replay_session():
        log.debug("War account init skipped — replay session")
        return {"ok": False, "reason": "replay"}
    if not war_account_enabled(cfg):
        return {"ok": False, "reason": "disabled"}
    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state, cfg)
    _sync_paper_war_config(state, cfg)
    _maybe_refresh_trips_if_settled(state, cfg)
    state["mode"] = _recompute_mode(state, cfg)
    state["is_live"] = is_live_war(cfg)
    if ib is not None:
        synced = False
        try:
            from core.war_ib_sync import sync_war_from_ib, war_ib_sync_enabled
            if war_ib_sync_enabled(cfg):
                sync_war_from_ib(ib, cfg, apply=True, force=True)
                state = load_state(cfg)
                synced = True
        except Exception as exc:
            log.warning(f"War IB sync at startup: {exc}")
        if not synced:
            save_state(state)
    else:
        save_state(state)
    war_trips, war_cap = war_trip_display(state, cfg)
    lab_trips, lab_cap = war_trip_display(state, cfg, use_lab=True)
    bullets_left = war_bullets_remaining(state, cfg)
    if balance_driven_trips_enabled(cfg):
        trips_part = (
            f"round_trips={war_trips} bullets_left={bullets_left} "
            f"(balance-driven)"
        )
    else:
        trips_part = (
            f"war_trips={war_trips}/{war_cap} lab_trips={lab_trips}/{lab_cap}"
        )
    log.info(
        f"⚔️ War account — {'LIVE' if state['is_live'] else 'PAPER'} "
        f"nav=${float(state.get('nav', 0)):,.0f} settled=${float(state.get('settled_cash', 0)):,.0f} "
        f"mode={state['mode']} {trips_part} "
        f"fees_today=${float(state.get('fee_drag_today', 0)):,.2f}"
    )
    return {"ok": True, **state}


def current_mode(cfg: Optional[BotConfig] = None) -> str:
    if is_replay_session():
        return "OBSERVE"
    state = load_state(cfg)
    _roll_session(state, cfg)
    _apply_settlement(state, cfg)
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
    _apply_settlement(state, cfg)
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
    _apply_settlement(state, cfg)
    mode = _recompute_mode(state, cfg)
    state["mode"] = mode
    bullet = _bullet_size(state, cfg)
    war_trips, war_cap = war_trip_display(state, cfg)
    lab_trips, lab_cap = war_trip_display(state, cfg, use_lab=True)
    bullets_left = war_bullets_remaining(state, cfg)
    lab_bullets_left = war_bullets_remaining(state, cfg, use_lab=True)
    deploy_cap = _entry_deploy_cap(state, cfg)
    ctx = {
        "war_enabled": True,
        "war_mode": mode,
        "war_live": is_live_war(cfg),
        "war_balance_driven": balance_driven_trips_enabled(cfg),
        "war_lab_balance_driven": balance_driven_trips_enabled(cfg, use_lab=True),
        "war_ai_sizing": war_ai_sizing_enabled(cfg),
        "war_deploy_cap_usd": round(deploy_cap, 2),
        "war_nav": round(float(state.get("nav", 0)), 2),
        "war_settled_cash": round(float(state.get("settled_cash", 0)), 2),
        "war_lab_nav": round(float(state.get("lab_nav", 0)), 2),
        "war_lab_settled": round(float(state.get("lab_settled", 0)), 2),
        "war_bullet_usd": round(bullet, 2),
        "war_min_entry_usd": round(_min_entry_settled(state, cfg), 2),
        "war_bullets_total": int(state.get("bullets_total", 5)),
        "war_bullets_used": int(state.get("bullets_used_session", 0)),
        "war_bullets_remaining": bullets_left,
        "war_lab_bullets_remaining": lab_bullets_left,
        "war_round_trips_today": war_trips,
        "war_round_trips_max": war_cap,
        "war_lab_round_trips_today": lab_trips,
        "war_lab_round_trips_max": lab_cap,
        "war_fee_drag_today": round(float(state.get("fee_drag_today", 0)), 2),
        "war_promotion": promotion_tag_for_mode(mode),
        "war_sniper": sniper_mode(cfg),
    }
    unsettled = sum(float(u.get("amount", 0)) for u in (state.get("unsettled") or []))
    ctx["war_unsettled_cash"] = round(unsettled, 2)
    try:
        from core.capital_phase import capital_phase_context
        ctx.update(capital_phase_context(cfg))
    except Exception:
        pass
    return ctx


def war_context_line(cfg: Optional[BotConfig] = None) -> str:
    c = war_account_context(cfg)
    if not c:
        return ""
    if c.get("war_balance_driven"):
        if c.get("war_ai_sizing"):
            trips_bit = (
                f"round_trips={c['war_round_trips_today']} "
                f"deploy_cap=${c.get('war_deploy_cap_usd', 0):,.0f} "
                f"bullets_advisory={c['war_bullets_remaining']}/{c['war_bullets_total']}"
            )
        else:
            trips_bit = (
                f"round_trips={c['war_round_trips_today']} "
                f"bullets_left={c['war_bullets_remaining']} (settled-driven)"
            )
    else:
        trips_bit = (
            f"trips={c['war_round_trips_today']}/{c['war_round_trips_max']}"
        )
    return (
        f"WAR ACCOUNT [{c['war_mode']}]{' LIVE' if c.get('war_live') else ''}: "
        f"nav=${c['war_nav']:,.0f} settled=${c['war_settled_cash']:,.0f} "
        f"bullet≈${c['war_bullet_usd']:,.0f} {trips_bit} "
        f"fees_today=${c['war_fee_drag_today']:,.2f} "
        f"promotion={'yes' if c['war_promotion'] else 'observe/lab'}"
    )


def _observe_block_reason(state: Dict[str, Any], cfg: Optional[BotConfig] = None) -> str:
    """Human-readable OBSERVE veto — settled cash vs trip cap."""
    cfg = cfg or BotConfig()
    settled = float(state.get("settled_cash", 0))
    min_bullet = _min_entry_settled(state, cfg)
    lab_settled = float(state.get("lab_settled", 0))
    bullets_left = war_bullets_remaining(state, cfg)
    lab_bullets_left = war_bullets_remaining(state, cfg, use_lab=True)

    if balance_driven_trips_enabled(cfg):
        if settled < min_bullet:
            return (
                f"war OBSERVE — settled ${settled:,.0f} below min entry "
                f"${min_bullet:,.0f} (pool dry)"
            )
        if not war_ai_sizing_enabled(cfg) and bullets_left <= 0:
            return (
                f"war OBSERVE — no bullets left from settled ${settled:,.0f} "
                f"(min entry ${min_bullet:,.0f})"
            )
    else:
        war_trips, war_cap = war_trip_display(state, cfg)
        if war_trips >= war_cap:
            return (
                f"war OBSERVE — daily war trip cap {war_trips}/{war_cap} "
                f"(settled=${settled:,.0f} still available); resets 09:30 ET"
            )
        if settled < min_bullet:
            return (
                f"war OBSERVE — settled ${settled:,.0f} below min bullet "
                f"${min_bullet:,.0f} (T+1 dry)"
            )

    if balance_driven_trips_enabled(cfg, use_lab=True):
        if lab_bullets_left <= 0 and lab_settled < min_bullet * 0.5:
            return (
                f"war OBSERVE — lab pool dry (lab settled=${lab_settled:,.0f})"
            )
    else:
        lab_trips, lab_cap = war_trip_display(state, cfg, use_lab=True)
        if lab_trips >= lab_cap and lab_settled < min_bullet * 0.5:
            return (
                f"war OBSERVE — lab trip cap {lab_trips}/{lab_cap} "
                f"(war settled=${settled:,.0f})"
            )
    return f"war OBSERVE — logging only (settled=${settled:,.0f})"


def war_ledger_applies(
    cfg: Optional[BotConfig] = None,
    *,
    horizon: str = "scalp",
    capital_phase: Optional[str] = None,
) -> bool:
    """Virtual war ledger debits only for RTH war scalp."""
    if not war_account_enabled(cfg):
        return False
    if horizon != "scalp":
        return False
    try:
        from core.capital_phase import capital_phases_enabled, PHASE_RTH_WAR, capital_phase as current_capital_phase
        if capital_phases_enabled(cfg):
            phase = capital_phase or current_capital_phase(cfg)
            return phase == PHASE_RTH_WAR
    except Exception:
        pass
    return True


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

    try:
        from core.capital_phase import capital_phases_enabled, uses_war_sizing
        if capital_phases_enabled(cfg) and not uses_war_sizing(cfg):
            return None
    except Exception:
        pass

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
    with _state_lock:
        _roll_session(state, cfg)
        _apply_settlement(state, cfg)
        mode = _recompute_mode(state, cfg)
        state["mode"] = mode
        save_state(state)

    if mode == "OBSERVE":
        return _observe_block_reason(state, cfg)

    use_lab = mode == "LAB_ACTIVE"
    settled = float(state.get("lab_settled" if use_lab else "settled_cash", 0))
    notional = float(notional_usd or 0)
    bullet = _bullet_size(state, cfg)
    deploy_cap = _entry_deploy_cap(state, cfg, use_lab=use_lab)
    max_notional = min(settled, deploy_cap * 1.05)

    if notional <= 0:
        notional = deploy_cap

    if notional > max_notional:
        return (
            f"war {mode}: need ${notional:,.0f} > settled/deploy cap "
            f"(${max_notional:,.0f})"
        )

    if _trip_cap_blocks(state, cfg, use_lab=use_lab):
        if balance_driven_trips_enabled(cfg, use_lab=use_lab):
            return (
                f"war {mode}: settled ${settled:,.0f} below min entry "
                f"${_min_entry_settled(state, cfg):,.0f}"
            )
        trips, cap = war_trip_display(state, cfg, use_lab=use_lab)
        return f"war {mode}: round-trip cap {trips}/{cap}"

    comm = _commission_usd(cfg, notional) * 2
    min_edge = _env_float("WAR_MIN_NET_EDGE_USD", 0.75)
    if sniper_mode(cfg) and notional < min_edge * 3:
        pass  # size already bullet-limited

    if settled < notional + comm:
        return f"war GFV risk — settled ${settled:,.0f} < trade+fees ${notional + comm:,.0f}"

    return None


def reset_live_war_session(
    cfg: Optional[BotConfig] = None,
    *,
    reason: str = "manual_reset",
) -> Dict[str, Any]:
    """Fresh war/lab capital and zero session counters for live/paper only."""
    cfg = cfg or BotConfig()
    if is_replay_session():
        return {"ok": False, "reason": "replay"}
    state = _default_state(cfg)
    save_state(state)
    _append_ledger({
        "event": "session_reset",
        "reason": reason[:120],
        "mode": state.get("mode"),
        "nav": state.get("nav"),
        "ts": time.time(),
    })
    log.info(
        f"⚔️ War account reset ({reason}) — mode={state['mode']} "
        f"nav=${float(state.get('nav', 0)):,.0f} settled=${float(state.get('settled_cash', 0)):,.0f} "
        f"lab=${float(state.get('lab_settled', 0)):,.0f} trips=0"
    )
    return {"ok": True, **state}


def rescale_decision_for_war(
    cfg: Optional[BotConfig],
    decision: Dict[str, Any],
    entry_px: float,
    *,
    ticker: str = "",
) -> Dict[str, Any]:
    """Clamp shares to war deploy cap + settled cash (full pool when AI sizing)."""
    cfg = cfg or BotConfig()
    if entry_px <= 0 or not war_ledger_applies(cfg):
        return decision
    state = load_state(cfg)
    mode = state.get("mode") or _recompute_mode(state, cfg)
    use_lab = mode == "LAB_ACTIVE"
    cap_usd = _entry_deploy_cap(state, cfg, use_lab=use_lab)
    shares = int(decision.get("shares") or 0)
    deploy = float(decision.get("deploy_usd") or 0)
    if shares <= 0:
        if deploy <= 0:
            deploy = cap_usd
        else:
            deploy = min(deploy, cap_usd)
        shares = int(deploy / entry_px)
    elif war_ai_sizing_enabled(cfg) and deploy > 0:
        shares = min(shares, max(1, int(min(deploy, cap_usd) / entry_px)))
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


def adopt_war_ib_recovery(
    cfg: Optional[BotConfig],
    *,
    ticker: str,
    shares: int,
    ib_fill: float,
    quote: float,
    spread_pct: float = 0.0,
) -> Dict[str, Any]:
    """Register IB-recovered war slot without debiting settled like a new BUY."""
    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg):
        return {}
    state = load_state(cfg)
    _roll_session(state, cfg)
    mode = state.get("mode") or _recompute_mode(state, cfg)
    if mode == "LAB_ACTIVE":
        return {}

    t_up = ticker.upper()
    sh = int(shares or 0)
    if sh <= 0:
        return {}

    v_fill, slip = apply_slippage_overlay(
        cfg, side="BUY", quote=ib_fill or quote, shares=sh, ticker=ticker,
        spread_pct=spread_pct,
    )
    if v_fill <= 0:
        return {}

    notional = v_fill * sh
    nav = float(state.get("nav", state.get("operating_capital", 0)) or operating_capital_usd(cfg))
    max_ledger_pct = _env_float("WAR_IB_RECOVER_MAX_NAV_PCT", 0.90)
    if notional > nav * max_ledger_pct:
        log.warning(
            f"  ⚠️ IB recover {t_up} ${notional:,.0f} > {max_ledger_pct:.0%} of war nav "
            f"${nav:,.0f} — monitor only (not war ledger)"
        )
        return {"skipped": True, "reason": "exceeds_war_nav", "ticker": t_up, "notional": notional}

    _normalize_open_positions(state)
    wars = state.setdefault("open_wars", {})
    if t_up in wars:
        return wars[t_up]

    comm = _commission_usd(cfg, notional)
    slot_data = {
        "ticker": t_up,
        "shares": sh,
        "entry": v_fill,
        "ib_fill": ib_fill,
        "comm": comm,
        "ts": time.time(),
        "pipeline": "ib_recover",
        "promotion": promotion_tag_for_mode(mode),
        "recovered": True,
    }
    wars[t_up] = slot_data
    state["open_war"] = slot_data
    _reconcile_war_cash_from_positions(state, cfg)
    state["mode"] = mode
    save_state(state)

    row = {
        "event": "war_ib_recover",
        "ticker": t_up,
        "mode": mode,
        "shares": sh,
        "virtual_fill": v_fill,
        "ib_fill": ib_fill,
        "slippage_pct": slip,
        "commission": comm,
        "notional": notional,
        "pipeline": "ib_recover",
        "ts": time.time(),
    }
    _append_ledger(row)
    log.info(
        f"  ⚔️ WAR IB RECOVER {t_up}: {sh}sh @ ${v_fill:.4f} "
        f"(ledger only — settled=${float(state.get('settled_cash', 0)):,.0f})"
    )
    return row


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
    if not war_ledger_applies(cfg):
        return {}
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

    t_up = ticker.upper()
    slot_data = {
        "ticker": t_up, "shares": shares, "entry": v_fill,
        "ib_fill": ib_fill, "comm": comm, "ts": time.time(),
    }

    if use_lab:
        state["lab_cash"] = float(state.get("lab_cash", 0)) - notional - comm
        state["lab_settled"] = float(state.get("lab_settled", 0)) - notional - comm
        _normalize_open_positions(state)
        labs = state.setdefault("open_labs", {})
        labs[t_up] = slot_data
        state["open_lab"] = slot_data
    else:
        state["cash"] = float(state.get("cash", 0)) - notional - comm
        state["settled_cash"] = float(state.get("settled_cash", 0)) - notional - comm
        state["deployed_usd"] = float(state.get("deployed_usd", 0)) + notional
        state["bullets_used_session"] = int(state.get("bullets_used_session", 0)) + 1
        state["entries_today"] = int(state.get("entries_today", 0)) + 1
        state["fee_drag_today"] = float(state.get("fee_drag_today", 0)) + comm
        _normalize_open_positions(state)
        wars = state.setdefault("open_wars", {})
        wars[t_up] = {
            **slot_data,
            "pipeline": pipeline,
            "promotion": promotion_tag_for_mode(mode),
        }
        state["open_war"] = wars[t_up]

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
    entry_ib_fill: float = 0.0,
    exit_reason: str = "",
    spread_pct: float = 0.0,
) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg):
        return {}
    state = load_state(cfg)
    t = ticker.upper()
    use_lab, open_slot = _resolve_open_slot(state, t)

    v_fill, slip = apply_slippage_overlay(
        cfg, side="SELL", quote=ib_fill or quote, shares=shares, ticker=ticker,
        spread_pct=spread_pct,
    )
    sh = int(open_slot.get("shares", 0) or shares or 0) if open_slot else int(shares or 0)
    proceeds = v_fill * sh
    exit_comm = _commission_usd(cfg, proceeds)
    nav = float(state.get("nav", operating_capital_usd(cfg)) or operating_capital_usd(cfg))

    if open_slot:
        entry_v = float(open_slot.get("entry", 0) or 0)
        entry_comm = float(open_slot.get("comm", 0) or 0)
        gross = (v_fill - entry_v) * sh if entry_v > 0 else None
    elif entry_ib_fill > 0:
        entry_v = float(entry_ib_fill)
        entry_comm = _commission_usd(cfg, entry_v * sh)
        gross = (v_fill - entry_v) * sh
        log.warning(
            f"  ⚠️ WAR EXIT {t}: no open slot — PnL from IB fills "
            f"entry=${entry_v:.4f} exit=${v_fill:.4f}"
        )
    else:
        entry_v = 0.0
        entry_comm = 0.0
        gross = None
        log.warning(f"  ⚠️ WAR EXIT {t}: ghost exit — no slot, no IB entry fill")

    if gross is None:
        max_abs = max(nav * 0.35, sh * max(v_fill, 0.01) * 0.25)
        if entry_ib_fill > 0 and v_fill > 0 and sh > 0:
            gross = (v_fill - entry_ib_fill) * sh
            entry_v = entry_ib_fill
            entry_comm = _commission_usd(cfg, entry_v * sh)
        elif abs(pnl_usd_ib) > 0 and abs(pnl_usd_ib) <= max_abs:
            gross = float(pnl_usd_ib)
            log.warning(f"  ⚠️ WAR EXIT {t}: using capped IB PnL ${pnl_usd_ib:+.2f}")
        else:
            log.warning(
                f"  ⚠️ WAR EXIT {t}: skipping ledger — bogus PnL "
                f"(pnl_usd_ib=${pnl_usd_ib:+.2f} cap=${max_abs:.2f})"
            )
            return {"skipped": True, "reason": "ghost_exit_no_slot", "ticker": t}
    net = gross - entry_comm - exit_comm
    max_trip_loss = nav * 0.50
    if net < -max_trip_loss:
        log.warning(
            f"  ⚠️ WAR EXIT {t}: capping loss ${net:+.2f} → ${-max_trip_loss:+.2f}"
        )
        net = -max_trip_loss

    if use_lab:
        state["lab_cash"] = float(state.get("lab_cash", 0)) + proceeds - exit_comm
        state["lab_settled"] = float(state.get("lab_settled", 0)) + proceeds - exit_comm
        state["lab_round_trips_today"] = int(state.get("lab_round_trips_today", 0)) + 1
        state["session_pnl_lab"] = float(state.get("session_pnl_lab", 0)) + net
        _clear_open_slot(state, t, use_lab=True)
        state["lab_nav"] = float(state.get("lab_cash", 0))
    else:
        state["cash"] = float(state.get("cash", 0)) + proceeds - exit_comm
        if entry_v > 0:
            state["deployed_usd"] = max(0.0, float(state.get("deployed_usd", 0)) - entry_v * sh)
        _schedule_settlement(state, proceeds - exit_comm, cfg)
        state["round_trips_today"] = int(state.get("round_trips_today", 0)) + 1
        state["fee_drag_today"] = float(state.get("fee_drag_today", 0)) + exit_comm
        state["session_pnl_war"] = float(state.get("session_pnl_war", 0)) + net
        state["nav"] = float(state.get("cash", 0)) + float(state.get("deployed_usd", 0))
        _clear_open_slot(state, t, use_lab=False)

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
