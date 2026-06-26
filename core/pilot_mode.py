#!/usr/bin/env python3
"""
core/pilot_mode.py — Full Pilot Mode orchestration.

Wires veteran XP, cognitive autopilot, consciousness, incremental training,
dynamic notifications, and live IB scanning into one cohesive "awake pilot"
that learns forward-only (never retrains on stale data).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

import numpy as np

from core.config import BotConfig
from core.notify import log
from core.paper_mode import account_equity as resolve_account_equity, is_paper_free_learning

if TYPE_CHECKING:
    from core.pilot_experience import PilotExperienceSystem
    from core.cognitive_autopilot import CognitiveAutopilot
    from core.consciousness import AIConsciousness
    from core.scanner import StockScanner

TRAINED_HASHES_PATH = Path("models/trained_record_hashes.jsonl")
def is_tradeable_ticker(ticker: str, exchange: str = "", cfg: Optional[BotConfig] = None) -> bool:
    """Exclude pink sheets, OTC, distressed — major US listings for profit hunting."""
    from core.universe_filter import passes_profit_hunt_universe
    cfg = cfg or BotConfig()
    ok, _ = passes_profit_hunt_universe(cfg, ticker, exchange)
    return ok


def get_live_scan_universe(
    scanner: "StockScanner",
    connector,
    cfg: BotConfig,
    *,
    startup: bool = False,
    skip_ib_scanner: bool = False,
) -> Tuple[List[str], str]:
    """Live IB scanner with instant emergency fallback so startup never blocks."""
    if not getattr(cfg, "USE_LIVE_IB_SCANNER", True):
        log.warning("USE_LIVE_IB_SCANNER is off — universe empty (static fallback disabled)")
        return [], "none"

    defer = skip_ib_scanner or (
        startup and getattr(cfg, "SCAN_DEFER_IB_ON_STARTUP", False)
    )
    if defer:
        log.info("⚡ Instant startup universe — IB live scanner deferred")
        from core.scanner import emergency_scan_universe
        tickers = emergency_scan_universe(connector, cfg, reason="deferred")
        return tickers, "startup_curated"

    tickers: List[str] = []
    warmup = float(getattr(cfg, "IB_SCANNER_WARMUP_SEC", 3.0))
    if warmup > 0 and (startup or not skip_ib_scanner):
        log.info(f"🔍 IB scanner warmup {warmup:.0f}s (Gateway sync)…")
        time.sleep(warmup)
    retries = int(
        getattr(cfg, "IB_SCANNER_STARTUP_RETRIES", 1) if startup
        else getattr(cfg, "IB_SCANNER_RETRIES", 2)
    )
    for attempt in range(max(1, retries)):
        try:
            force = attempt > 0
            tickers = scanner.get_dynamic_universe(connector, force=force) or []
        except Exception as exc:
            log.warning(f"IB dynamic scanner error (attempt {attempt + 1}): {exc}")
            tickers = []
        if tickers:
            break
        if attempt < retries - 1:
            wait = float(getattr(cfg, "IB_SCANNER_RETRY_WAIT_SEC", 1.0))
            log.info(f"IB scanner empty — retry {attempt + 2}/{retries} in {wait:.0f}s…")
            time.sleep(wait)

    seen = set()
    out = []
    for t in tickers:
        if t not in seen and is_tradeable_ticker(t, cfg=cfg):
            seen.add(t)
            out.append(t)

    try:
        from core.market_data_learning import filter_tradeable_tickers
        out = filter_tradeable_tickers(cfg, out)
    except Exception:
        pass

    if not out and getattr(cfg, "SCAN_EMERGENCY_FALLBACK", True):
        from core.scanner import emergency_scan_universe
        out = emergency_scan_universe(connector, cfg, reason="empty")
        return out[: effective_scan_universe_max(cfg)], "emergency_fallback"
    elif not out:
        log.warning(
            "🔴 Live IB scanner returned 0 tickers — no fallback. "
            "Check IB Gateway login, market hours, and scanner subscription."
        )
        return [], "none"
    return out[: effective_scan_universe_max(cfg)], "ib_live"


def is_ai_unlimited(cfg: BotConfig) -> bool:
    if bool(getattr(cfg, "AI_UNLIMITED_MODE", False)):
        return True
    try:
        from core.ai_learning_policy import learn_dont_block
        return learn_dont_block(cfg)
    except Exception:
        return False


def ai_full_capital_access(cfg: BotConfig) -> bool:
    """AI may deploy from full IB cash/equity — no $1k training-wheel cap."""
    if getattr(cfg, "USE_FIXED_DEPLOY_CAP", False):
        return False
    if not getattr(cfg, "AI_FULL_CAPITAL_ACCESS", True):
        return False
    return is_ai_unlimited(cfg) or is_paper_free_learning(cfg)


def is_ai_council_mode(cfg: BotConfig) -> bool:
    """All trading decisions flow through non-blocking Ollama+PPO council."""
    return bool(
        getattr(cfg, "AI_COUNCIL_ALL_DECISIONS", True)
        and getattr(cfg, "AI_FULL_CONTROL", True)
        and getattr(cfg, "LIVE_AI_PIPELINE_ENABLED", True)
    )


def effective_max_locked_targets(cfg: BotConfig) -> int:
    if is_ai_unlimited(cfg):
        from core.ai_session_limits import get_session_limit, should_ai_define_limits
        from core.fast_execution import ai_fast_execution, stream_watch_cap
        if should_ai_define_limits(cfg):
            base = int(get_session_limit(cfg, "watch_pool", getattr(cfg, "AI_MAX_LOCKED_TARGETS", 30)))
        else:
            base = int(getattr(cfg, "AI_MAX_LOCKED_TARGETS", 30))
        if ai_fast_execution(cfg):
            return min(base, stream_watch_cap(cfg))
        return base
    return int(getattr(cfg, "MAX_LOCKED_TARGETS", 5))


def effective_max_concurrent_positions(cfg: BotConfig) -> int:
    if is_ai_unlimited(cfg):
        from core.ai_session_limits import get_session_limit, should_ai_define_limits
        if should_ai_define_limits(cfg):
            return int(get_session_limit(cfg, "max_positions", getattr(cfg, "AI_MAX_CONCURRENT_POSITIONS", 50)))
        return int(getattr(cfg, "AI_MAX_CONCURRENT_POSITIONS", 50))
    return int(getattr(cfg, "MAX_CONCURRENT_POSITIONS", 5))


def effective_min_lock_score(cfg: BotConfig) -> float:
    if is_ai_unlimited(cfg):
        from core.ai_session_limits import get_session_limit, should_ai_define_limits
        if should_ai_define_limits(cfg):
            return float(get_session_limit(cfg, "min_lock_score", getattr(cfg, "AI_MIN_LOCK_SCORE", 0.0)))
        return float(getattr(cfg, "AI_MIN_LOCK_SCORE", 0.0))
    return float(getattr(cfg, "MIN_LOCK_SCORE", 30.0))


def effective_min_lock_candidates(cfg: BotConfig) -> int:
    return 1 if is_ai_unlimited(cfg) else int(getattr(cfg, "MIN_LOCK_CANDIDATES", 2))


def effective_scan_universe_max(cfg: BotConfig) -> int:
    if is_ai_unlimited(cfg):
        return int(getattr(cfg, "AI_SCAN_UNIVERSE_MAX", 80))
    return int(getattr(cfg, "SCAN_UNIVERSE_MAX", 30))


def effective_min_cash_reserve_pct(cfg: BotConfig) -> float:
    if is_ai_unlimited(cfg):
        return float(getattr(cfg, "AI_MIN_CASH_RESERVE_PCT", 0.0))
    return float(getattr(cfg, "MIN_CASH_RESERVE_PCT", 0.05))


def effective_max_shares_per_trade(cfg: BotConfig) -> int:
    if is_ai_unlimited(cfg):
        return int(getattr(cfg, "AI_MAX_SHARES_PER_TRADE", 100_000))
    return int(getattr(cfg, "MAX_SHARES_PER_TRADE", 2000))


def effective_prefetch_top_n(cfg: BotConfig) -> int:
    if is_ai_unlimited(cfg):
        # Prefetch fewer names so Ollama has capacity for sync entry calls on spikes
        return min(8, effective_max_locked_targets(cfg))
    return int(getattr(cfg, "LIVE_AI_PREFETCH_TOP_N", 3))


def effective_min_position_hold_sec(cfg: BotConfig) -> float:
    if is_ai_unlimited(cfg):
        return 0.0
    if getattr(cfg, "PROFIT_HUNT_PRIMARY_GOAL", True) and getattr(cfg, "PROFIT_HUNT_SKIP_MIN_HOLD", True):
        return 0.0
    return float(getattr(cfg, "MIN_POSITION_HOLD_SEC", 45.0))


def effective_min_hold_for_exit(cfg: BotConfig, pnl_pct: float = 0.0, reason: str = "") -> float:
    """Min hold for exits — profit hunts bypass when primary goal is on."""
    try:
        from core.profit_hunting import profit_exit_bypasses_hold
        if profit_exit_bypasses_hold(cfg, pnl_pct, reason):
            return 0.0
    except Exception:
        pass
    return effective_min_position_hold_sec(cfg)


def get_effective_confidence_threshold(
    cfg: BotConfig,
    pilot: Optional["PilotExperienceSystem"] = None,
) -> float:
    """Pilot rank modulates AI gate — veterans trust themselves more."""
    base = float(cfg.CONFIDENCE_THRESHOLD)
    if not getattr(cfg, "PILOT_MODE_ENABLED", True) or pilot is None:
        return base
    pilot_thr = pilot.get_confidence_threshold()
    # Blend: cfg floor + pilot progression (veterans lower threshold)
    return max(0.38, min(base, pilot_thr))


def get_deploy_usd(cfg: BotConfig, pilot: Optional["PilotExperienceSystem"] = None) -> float:
    """Deploy per stock — paper free / AI unlimited use live equity, not fixed $1k."""
    eq = resolve_account_equity(cfg)
    if ai_full_capital_access(cfg):
        reserve = effective_min_cash_reserve_pct(cfg)
        return max(0.0, eq * (1.0 - reserve))
    if is_paper_free_learning(cfg):
        return eq * 0.95
    base = float(getattr(cfg, "DEPLOY_PER_STOCK_USD", 1000.0))
    if not getattr(cfg, "PILOT_MODE_ENABLED", True) or pilot is None:
        return base
    mult = pilot.get_max_position_size()
    cap = float(getattr(cfg, "PILOT_MAX_DEPLOY_USD", 2000.0))
    return min(base * mult, cap)


def get_ai_deploy_budget(
    cfg: BotConfig,
    pilot: Optional["PilotExperienceSystem"] = None,
    account_equity: float = 0.0,
    available_cash: float = 0.0,
    open_positions: int = 0,
) -> float:
    """Max USD deployable for one new position — fixed cap or equity-based slots."""
    if getattr(cfg, "USE_FIXED_DEPLOY_CAP", False):
        budget = get_deploy_usd(cfg, pilot)
        max_trade = float(getattr(cfg, "MAX_TRADE_SIZE_USD", 1000.0))
        if max_trade > 0:
            budget = min(budget, max_trade)
        return max(0.0, budget)

    max_conc = effective_max_concurrent_positions(cfg)
    slots_left = max(1, max_conc - open_positions)
    reserve_pct = effective_min_cash_reserve_pct(cfg)
    cash_basis = available_cash if available_cash > 0 else account_equity
    deployable = max(0.0, cash_basis * (1.0 - reserve_pct))
    per_slot = deployable / slots_left

    full_cap = ai_full_capital_access(cfg)
    if full_cap:
        # Flat account → full deployable cash on next entry (no 1/N training-wheel split)
        if open_positions < 1:
            per_slot = deployable
        elif slots_left <= 1:
            per_slot = deployable
        else:
            per_slot = deployable / slots_left
        return max(0.0, per_slot)

    limits = getattr(cfg, "_ai_session_limits", None) or {}
    deploy_pct_slot = limits.get("deploy_pct_per_slot")
    if deploy_pct_slot and account_equity > 0:
        per_slot = min(per_slot, account_equity * float(deploy_pct_slot))

    max_pct = float(getattr(cfg, "AI_MAX_DEPLOY_PCT", 0.0))
    if max_pct > 0 and account_equity > 0:
        per_slot = min(per_slot, account_equity * max_pct)

    max_trade = float(getattr(cfg, "MAX_TRADE_SIZE_USD", 0))
    if max_trade > 0:
        per_slot = min(per_slot, max_trade)
    elif is_paper_free_learning(cfg) and account_equity > 0:
        per_slot = min(per_slot, account_equity * 0.95)

    return max(0.0, per_slot)


def get_trade_risk_usd(cfg: BotConfig, account_equity: float = 0.0) -> float:
    """Per-trade risk budget — paper free uses full equity %; live uses capped risk."""
    if getattr(cfg, "USE_FIXED_RISK_CAP", False):
        return float(getattr(cfg, "HARD_STOP_USD", 50.0))
    try:
        from core.ai_session_limits import get_ai_risk_usd, should_ai_define_limits
        if should_ai_define_limits(cfg):
            ai_risk = get_ai_risk_usd(cfg, account_equity)
            if ai_risk is not None and ai_risk > 0:
                return ai_risk
    except Exception:
        pass
    if account_equity > 0:
        return cfg.risk_amount_usd(account_equity)
    if is_paper_free_learning(cfg):
        return cfg.risk_amount_usd(resolve_account_equity(cfg))
    return float(getattr(cfg, "MAX_RISK_PER_TRADE_USD", 75.0))


def snapshot_features(feature_buffer, cfg: BotConfig) -> List[float]:
    """Serialize feature window for experience buffer / incremental training."""
    if not feature_buffer:
        return []
    try:
        window = np.array(list(feature_buffer)[-cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
        return window.tolist()
    except Exception:
        return []


def send_dynamic_notification(
    notifier,
    autopilot: Optional["CognitiveAutopilot"],
    event_type: str,
    context: Dict[str, Any],
    fallback_msg: str,
    ai_commander=None,
    consciousness=None,
    pilot=None,
) -> None:
    """AI-crafted Telegram alert — Ollama pilot voice, structured fallback."""
    cfg = getattr(notifier, "cfg", None)
    if cfg is None:
        from core.config import BotConfig
        cfg = BotConfig()
    sync_events = frozenset({"session_close", "session_shutdown", "market_close"})
    if (
        getattr(cfg, "TELEGRAM_ASYNC_DURING_SESSION", True)
        and event_type not in sync_events
    ):
        try:
            from core.async_utils import get_background_worker
            get_background_worker()._executor.submit(
                lambda: _send_dynamic_notification_impl(
                    notifier, autopilot, event_type, context, fallback_msg,
                    ai_commander, consciousness, pilot,
                )
            )
            return
        except Exception:
            pass
    _send_dynamic_notification_impl(
        notifier, autopilot, event_type, context, fallback_msg,
        ai_commander, consciousness, pilot,
    )


def _send_dynamic_notification_impl(
    notifier,
    autopilot,
    event_type: str,
    context: Dict[str, Any],
    fallback_msg: str,
    ai_commander=None,
    consciousness=None,
    pilot=None,
) -> None:
    from core.ai_notifier import send_smart_telegram
    send_smart_telegram(
        notifier, event_type, context, fallback_msg,
        ai_commander=ai_commander,
        autopilot=autopilot,
        consciousness=consciousness,
        pilot=pilot,
    )


def observe_trade_everywhere(
    trade: Dict[str, Any],
    autopilot: Optional["CognitiveAutopilot"] = None,
    consciousness: Optional["AIConsciousness"] = None,
    pilot: Optional["PilotExperienceSystem"] = None,
    cfg: Optional[BotConfig] = None,
) -> None:
    """Single hook so all brains learn from the same flight."""
    from core.architecture_epoch import stamp_trade
    trade = stamp_trade(trade, cfg)
    if autopilot:
        try:
            autopilot.observe_trade(trade)
        except Exception:
            pass
    if consciousness:
        try:
            consciousness.observe_trade(trade)
        except Exception:
            pass
    if pilot and trade.get("pnl_usd") is not None:
        try:
            from core.git_sync import pilot_experience_to_git
            pilot_experience_to_git(pilot)
        except Exception:
            pass


def _record_hash(record_id: str) -> None:
    TRAINED_HASHES_PATH.parent.mkdir(exist_ok=True)
    with open(TRAINED_HASHES_PATH, "a") as f:
        f.write(json.dumps({"id": record_id, "ts": datetime.now(timezone.utc).isoformat()}) + "\n")


def _load_trained_hashes() -> set:
    if not TRAINED_HASHES_PATH.exists():
        return set()
    out = set()
    try:
        with open(TRAINED_HASHES_PATH) as f:
            for line in f:
                if line.strip():
                    out.add(json.loads(line).get("id", ""))
    except Exception:
        pass
    return out


def get_new_buffer_records(records: List[Dict]) -> List[Dict]:
    """Forward-only training: skip records already used in a training session."""
    seen = _load_trained_hashes()
    fresh = []
    for r in records:
        rid = r.get("timestamp", "") + "|" + str(r.get("ticker", "")) + "|" + str(r.get("source", ""))
        if rid and rid not in seen:
            fresh.append(r)
    return fresh


def mark_records_trained(records: List[Dict]) -> None:
    for r in records:
        rid = r.get("timestamp", "") + "|" + str(r.get("ticker", "")) + "|" + str(r.get("source", ""))
        if rid:
            _record_hash(rid)


def maybe_incremental_train(
    cfg: BotConfig,
    trades_today: int,
    consciousness: Optional["AIConsciousness"] = None,
    autopilot: Optional["CognitiveAutopilot"] = None,
) -> bool:
    """
    Train only on NEW experience after N trades or when autopilot requests it.
    Never replays full backtest corpus on every tick.
    """
    if not getattr(cfg, "INCREMENTAL_TRAINING_ENABLED", True):
        return False
    every_n = int(getattr(cfg, "INCREMENTAL_TRAIN_EVERY_N_TRADES", 3))
    if trades_today > 0 and trades_today % every_n != 0:
        should, _ = (False, {})
        if autopilot:
            try:
                should, _ = autopilot.should_train()
            except Exception:
                pass
        if not should:
            return False

    if consciousness and hasattr(consciousness, "should_train"):
        try:
            if not consciousness.should_train():
                return False
        except Exception:
            pass

    try:
        from core.experience_buffer import load_recent
        from core.online_trainer import run_incremental_training

        recent = load_recent(n=500)
        fresh = get_new_buffer_records(recent)
        min_new = int(getattr(cfg, "INCREMENTAL_TRAIN_MIN_NEW_RECORDS", 2))
        feature_rich = [r for r in fresh if r.get("features")]
        if len(feature_rich) < min_new and len(fresh) < min_new:
            log.debug(f"Incremental train skipped: {len(fresh)} new records < {min_new}")
            return False

        log.info(f"🧠 PILOT INCREMENTAL TRAIN: {len(fresh)} new records")
        ok = run_incremental_training(cfg, fresh_records=fresh)
        if ok:
            mark_records_trained(fresh)
            if consciousness:
                try:
                    consciousness.reflect()
                except Exception:
                    pass
        return ok
    except Exception as exc:
        log.debug(f"Incremental train: {exc}")
        return False


def mtf_score_bonus(df_1m, df_5m, df_15m) -> tuple[float, str]:
    """Multi-timeframe alignment bonus matching human trader workflow."""
    bonus = 0.0
    notes = []
    for label, df in (("1m", df_1m), ("5m", df_5m), ("15m", df_15m)):
        if df is None or len(df) < 20:
            continue
        closes = df["close"].values
        sma = float(np.mean(closes[-20:]))
        if float(closes[-1]) > sma:
            bonus += 3.0
            notes.append(f"{label}_up")
        vol = df["volume"].values
        if len(vol) > 5 and float(vol[-1]) > float(np.mean(vol[-10:-1])) * 1.2:
            bonus += 2.0
            notes.append(f"{label}_vol")
    return bonus, " | ".join(notes[:4])


def generative_think(
    cfg: BotConfig,
    autopilot: Optional["CognitiveAutopilot"],
    prompt: str,
    *,
    force: bool = False,
) -> str:
    """
    Ambient LLM commentary — DISABLED during RTH to save API budget.

    Use council hotline (entry/exit) for trade decisions. End-of-day statement
    uses COUNCIL daily_digest (one call after close). This path is legacy only.
    """
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
        return ""
    from core.council_budget import PURPOSE_GENERATIVE, should_use_council_api
    ok, reason = should_use_council_api(cfg, PURPOSE_GENERATIVE, force=force)
    if not ok:
        log.debug(f"Generative think skipped: {reason}")
        return ""
    if not autopilot or not getattr(autopilot, "core", None):
        return ""
    core = autopilot.core
    if core and getattr(core, "ollama", None):
        try:
            return (core.think(prompt) or "").strip()
        except Exception as exc:
            log.debug(f"Generative think: {exc}")
    return ""


def generative_position_decision(
    cfg: BotConfig,
    autopilot: Optional["CognitiveAutopilot"],
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Live position management via Ollama — widen/tighten stops, raise TP, or exit.
    Returns dict with keys: action, stop, target, reason (action=HOLD|WIDEN_STOP|...).
    """
    default = {"action": "HOLD", "stop": ctx.get("stop"), "target": ctx.get("target"), "reason": "default"}
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
        return default
    prompt = (
        "You are HANOON live scalp pilot managing an open position. "
        "Decide using full computational analysis AND gut feel.\n"
        f"Ticker: {ctx.get('ticker')} | Entry: ${ctx.get('entry', 0):.4f} | "
        f"Now: ${ctx.get('price', 0):.4f} | Peak: ${ctx.get('peak', 0):.4f}\n"
        f"Unrealized P&L: ${ctx.get('pnl_usd', 0):+.2f} ({ctx.get('pnl_pct', 0):+.2f}%) | "
        f"Stop: ${ctx.get('stop', 0):.4f} | Target: ${ctx.get('target', 0):.4f}\n"
        f"Hard risk floor: ${ctx.get('hard_floor', 0):.4f} | "
        f"Vol ratio: {ctx.get('vol_ratio', 1):.2f}x | Regime: {ctx.get('regime', 'unknown')}\n"
        "Does this trade still feel alive in your gut, or is momentum dying?\n"
        "Rules:\n"
        "- WIDEN_STOP: lower stop for volatility noise ONLY if still above hard_floor\n"
        "- TIGHTEN_STOP: raise stop to lock profit\n"
        "- RAISE_TP: extend take-profit when momentum strong\n"
        "- EXIT: close now if gut says momentum is dead, slippage risk high, "
        "OR a clean spike top just printed (profit hunt — sell INTO the burst)\n"
        "- HOLD: no change\n"
        "Profit hunting: opportunistically take profit on spike tops + volume bursts; "
        "learn SPIKE_TOP_MIN_GAIN_PCT / SPIKE_TOP_MIN_VOL_RATIO from outcomes.\n"
        'Reply ONLY valid JSON: {"action":"HOLD|WIDEN_STOP|TIGHTEN_STOP|RAISE_TP|EXIT",'
        '"stop":0.00,"target":0.00,"gut_feel":0.0-1.0,"intuition":"gut read","reason":"brief"}'
    )
    raw = generative_think(cfg, autopilot, prompt)
    if not raw:
        return default
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            action = str(parsed.get("action", "HOLD")).upper()
            if action not in ("HOLD", "WIDEN_STOP", "TIGHTEN_STOP", "RAISE_TP", "EXIT"):
                action = "HOLD"
            from core.ai_commander import _parse_float_price
            default_stop = float(ctx.get("stop", 0) or 0)
            default_target = float(ctx.get("target", 0) or 0)
            return {
                "action": action,
                "stop": _parse_float_price(parsed.get("stop"), default_stop),
                "target": _parse_float_price(parsed.get("target"), default_target),
                "reason": str(parsed.get("reason", ""))[:120],
            }
    except Exception as exc:
        log.debug(f"Position AI parse: {exc}")
    return default
