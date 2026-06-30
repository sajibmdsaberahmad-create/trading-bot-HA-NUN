#!/usr/bin/env python3
"""
core/brain_maturity.py — Infant → adult growth; teacher API fades as students grow.

Stages unlock slowly from local heuristics + PPO only, toward proxy/PPO/copilot
students that replace cloud council day by day.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig
from core.notify import log

STATE_PATH = Path("models/owned_brain_state.json")
PROXY_PATH = Path("models/teacher_proxy.joblib")

_lock = threading.Lock()
_sample_counter = 0

# Stage thresholds — all cumulative; infant starts tiny
STAGES: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("newborn", {
        "min_trades": 0,
        "min_evolutions": 0,
        "min_dataset": 0,
        "decision_api_daily": 0,
        "copilot_api_daily": 0,
        "ppo_teacher_api_daily": 0,
        "council_sample_rate": 0.0,
        "copilot_refresh_sec": 99999.0,
        "ppo_micro_steps": 128,
        "proxy_min_trades": 9999,
        "use_proxy_entries": False,
        "description": "PPO + heuristics only — collecting first experiences",
    }),
    ("infant", {
        "min_trades": 8,
        "min_evolutions": 0,
        "min_dataset": 0,
        "decision_api_daily": 4,
        "copilot_api_daily": 2,
        "ppo_teacher_api_daily": 0,
        "council_sample_rate": 0.08,
        "copilot_refresh_sec": 300.0,
        "ppo_micro_steps": 256,
        "proxy_min_trades": 25,
        "use_proxy_entries": False,
        "description": "Tiny teacher glimpses — mostly local learning",
    }),
    ("toddler", {
        "min_trades": 25,
        "min_evolutions": 1,
        "min_dataset": 50,
        "decision_api_daily": 12,
        "copilot_api_daily": 5,
        "ppo_teacher_api_daily": 1,
        "council_sample_rate": 0.18,
        "copilot_refresh_sec": 180.0,
        "ppo_micro_steps": 384,
        "proxy_min_trades": 20,
        "use_proxy_entries": False,
        "description": "Teacher labels entries; proxy training begins",
    }),
    ("child", {
        "min_trades": 60,
        "min_evolutions": 2,
        "min_dataset": 200,
        "decision_api_daily": 25,
        "copilot_api_daily": 8,
        "ppo_teacher_api_daily": 2,
        "council_sample_rate": 0.35,
        "copilot_refresh_sec": 120.0,
        "ppo_micro_steps": 512,
        "proxy_min_trades": 15,
        "use_proxy_entries": True,
        "description": "Student proxy assists entries; API still teaches gaps",
    }),
    ("teen", {
        "min_trades": 150,
        "min_evolutions": 4,
        "min_dataset": 600,
        "decision_api_daily": 15,
        "copilot_api_daily": 4,
        "ppo_teacher_api_daily": 1,
        "council_sample_rate": 0.15,
        "copilot_refresh_sec": 150.0,
        "ppo_micro_steps": 512,
        "proxy_min_trades": 12,
        "use_proxy_entries": True,
        "description": "Students lead; teacher for hard cases only",
    }),
    ("adult", {
        "min_trades": 350,
        "min_evolutions": 8,
        "min_dataset": 1200,
        "decision_api_daily": 8,
        "copilot_api_daily": 2,
        "ppo_teacher_api_daily": 0,
        "council_sample_rate": 0.06,
        "copilot_refresh_sec": 240.0,
        "ppo_micro_steps": 768,
        "proxy_min_trades": 10,
        "use_proxy_entries": True,
        "description": "Owned brain — API polish only; students run the session",
    }),
)

_PURPOSE_MAP = {
    "decision": "decision_api_daily",
    "copilot": "copilot_api_daily",
    "ppo_teacher": "ppo_teacher_api_daily",
    "notify": "decision_api_daily",
}


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def ensure_birth() -> Dict[str, Any]:
    """First run — register newborn brain."""
    state = _load_state()
    if state.get("birth_at"):
        return state
    now = datetime.now(timezone.utc).isoformat()
    state.update({
        "birth_at": now,
        "stage": "newborn",
        "evolution_count": 0,
        "api_usage_today": {"day": _today_str()},
    })
    _save_state(state)
    log.info("👶 Owned brain born — newborn stage (local-only, no teacher API yet)")
    return state


def _rolling_trade_win_rate(cfg: Optional[BotConfig] = None, *, lookback: int = 30) -> Optional[float]:
    """Recent closed-trade win rate from experience buffer."""
    try:
        from core.experience_buffer import load_recent
        recs = load_recent(max(lookback * 3, 80))
        trades = [
            r for r in recs
            if r.get("source") in ("live_trade", "replay_live", "shadow_trade")
            and (r.get("pnl_usd") is not None or r.get("win") is not None)
        ][-lookback:]
        if len(trades) < 5:
            return None
        wins = sum(
            1 for t in trades
            if t.get("win") or float(t.get("pnl_usd", 0) or 0) > 0
        )
        return wins / len(trades)
    except Exception:
        return None


def _holdout_proxy_accuracy(cfg: Optional[BotConfig] = None) -> Optional[float]:
    try:
        from core.hybrid_distiller import distillation_status
        st = distillation_status(cfg)
        acc = st.get("proxy_holdout_accuracy") or st.get("proxy_accuracy")
        return float(acc) if acc is not None else None
    except Exception:
        return None


def _metrics(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    closed = 0
    try:
        from core.hybrid_distiller import _count_closed_trades
        closed = _count_closed_trades()
    except Exception:
        pass
    dataset = 0
    ds_path = Path("models/council_training_dataset.jsonl")
    if ds_path.is_file():
        with open(ds_path) as f:
            dataset = sum(1 for _ in f)
    state = _load_state()
    proxy_acc = None
    holdout_acc = None
    try:
        from core.hybrid_distiller import distillation_status
        st = distillation_status(cfg)
        proxy_acc = st.get("proxy_accuracy")
        holdout_acc = st.get("proxy_holdout_accuracy")
    except Exception:
        pass
    rolling_wr = _rolling_trade_win_rate(cfg)
    return {
        "closed_trades": closed,
        "dataset_pairs": dataset,
        "evolution_count": int(state.get("evolution_count", 0)),
        "proxy_accuracy": float(proxy_acc) if proxy_acc is not None else None,
        "proxy_holdout_accuracy": float(holdout_acc) if holdout_acc is not None else None,
        "rolling_win_rate": float(rolling_wr) if rolling_wr is not None else None,
        "proxy_exists": PROXY_PATH.is_file(),
    }


def _stage_score(name: str, limits: Dict[str, Any], m: Dict[str, Any]) -> float:
    """How fully this stage's requirements are met (0–1)."""
    checks = [
        m["closed_trades"] >= limits["min_trades"],
        m["evolution_count"] >= limits["min_evolutions"],
        m["dataset_pairs"] >= limits["min_dataset"],
    ]
    if limits.get("use_proxy_entries") and limits["min_trades"] >= 60:
        checks.append(m["proxy_exists"])
    return sum(checks) / max(len(checks), 1)


def compute_stage(cfg: Optional[BotConfig] = None) -> str:
    """Highest stage whose thresholds are met (counts + quality gates)."""
    ensure_birth()
    m = _metrics(cfg)
    best = "newborn"
    for name, limits in STAGES:
        if (
            m["closed_trades"] >= limits["min_trades"]
            and m["evolution_count"] >= limits["min_evolutions"]
            and m["dataset_pairs"] >= limits["min_dataset"]
        ):
            if limits.get("use_proxy_entries") and limits["min_trades"] >= 60:
                if not m["proxy_exists"]:
                    continue
            best = name

    # Quality gates — prevent proxy random-split inflation from jumping stages
    holdout = m.get("proxy_holdout_accuracy") or m.get("proxy_accuracy")
    wr = m.get("rolling_win_rate")
    names = [n for n, _ in STAGES]
    idx = names.index(best)

    teen_min_wr = float(os.getenv("BRAIN_TEEN_MIN_WIN_RATE", "0.38"))
    teen_min_holdout = float(os.getenv("BRAIN_TEEN_MIN_HOLDOUT_ACC", "0.55"))
    adult_min_wr = float(os.getenv("BRAIN_ADULT_MIN_WIN_RATE", "0.40"))
    adult_min_holdout = float(os.getenv("BRAIN_ADULT_MIN_HOLDOUT_ACC", "0.62"))

    if idx >= names.index("teen"):
        if wr is not None and wr < teen_min_wr:
            best = "child"
            idx = names.index(best)
        if holdout is not None and holdout < teen_min_holdout:
            best = "child"
            idx = names.index(best)

    if idx >= names.index("adult"):
        if m["closed_trades"] < 350:
            best = "teen"
            idx = names.index(best)
        elif wr is not None and wr < adult_min_wr:
            best = "teen"
            idx = names.index(best)
        elif holdout is not None and holdout < adult_min_holdout:
            best = "teen"
            idx = names.index(best)

    return best


def _stage_limits(stage: str) -> Dict[str, Any]:
    for name, limits in STAGES:
        if name == stage:
            return dict(limits)
    return dict(STAGES[0][1])


def _api_budget_multiplier(proxy_acc: Optional[float]) -> float:
    """Students stronger → less teacher API. Prefer holdout accuracy when set."""
    if proxy_acc is None:
        return 1.0
    if proxy_acc >= 0.70:
        return 0.20
    if proxy_acc >= 0.62:
        return 0.35
    if proxy_acc >= 0.55:
        return 0.55
    if proxy_acc >= 0.48:
        return 0.75
    return 1.0


def _proxy_acc_for_budget(m: Dict[str, Any]) -> Optional[float]:
    holdout = m.get("proxy_holdout_accuracy")
    if holdout is not None:
        return float(holdout)
    acc = m.get("proxy_accuracy")
    return float(acc) if acc is not None else None


def maturity_snapshot(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    ensure_birth()
    stage = compute_stage(cfg)
    limits = _stage_limits(stage)
    m = _metrics(cfg)
    mult = _api_budget_multiplier(_proxy_acc_for_budget(m))
    usage = _load_state().get("api_usage_today") or {}
    if usage.get("day") != _today_str():
        usage = {"day": _today_str()}
    return {
        "stage": stage,
        "stage_index": next(i for i, (n, _) in enumerate(STAGES) if n == stage),
        "stage_count": len(STAGES),
        "description": limits.get("description", ""),
        "metrics": m,
        "limits": limits,
        "api_budget_multiplier": mult,
        "api_usage_today": usage,
        "decision_budget_left": max(
            0,
            _daily_budget_for("decision", limits, mult, stage=stage)
            - int(usage.get("decision", 0)),
        ),
        "copilot_budget_left": max(
            0,
            int(limits["copilot_api_daily"] * mult)
            - int(usage.get("copilot", 0)),
        ),
        "ppo_teacher_budget_left": max(
            0,
            _daily_budget_for("ppo_teacher", limits, mult, stage=stage)
            - int(usage.get("ppo_teacher", 0)),
        ),
        "next_stage": _next_stage_name(stage),
    }


def _next_stage_name(current: str) -> Optional[str]:
    names = [n for n, _ in STAGES]
    try:
        i = names.index(current)
        return names[i + 1] if i + 1 < len(names) else None
    except ValueError:
        return "infant"


def maturity_limits(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    snap = maturity_snapshot(cfg)
    lim = dict(snap["limits"])
    lim["stage"] = snap["stage"]
    return lim


def _reset_daily_usage_if_needed(state: Dict[str, Any]) -> Dict[str, Any]:
    usage = state.setdefault("api_usage_today", {})
    if usage.get("day") != _today_str():
        state["api_usage_today"] = {"day": _today_str()}
    return state


def record_api_call(purpose: str) -> None:
    with _lock:
        state = _load_state()
        _reset_daily_usage_if_needed(state)
        key = {"decision": "decision", "copilot": "copilot", "ppo_teacher": "ppo_teacher"}.get(
            purpose, "decision",
        )
        usage = state["api_usage_today"]
        usage[key] = int(usage.get(key, 0)) + 1
        state["stage"] = compute_stage()
        _save_state(state)


def record_evolution() -> None:
    with _lock:
        state = ensure_birth()
        state["evolution_count"] = int(state.get("evolution_count", 0)) + 1
        state["last_evolution"] = datetime.now(timezone.utc).isoformat()
        state["stage"] = compute_stage()
        _save_state(state)


def _training_session_decision_floor() -> int:
    """Higher teacher API budget during replay/live gold collection."""
    replay = os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
    if replay:
        try:
            return max(0, int(os.getenv("REPLAY_DECISION_API_DAILY", "48")))
        except (TypeError, ValueError):
            return 48
    try:
        from core.trading_focus_guard import is_live_scalper_active
        live_active = is_live_scalper_active()
    except Exception:
        live_active = False
    if live_active and os.getenv("HALIM_LIVE_GOLD_COLLECT", "true").lower() in (
        "1", "true", "yes",
    ):
        try:
            return max(0, int(os.getenv("LIVE_DECISION_API_DAILY", "16")))
        except (TypeError, ValueError):
            return 16
    return 0


def _decision_sample_throttle_enabled() -> bool:
    """Replay/live training skips council sample_skip unless explicitly enabled."""
    replay = os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
    if replay:
        return os.getenv("REPLAY_COUNCIL_SAMPLE", "false").lower() in ("1", "true", "yes")
    try:
        from core.trading_focus_guard import is_live_scalper_active
        if is_live_scalper_active():
            return os.getenv("LIVE_COUNCIL_SAMPLE", "false").lower() in ("1", "true", "yes")
    except Exception:
        pass
    return True


def _daily_budget_for(
    purpose: str,
    limits: Dict[str, Any],
    mult: float,
    *,
    stage: str = "",
) -> int:
    key = _PURPOSE_MAP.get(purpose, "decision_api_daily")
    base = max(0, int(limits.get(key, 0) * mult))
    if purpose == "decision":
        floor = _training_session_decision_floor()
        if floor > 0:
            return max(base, floor)
    if purpose == "ppo_teacher" and base <= 0 and stage == "adult":
        try:
            adult_floor = int(os.getenv("BRAIN_ADULT_PPO_TEACHER_DAILY", "2"))
        except (TypeError, ValueError):
            adult_floor = 2
        if adult_floor > 0:
            return adult_floor
    return base


def allow_teacher_api(purpose: str, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    """Gate cloud teacher — returns (allowed, reason)."""
    if os.getenv("BRAIN_MATURITY_FORCE_API", "").lower() in ("1", "true", "yes"):
        return True, "forced"
    cfg = cfg or BotConfig()
    snap = maturity_snapshot(cfg)
    limits = snap["limits"]
    mult = snap["api_budget_multiplier"]
    budget = _daily_budget_for(purpose, limits, mult, stage=snap["stage"])
    usage = snap["api_usage_today"]
    used_key = {"decision": "decision", "copilot": "copilot", "ppo_teacher": "ppo_teacher"}.get(
        purpose, "decision",
    )
    used = int(usage.get(used_key, 0))

    if budget <= 0:
        return False, f"{snap['stage']}_no_{purpose}_api"

    if used >= budget:
        return False, f"daily_{purpose}_cap_{budget}"

    if purpose == "decision" and _decision_sample_throttle_enabled():
        rate = float(limits.get("council_sample_rate", 1.0)) * mult
        if rate <= 0:
            return False, f"{snap['stage']}_council_local_only"
        global _sample_counter
        _sample_counter += 1
        period = max(1, int(round(1.0 / max(rate, 0.01))))
        if (_sample_counter % period) != 0:
            return False, f"sample_skip_1/{period}"

    return True, "ok"


def allow_ppo_teacher_api(cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    return allow_teacher_api("ppo_teacher", cfg)


def should_use_student_entry(cfg: Optional[BotConfig] = None) -> bool:
    snap = maturity_snapshot(cfg)
    if not snap["limits"].get("use_proxy_entries"):
        return False
    if not PROXY_PATH.is_file():
        return False
    try:
        from core.hybrid_distiller import is_fast_path_enabled
        if is_fast_path_enabled(cfg or BotConfig()):
            return True
    except Exception:
        pass
    acc = snap["metrics"].get("proxy_accuracy")
    return acc is not None and acc >= 0.50


def apply_maturity_to_config(cfg: BotConfig) -> Dict[str, Any]:
    """Apply infant-appropriate limits to cfg at session start."""
    from core.owned_brain_evolution import detect_device_profile, device_limits

    snap = maturity_snapshot(cfg)
    lim = snap["limits"]
    dev = device_limits(detect_device_profile())
    ppo_steps = min(int(lim["ppo_micro_steps"]), int(dev.get("ppo_micro_steps", 512)))

    cfg.PPO_ENTRY_MICRO_STEPS = ppo_steps
    cfg.PPO_TEACHER_MICRO_STEPS = ppo_steps
    cfg.COPILOT_REFRESH_SEC = float(lim["copilot_refresh_sec"])
    cfg.HYBRID_DISTILL_MIN_TRADES = int(lim["proxy_min_trades"])

    # Cap council RPM by stage (works with council_budget)
    stage_rpm = {
        "newborn": 0,
        "infant": 2,
        "toddler": 6,
        "child": 12,
        "teen": 8,
        "adult": 4,
    }
    cfg.COUNCIL_DECISION_MAX_PER_MIN = stage_rpm.get(snap["stage"], 6)

    state = ensure_birth()
    state["stage"] = snap["stage"]
    _save_state(state)
    return snap


def log_maturity_banner(cfg: Optional[BotConfig] = None) -> None:
    snap = maturity_snapshot(cfg)
    m = snap["metrics"]
    nxt = snap.get("next_stage") or "mastery"
    log.info("=" * 56)
    log.info(f"  🧠 OWNED BRAIN — stage: {snap['stage'].upper()} ({snap['stage_index']+1}/{snap['stage_count']})")
    log.info(f"  {snap['description']}")
    log.info(
        f"  Trades={m['closed_trades']} dataset={m['dataset_pairs']} "
        f"evolutions={m['evolution_count']} proxy={m.get('proxy_accuracy') or '—'}"
    )
    log.info(
        f"  Teacher API today: council≤{snap['decision_budget_left']} "
        f"copilot≤{snap['copilot_budget_left']} ppo_teacher≤{snap['ppo_teacher_budget_left']} "
        f"(×{snap['api_budget_multiplier']:.0%} as students grow)"
    )
    log.info(f"  Next growth: → {nxt}")
    log.info("=" * 56)


def evolution_progress(step: str) -> None:
    log.info(f"  🧬 evolution step: {step}…")
    sys.stdout.flush()
    sys.stderr.flush()
