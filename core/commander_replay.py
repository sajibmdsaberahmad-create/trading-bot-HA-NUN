#!/usr/bin/env python3
"""
core/commander_replay.py — Lane B counterfactual replay (read-only).

Replays commander IB report trips + live fill_ledger round-trips:
  actual | mistake-free | bot-filtered NAV estimates + recommendations.
Does not place orders or mutate live params directly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

RECOMMENDATIONS_PATH = Path("models/commander_replay_recommendations.json")
REPLAY_HISTORY_PATH = Path("models/commander_replay_history.jsonl")
FILL_LEDGER_PATH = Path("models/fill_ledger.jsonl")

MAX_TRIP_LOSS_PCT = 0.03
LOTTERY_MIN_CONVICTION = 80.0


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def replay_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return _env_bool("COMMANDER_REPLAY_ENABLED", "true")


def _starting_nav() -> float:
    from core.commander_ib_gold import REPORT_META
    ret = float(REPORT_META.get("cumulative_return_pct", 35.0)) / 100.0
    end = float(REPORT_META.get("ending_nav_usd", 2084.55))
    if ret <= -0.99:
        return end
    return end / (1.0 + ret)


def _case_conviction(case: Dict[str, Any]) -> float:
    for key in ("conviction_est", "min_confidence_pct"):
        if key in case:
            return float(case[key])
    ticker = str(case.get("ticker", "")).upper()
    from core.commander_ib_gold import LOTTERY_FAIL_CASES, LOTTERY_WIN_CASES
    for row in LOTTERY_FAIL_CASES + LOTTERY_WIN_CASES:
        if str(row.get("ticker", "")).upper() == ticker:
            return float(row.get("conviction_est", 75))
    if case.get("outcome") == "loss" and case.get("return_pct", 0) < -15:
        return 45.0
    if case.get("outcome") == "loss":
        return 68.0
    return 88.0


def _is_hope_hold_case(case: Dict[str, Any]) -> bool:
    err = str(case.get("human_error") or "").lower()
    if "hope" in err or "held" in err:
        return True
    ticker = str(case.get("ticker", "")).upper()
    from core.commander_ib_gold import LOTTERY_FAIL_CASES
    for row in LOTTERY_FAIL_CASES:
        if str(row.get("ticker", "")).upper() == ticker:
            return row.get("failure_mode") == "hope_hold"
    return False


def _is_weak_setup_case(case: Dict[str, Any]) -> bool:
    if _case_conviction(case) < LOTTERY_MIN_CONVICTION:
        return True
    ticker = str(case.get("ticker", "")).upper()
    from core.commander_ib_gold import LOTTERY_FAIL_CASES
    for row in LOTTERY_FAIL_CASES:
        if str(row.get("ticker", "")).upper() == ticker:
            return row.get("failure_mode") == "weak_setup"
    return False


def _cap_trip_loss(pnl_usd: float, nav: float) -> float:
    floor = -abs(nav) * MAX_TRIP_LOSS_PCT
    return max(float(pnl_usd), floor)


def replay_commander_trade_cases(nav: Optional[float] = None) -> Dict[str, Any]:
    """Counterfactual on commander report trade cases."""
    from core.commander_ib_gold import REPORT_META, TRADE_CASES

    nav0 = float(nav or _starting_nav())
    actual = mistake_free = bot_filtered = 0.0
    trips_actual = trips_mf = trips_bf = 0
    details: List[Dict[str, Any]] = []

    for case in TRADE_CASES:
        pnl = float(case.get("realized_usd", 0) or 0)
        actual += pnl
        trips_actual += 1

        if _is_weak_setup_case(case):
            mf_pnl = 0.0
            bf_pnl = 0.0
            trips_mf += 0
            trips_bf += 0
        else:
            trips_mf += 1
            trips_bf += 1
            if case.get("outcome") == "loss" and _is_hope_hold_case(case):
                mf_pnl = _cap_trip_loss(pnl, nav0)
                bf_pnl = mf_pnl
            else:
                mf_pnl = pnl
                bf_pnl = pnl
        mistake_free += mf_pnl
        bot_filtered += bf_pnl
        details.append({
            "ticker": case.get("ticker"),
            "sector": case.get("sector"),
            "actual_usd": round(pnl, 2),
            "mistake_free_usd": round(mf_pnl, 2),
            "bot_filtered_usd": round(bf_pnl, 2),
            "outcome": case.get("outcome"),
            "weak_setup": _is_weak_setup_case(case),
            "hope_hold": _is_hope_hold_case(case),
        })

    return {
        "source": "commander_ib_report",
        "report": REPORT_META.get("report"),
        "period": f"{REPORT_META.get('period_start')} → {REPORT_META.get('period_end')}",
        "starting_nav_usd": round(nav0, 2),
        "report_return_pct": REPORT_META.get("cumulative_return_pct"),
        "actual_pnl_usd": round(actual, 2),
        "mistake_free_pnl_usd": round(mistake_free, 2),
        "bot_filtered_pnl_usd": round(bot_filtered, 2),
        "uplift_mistake_free_usd": round(mistake_free - actual, 2),
        "uplift_bot_filtered_usd": round(bot_filtered - actual, 2),
        "trips": {"actual": trips_actual, "mistake_free": trips_mf, "bot_filtered": trips_bf},
        "cases": details,
    }


def _parse_ts_day(ts: str) -> str:
    try:
        return str(ts)[:10]
    except Exception:
        return ""


def load_round_trips(
    *,
    day: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    if not FILL_LEDGER_PATH.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(FILL_LEDGER_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event") != "round_trip":
                    continue
                if day and _parse_ts_day(str(row.get("timestamp", ""))) != day:
                    continue
                rows.append(row)
    except Exception:
        return []
    return rows[-limit:]


def classify_live_trip(
    trip: Dict[str, Any],
    equity: float = 1000.0,
) -> str:
    """Tag live bot trip vs commander doctrine."""
    pnl_pct = float(trip.get("pnl_pct", 0) or 0)
    pnl_usd = float(trip.get("pnl_usd", 0) or 0)
    hold_sec = float(trip.get("hold_sec", 0) or 0)
    reason = str(trip.get("exit_reason", "")).lower()

    if abs(pnl_pct) > 500 or abs(pnl_usd) > max(equity * 50, 50000):
        return "data_corrupt"

    eq = max(float(equity), 100.0)
    if pnl_usd <= -eq * MAX_TRIP_LOSS_PCT or pnl_pct <= -8:
        return "tail_loss" if hold_sec < 180 else "hope_hold"
    if pnl_pct < 0 and hold_sec > 300:
        return "hope_hold"
    if "hope" in reason or hold_sec > 600 and pnl_pct < 2:
        return "hope_hold"
    if abs(pnl_usd) < 2.0 and hold_sec < 90:
        return "fee_bleed"
    if pnl_pct > 0 and hold_sec < 400:
        return "good_cut"
    if pnl_pct < 0:
        return "loss_other"
    return "neutral"


def analyze_live_trips(
    trips: List[Dict[str, Any]],
    equity: float = 1000.0,
) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    pnl_by_class: Dict[str, float] = {}
    total_pnl = 0.0
    clean: List[Dict[str, Any]] = []

    for trip in trips:
        tag = classify_live_trip(trip, equity)
        pnl = float(trip.get("pnl_usd", 0) or 0)
        counts[tag] = counts.get(tag, 0) + 1
        pnl_by_class[tag] = pnl_by_class.get(tag, 0.0) + pnl
        if tag != "data_corrupt":
            total_pnl += pnl
        clean.append({
            "ticker": trip.get("ticker"),
            "pnl_usd": round(pnl, 2),
            "pnl_pct": round(float(trip.get("pnl_pct", 0) or 0), 2),
            "hold_sec": round(float(trip.get("hold_sec", 0) or 0), 1),
            "class": tag,
            "exit_reason": str(trip.get("exit_reason", ""))[:80],
        })

    return {
        "trip_count": len(trips),
        "valid_trip_count": sum(v for k, v in counts.items() if k != "data_corrupt"),
        "total_pnl_usd": round(total_pnl, 2),
        "class_counts": counts,
        "pnl_by_class": {k: round(v, 2) for k, v in pnl_by_class.items()},
        "trips": clean[-40:],
    }


def build_recommendations(
    commander: Dict[str, Any],
    live: Dict[str, Any],
) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    uplift = float(commander.get("uplift_mistake_free_usd", 0) or 0)
    if uplift > 50:
        recs.append({
            "id": "mechanical_exits",
            "priority": "high",
            "signal": "commander_replay",
            "message": (
                f"Mistake-free commander trips add ~${uplift:.0f} vs actual — "
                "enforce hard stops + spike-top exits (no hope-hold)."
            ),
            "suggested_params": ["STAGNATION_EXIT_SEC", "MIN_PROFIT_PROBABILITY"],
        })

    live_counts = live.get("class_counts") or {}
    if int(live_counts.get("hope_hold", 0)) >= 2:
        recs.append({
            "id": "live_hope_hold",
            "priority": "high",
            "signal": "hope_hold",
            "message": "Live sessions show hope-hold losses — tighten stagnation / early loss exits.",
            "suggested_params": ["STAGNATION_EXIT_SEC", "SCALP_PROFIT_GIVEBACK_PCT"],
        })
    if int(live_counts.get("tail_loss", 0)) >= 1:
        recs.append({
            "id": "live_tail_loss",
            "priority": "high",
            "signal": "tail_loss",
            "message": "Tail losses detected — keep war trip caps; raise entry quality floor.",
            "suggested_params": ["MIN_PROFIT_PROBABILITY", "CONFIDENCE_THRESHOLD"],
        })
    if int(live_counts.get("fee_bleed", 0)) >= 3:
        recs.append({
            "id": "live_fee_bleed",
            "priority": "medium",
            "signal": "fee_bleed",
            "message": "Churn with flat PnL — raise conviction before entry.",
            "suggested_params": ["CONFIDENCE_THRESHOLD", "MIN_PROFIT_PROBABILITY"],
        })
    if int(live_counts.get("good_cut", 0)) >= 2:
        recs.append({
            "id": "reinforce_wins",
            "priority": "low",
            "signal": "good_cut",
            "message": "Fast winners match commander style — keep sniper flash path.",
            "suggested_params": [],
        })
    return recs


def run_full_replay(
    cfg: Optional[BotConfig] = None,
    *,
    day: Optional[str] = None,
    equity: float = 1000.0,
    persist: bool = True,
) -> Dict[str, Any]:
    """Commander counterfactual + live session analysis."""
    cfg = cfg or BotConfig()
    commander = replay_commander_trade_cases()
    trips = load_round_trips(day=day)
    live = analyze_live_trips(trips, equity=equity)
    recommendations = build_recommendations(commander, live)

    result = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "day": day,
        "commander": commander,
        "live": live,
        "recommendations": recommendations,
    }

    if persist:
        _persist(result)
    return result


def _persist(result: Dict[str, Any]) -> None:
    try:
        RECOMMENDATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RECOMMENDATIONS_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
        with open(REPLAY_HISTORY_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp": result.get("generated_at"),
                "day": result.get("day"),
                "commander_uplift_usd": (result.get("commander") or {}).get("uplift_mistake_free_usd"),
                "live_trips": (result.get("live") or {}).get("trip_count"),
                "recommendation_count": len(result.get("recommendations") or []),
            }, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"Replay persist: {exc}")


def shadow_would_skip_entry(
    cfg: BotConfig,
    *,
    ticker: str,
    scan_score: float = 0.0,
    spike_ratio: float = 1.0,
    profit_probability: float = 0.5,
    fakeout_risk: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
) -> Tuple[bool, str]:
    """
    Shadow gate — log-only coach view of calculated lottery checklist.
    Does not block live entries.
    """
    if not replay_enabled(cfg):
        return False, ""
    if spike_ratio < 2.0 and scan_score < 70:
        return True, "below_lottery_vol_or_score"
    if profit_probability < 0.80:
        return True, "profit_prob_below_80pct"
    if fakeout_risk > 0.25:
        return True, "fakeout_risk_high"
    if int(ppo_action) != 1 and ppo_conf < 0.55:
        return True, "ppo_not_aligned"
    return False, ""
