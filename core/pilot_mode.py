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
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import numpy as np

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.pilot_experience import PilotExperienceSystem
    from core.cognitive_autopilot import CognitiveAutopilot
    from core.consciousness import AIConsciousness
    from core.scanner import StockScanner

TRAINED_HASHES_PATH = Path("models/trained_record_hashes.jsonl")
OTC_EXCLUDED_SUFFIXES = frozenset({".PK", ".OB", ".OTC", ".PINK"})
OTC_EXCLUDED_TICKERS = frozenset({
    "CEI", "NKLA", "GOEV", "WKHS", "ARRY", "X",
})


def is_tradeable_ticker(ticker: str, exchange: str = "") -> bool:
    """Exclude pink sheets, OTC, and known bad contracts."""
    t = (ticker or "").upper().strip()
    if not t or len(t) > 5:
        return False
    if t in OTC_EXCLUDED_TICKERS:
        return False
    ex = (exchange or "").upper()
    if any(x in ex for x in ("PINK", "OTC", "GREY", "GRAY")):
        return False
    return True


def get_live_scan_universe(
    scanner: "StockScanner",
    connector,
    cfg: BotConfig,
) -> List[str]:
    """Live IB scanner ONLY — no static ticker list fallback."""
    if not getattr(cfg, "USE_LIVE_IB_SCANNER", True):
        log.warning("USE_LIVE_IB_SCANNER is off — universe empty (static fallback disabled)")
        return []

    tickers: List[str] = []
    retries = int(getattr(cfg, "IB_SCANNER_RETRIES", 3))
    for attempt in range(retries):
        try:
            force = attempt > 0
            tickers = scanner.get_dynamic_universe(connector, force=force) or []
        except Exception as exc:
            log.warning(f"IB dynamic scanner error (attempt {attempt + 1}): {exc}")
            tickers = []
        if tickers:
            break
        if attempt < retries - 1:
            log.info("IB scanner empty — retrying live fetch in 3s...")
            time.sleep(3)

    seen = set()
    out = []
    for t in tickers:
        if t not in seen and is_tradeable_ticker(t):
            seen.add(t)
            out.append(t)

    if not out:
        log.warning(
            "🔴 Live IB scanner returned 0 tickers — no static fallback. "
            "Check IB Gateway login, market hours, and scanner subscription."
        )
    return out[: getattr(cfg, "SCAN_UNIVERSE_MAX", 80)]


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
    """$1000 base deploy; veterans may scale up within guardrail cap."""
    base = float(getattr(cfg, "DEPLOY_PER_STOCK_USD", 1000.0))
    if not getattr(cfg, "PILOT_MODE_ENABLED", True) or pilot is None:
        return base
    mult = pilot.get_max_position_size()
    cap = float(getattr(cfg, "PILOT_MAX_DEPLOY_USD", 2000.0))
    return min(base * mult, cap)


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
) -> None:
    """Single hook so all brains learn from the same flight."""
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
) -> str:
    """Invoke Ollama reasoning when generative thinking is enabled."""
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
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
        "- EXIT: close now if gut says momentum is dead or slippage risk high\n"
        "- HOLD: no change\n"
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
