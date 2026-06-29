#!/usr/bin/env python3
"""
core/slow_coach.py — Lane B coach: observe every session, apply slowly.

Accumulates evidence from live trips + commander replay recommendations.
Applies at most one small bounded param change per interval (default weekly).
Auto-rollback if post-change sessions worsen. Live sniper/war stack unchanged.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

STATE_PATH = Path("models/slow_coach_state.json")
SIGNALS_PATH = Path("models/coach_signal_scores.json")
QUEUE_PATH = Path("models/slow_coach_queue.json")
APPLIED_PATH = Path("models/slow_coach_applied.json")
SHADOW_LOG_PATH = Path("models/coach_shadow_log.jsonl")

# Params slow-coach may drip — not architecture / war / sniper switches
SLOW_APPLY_WHITELIST: Dict[str, Dict[str, Any]] = {
    "CONFIDENCE_THRESHOLD": {"delta": 0.02, "kind": "float"},
    "MIN_PROFIT_PROBABILITY": {"delta": 0.03, "kind": "float"},
    "STAGNATION_EXIT_SEC": {"delta": 20.0, "kind": "float", "faster": "decrease"},
    "SCALP_PROFIT_GIVEBACK_PCT": {"delta": 0.05, "kind": "float", "faster": "decrease"},
    "SPIKE_SKIP_SEC": {"delta": 3.0, "kind": "float"},
    "ENTRY_QUALITY_BLEND_WEIGHT": {"delta": 0.05, "kind": "float"},
    "AI_SPIKE_FAST_MIN_SCORE": {"delta": 3.0, "kind": "float"},
    "AI_SPIKE_FAST_MIN_RATIO": {"delta": 0.05, "kind": "float"},
}

FROZEN_PARAMS = frozenset({
    "WAR_SNIPER_MODE", "MAX_CONCURRENT_POSITIONS", "AI_MAX_CONCURRENT_POSITIONS",
    "MAX_DAILY_LOSS_PCT", "MAX_WEEKLY_LOSS_PCT", "PAPER_TRADING",
})

SIGNAL_TO_PARAM: Dict[str, Tuple[str, str]] = {
    "hope_hold": ("STAGNATION_EXIT_SEC", "decrease"),
    "tail_loss": ("MIN_PROFIT_PROBABILITY", "increase"),
    "fee_bleed": ("CONFIDENCE_THRESHOLD", "increase"),
    "weak_setup": ("MIN_PROFIT_PROBABILITY", "increase"),
    "council_churn": ("SPIKE_SKIP_SEC", "decrease"),
}

BYPASS_SLOW_SOURCES = frozenset({"slow_coach", "coach_rollback", "manual"})


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def coach_lane_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return _env_bool("COACH_LANE_ENABLED", "true")


def coach_slow_apply_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return coach_lane_enabled(cfg) and _env_bool("COACH_SLOW_APPLY", "true")


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def observe_round_trip(cfg: BotConfig, trip: Dict[str, Any], equity: float) -> None:
    """Lightweight per-trip signal bump (hot path — no I/O heavy work)."""
    if not coach_lane_enabled(cfg):
        return
    try:
        from core.commander_replay import classify_live_trip
        tag = classify_live_trip(trip, equity)
        if tag in ("data_corrupt", "neutral", "good_cut", "loss_other"):
            return
        scores = _read_json(SIGNALS_PATH, {})
        rec = scores.get(tag, {"count": 0, "sessions": [], "pnl_usd": 0.0})
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["pnl_usd"] = round(float(rec.get("pnl_usd", 0)) + float(trip.get("pnl_usd", 0)), 2)
        day = str(trip.get("timestamp", ""))[:10]
        sessions = list(rec.get("sessions") or [])
        if day and day not in sessions:
            sessions.append(day)
            sessions = sessions[-30:]
        rec["sessions"] = sessions
        rec["last_ticker"] = trip.get("ticker")
        rec["last_at"] = datetime.now(timezone.utc).isoformat()
        scores[tag] = rec
        _write_json(SIGNALS_PATH, scores)
    except Exception as exc:
        log.debug(f"Coach observe trip: {exc}")


def log_shadow_skip(
    cfg: BotConfig,
    *,
    ticker: str,
    reason: str,
    scan_score: float = 0.0,
    spike_ratio: float = 1.0,
) -> None:
    if not coach_lane_enabled(cfg) or not reason:
        return
    _append_jsonl(SHADOW_LOG_PATH, {
        "ticker": ticker,
        "reason": reason,
        "scan_score": scan_score,
        "spike_ratio": spike_ratio,
        "shadow_only": True,
    })


def queue_mutation(
    cfg: BotConfig,
    param: str,
    value: Any,
    reason: str,
    source: str = "queued",
) -> bool:
    """Queue a param change for slow-coach drip (does not apply immediately)."""
    if not coach_slow_apply_enabled(cfg):
        return False
    param = str(param).strip()
    if param in FROZEN_PARAMS or param not in SLOW_APPLY_WHITELIST:
        return False
    q = _read_json(QUEUE_PATH, {"items": []})
    items: List[Dict[str, Any]] = list(q.get("items") or [])
    items.append({
        "param": param,
        "value": value,
        "reason": reason[:300],
        "source": source,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    })
    items = items[-50:]
    _write_json(QUEUE_PATH, {"items": items, "updated_at": datetime.now(timezone.utc).isoformat()})
    log.debug(f"🎓 Coach queued {param}={value} ({source})")
    return True


def queue_plan_mutations(
    cfg: BotConfig,
    plan: Dict[str, Any],
    source: str,
) -> Dict[str, Any]:
    queued = 0
    for mut in (plan.get("mutations") or [])[:3]:
        if queue_mutation(
            cfg,
            str(mut.get("param", "")),
            mut.get("value"),
            str(mut.get("reason", "")),
            source=source,
        ):
            queued += 1
    if queued:
        log.info(f"🎓 Coach queued {queued} mutation(s) from {source} — slow drip")
    return {"queued": queued, "applied": [], "rejected": []}


def _evidence_threshold_met(signal: str, scores: Dict[str, Any]) -> bool:
    rec = scores.get(signal) or {}
    min_sessions = _env_int("COACH_EVIDENCE_MIN_SESSIONS", 3)
    min_count = _env_int("COACH_EVIDENCE_MIN_TRIPS", 4)
    sessions = rec.get("sessions") or []
    count = int(rec.get("count", 0))
    return len(sessions) >= min_sessions and count >= min_count


def _top_signal(scores: Dict[str, Any]) -> Optional[str]:
    priority = ["tail_loss", "hope_hold", "fee_bleed", "weak_setup", "council_churn"]
    for sig in priority:
        if _evidence_threshold_met(sig, scores):
            return sig
    return None


def _proposed_delta(param: str, direction: str) -> Optional[float]:
    spec = SLOW_APPLY_WHITELIST.get(param)
    if not spec:
        return None
    delta = float(spec.get("delta", 0))
    if direction == "decrease":
        return -delta
    return delta


def _next_value(cfg: BotConfig, param: str, direction: str) -> Optional[float]:
    if not hasattr(cfg, param):
        return None
    cur = float(getattr(cfg, param))
    delta = _proposed_delta(param, direction)
    if delta is None:
        return None
    return cur + delta


def _can_apply_now(state: Dict[str, Any]) -> bool:
    min_gap = _env_float("COACH_APPLY_MIN_INTERVAL_SEC", 604800.0)
    last = float(state.get("last_apply_ts", 0) or 0)
    return (time.time() - last) >= min_gap


def maybe_slow_apply(cfg: BotConfig) -> Optional[Dict[str, Any]]:
    """Apply at most one whitelisted param change if evidence + interval OK."""
    if not coach_slow_apply_enabled(cfg):
        return None

    state = _read_json(STATE_PATH, {})
    if not _can_apply_now(state):
        return None

    scores = _read_json(SIGNALS_PATH, {})
    signal = _top_signal(scores)
    if not signal:
        return None

    mapping = SIGNAL_TO_PARAM.get(signal)
    if not mapping:
        return None
    param, direction = mapping

    # Prefer queued mutation for same param if present
    q = _read_json(QUEUE_PATH, {"items": []})
    items: List[Dict[str, Any]] = list(q.get("items") or [])
    target_value = None
    reason = f"Coach slow apply — repeated {signal} across sessions"
    source = "slow_coach"
    for i, item in enumerate(items):
        if str(item.get("param")) == param:
            target_value = item.get("value")
            reason = str(item.get("reason", reason))
            source = str(item.get("source", source))
            items.pop(i)
            break
    if target_value is None:
        target_value = _next_value(cfg, param, direction)
    if target_value is None:
        return None

    from core.commander_learning import _apply_mutation
    from core.sniper_execution import cap_sniper_confidence_threshold

    old = getattr(cfg, param, None)
    ok, msg = _apply_mutation(cfg, param, target_value, reason, autopilot=None)
    if not ok:
        log.debug(f"Coach slow apply rejected {param}: {msg}")
        _write_json(QUEUE_PATH, {"items": items})
        return None

    cap_sniper_confidence_threshold(cfg)
    new_val = getattr(cfg, param)

    applied_rec = {
        "param": param,
        "old": old,
        "new": new_val,
        "signal": signal,
        "reason": reason,
        "source": source,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "pre_session_pnl": float(state.get("session_pnl_baseline", 0) or 0),
    }
    history = _read_json(APPLIED_PATH, {"history": []})
    hist: List[Dict[str, Any]] = list(history.get("history") or [])
    hist.append(applied_rec)
    _write_json(APPLIED_PATH, {"history": hist[-40:]})
    _write_json(QUEUE_PATH, {"items": items})

    state["last_apply_ts"] = time.time()
    state["last_apply"] = applied_rec
    state["sessions_since_apply"] = 0
    state["pnl_since_apply"] = 0.0
    _write_json(STATE_PATH, state)

    log.info(
        f"🎓 Coach slow-apply {param}: {old} → {new_val} "
        f"(signal={signal}, {reason[:60]})"
    )
    return applied_rec


def maybe_rollback(cfg: BotConfig) -> Optional[Dict[str, Any]]:
    """Revert last slow-coach change if recent sessions got worse."""
    if not coach_slow_apply_enabled(cfg):
        return None
    history = _read_json(APPLIED_PATH, {"history": []})
    hist: List[Dict[str, Any]] = list(history.get("history") or [])
    if not hist:
        return None
    last = hist[-1]
    if last.get("rolled_back"):
        return None

    state = _read_json(STATE_PATH, {})
    sessions = int(state.get("sessions_since_apply", 0) or 0)
    min_sessions = _env_int("COACH_ROLLBACK_SESSIONS", 3)
    if sessions < min_sessions:
        return None

    pnl_since = float(state.get("pnl_since_apply", 0) or 0)
    signal = str(last.get("signal", ""))
    bad = (
        (signal in ("tail_loss", "hope_hold") and pnl_since < -20)
        or (signal == "fee_bleed" and pnl_since < -10 and sessions >= min_sessions)
    )
    if not bad:
        return None

    param = str(last.get("param", ""))
    old = last.get("old")
    if not param or old is None or not hasattr(cfg, param):
        return None

    from core.commander_learning import _apply_mutation
    ok, _ = _apply_mutation(
        cfg, param, old,
        f"Coach rollback — {signal} worsened after slow apply",
        autopilot=None,
    )
    if not ok:
        return None

    last["rolled_back"] = True
    last["rollback_at"] = datetime.now(timezone.utc).isoformat()
    hist[-1] = last
    _write_json(APPLIED_PATH, {"history": hist})
    state["last_rollback_at"] = time.time()
    _write_json(STATE_PATH, state)
    log.warning(f"🎓 Coach rollback {param} → {old} (signal={signal}, pnl_since={pnl_since:.0f})")
    return last


def run_post_session_coach(
    cfg: BotConfig,
    runner: Optional[Any] = None,
    *,
    day: Optional[str] = None,
) -> Dict[str, Any]:
    """
    End-of-session Lane B batch (background-safe):
      replay → recommendations → slow apply / rollback
    """
    if not coach_lane_enabled(cfg):
        return {"ok": False, "reason": "coach_disabled"}

    equity = 1000.0
    try:
        from core.war_account import war_effective_equity, war_account_enabled
        if war_account_enabled(cfg):
            equity = max(100.0, war_effective_equity(cfg))
        elif runner is not None:
            equity = max(100.0, float(getattr(runner, "bot_nav", 1000) or 1000))
    except Exception:
        pass

    replay_result: Dict[str, Any] = {}
    try:
        from core.commander_replay import run_full_replay
        if _env_bool("COMMANDER_REPLAY_ON_SESSION_END", "true"):
            replay_result = run_full_replay(cfg, day=day, equity=equity, persist=True)
    except Exception as exc:
        log.debug(f"Coach replay: {exc}")

    state = _read_json(STATE_PATH, {})
    live_pnl = float((replay_result.get("live") or {}).get("total_pnl_usd", 0) or 0)
    state["sessions_since_apply"] = int(state.get("sessions_since_apply", 0) or 0) + 1
    state["pnl_since_apply"] = round(float(state.get("pnl_since_apply", 0) or 0) + live_pnl, 2)
    state["last_session_day"] = day
    state["last_session_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(STATE_PATH, state)

    rollback = maybe_rollback(cfg)
    applied = None if rollback else maybe_slow_apply(cfg)

    summary = {
        "ok": True,
        "day": day,
        "live_pnl_usd": live_pnl,
        "recommendations": len(replay_result.get("recommendations") or []),
        "commander_uplift_usd": (replay_result.get("commander") or {}).get("uplift_mistake_free_usd"),
        "slow_applied": applied,
        "rollback": rollback,
    }
    log.info(
        f"🎓 Coach session done — live_pnl=${live_pnl:+.0f} | "
        f"recs={summary['recommendations']} | "
        f"apply={'yes' if applied else 'no'} | rollback={'yes' if rollback else 'no'}"
    )
    return summary


def schedule_post_session_coach(
    cfg: BotConfig,
    runner: Optional[Any] = None,
    *,
    day: Optional[str] = None,
) -> None:
    """Fire-and-forget background coach lane."""
    if not coach_lane_enabled(cfg):
        return

    def _job():
        try:
            from core.market_hours import now_et
            d = day or now_et().strftime("%Y-%m-%d")
            run_post_session_coach(cfg, runner, day=d)
        except Exception as exc:
            log.debug(f"Post-session coach: {exc}")

    try:
        from core.async_utils import get_background_worker
        get_background_worker()._executor.submit(_job)
    except Exception:
        try:
            _job()
        except Exception as exc:
            log.debug(f"Post-session coach sync: {exc}")
