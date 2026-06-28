#!/usr/bin/env python3
"""
core/ppo_teacher_training.py — Cloud teacher improves PPO (teacher–student distillation).

Cloud teacher (Groq/Gemini council) improves student PPO — teacher–student distillation.
Reviews recent closed trades, labels what PPO *should* have done, adjusts strategy
params within bounds, and drives weighted PPO micro-training on corrected rewards.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.experience_buffer import append as buffer_append, load_recent
from core.notify import log

MODELS_DIR = Path("models")
STATE_PATH = MODELS_DIR / "ppo_teacher_state.json"
SESSION_LOG = MODELS_DIR / "ppo_teacher_sessions.jsonl"

TRADE_SOURCES = frozenset({
    "replay_live", "live_trade", "shadow_trade", "replay_sim",
})


def _teacher_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "PPO_TEACHER_ENABLED", True))


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _append_session(row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    try:
        with open(SESSION_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def trade_stats(
    sources: Tuple[str, ...] = tuple(TRADE_SOURCES),
    n: int = 500,
) -> Dict[str, Any]:
    """Win rate on actual round-trips — not all buffer noise."""
    recs = load_recent(n)
    trades = [
        r for r in recs
        if r.get("source") in sources
        and (r.get("pnl_usd") is not None or r.get("win") is not None)
        and r.get("action", "SELL") in ("SELL", "TRADE", "BUY")
    ]
    if not trades:
        return {"count": 0, "win_rate": 0.0, "avg_pnl": 0.0, "trades": []}
    wins = sum(
        1 for t in trades
        if t.get("win") or float(t.get("pnl_usd", 0) or 0) > 0
    )
    pnls = [float(t.get("pnl_usd", 0) or 0) for t in trades]
    return {
        "count": len(trades),
        "win_rate": wins / len(trades),
        "avg_pnl": sum(pnls) / len(pnls),
        "wins": wins,
        "losses": len(trades) - wins,
        "trades": trades[-30:],
    }


def _summarize_for_teacher(stats: Dict[str, Any], cfg: BotConfig) -> str:
    trades = stats.get("trades") or []
    by_ticker: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        by_ticker[str(t.get("ticker", "?")).upper()].append(t)

    lines = [
        f"Session trade win_rate={stats.get('win_rate', 0):.1%} "
        f"({stats.get('wins', 0)}W/{stats.get('losses', 0)}L) "
        f"avg_pnl=${stats.get('avg_pnl', 0):+.2f}",
        f"CONFIDENCE_THRESHOLD={getattr(cfg, 'CONFIDENCE_THRESHOLD', 0.65)} "
        f"MIN_PROFIT_PROB={getattr(cfg, 'MIN_PROFIT_PROBABILITY', 0.62)}",
        "",
        "Recent closed trades (newest last):",
    ]
    for i, t in enumerate(trades[-20:], 1):
        pnl = float(t.get("pnl_usd", 0) or 0)
        lines.append(
            f"  {i}. {t.get('ticker')} entry=${t.get('entry_price', t.get('entry', 0))} "
            f"exit=${t.get('exit_price', t.get('exit', 0))} "
            f"P&L=${pnl:+.2f} reason={str(t.get('exit_reason', t.get('reason', '')))[:40]} "
            f"slip_in={t.get('entry_slippage_pct', 0)} regime={t.get('regime', '')}"
        )

    reasons = Counter(
        str(t.get("exit_reason", t.get("reason", "unknown")))[:30] for t in trades
    )
    lines.append("")
    lines.append("Exit reason counts: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common(6)))

    repeat_losses = [
        f"{tk}: {len([x for x in xs if float(x.get('pnl_usd', 0) or 0) < 0])}L"
        for tk, xs in sorted(by_ticker.items())
        if sum(1 for x in xs if float(x.get("pnl_usd", 0) or 0) < 0) >= 2
    ]
    if repeat_losses:
        lines.append("Repeat losers: " + ", ".join(repeat_losses[:8]))
    return "\n".join(lines)


def _heuristic_teacher_plan(stats: Dict[str, Any], cfg: BotConfig) -> Dict[str, Any]:
    """Local fallback when cloud API is rate-limited — pattern-based teacher."""
    trades = stats.get("trades") or []
    by_ticker: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        by_ticker[str(t.get("ticker", "")).upper()].append(t)

    labels: List[Dict[str, Any]] = []
    for ticker, xs in by_ticker.items():
        losses = [x for x in xs if float(x.get("pnl_usd", 0) or 0) < 0]
        if len(losses) >= 2:
            labels.append({
                "ticker": ticker,
                "should_have_entered": False,
                "teacher_action": 0,
                "teacher_reward": -0.85,
                "lesson": f"repeat loser {ticker} — skip until pattern changes",
            })
        elif losses:
            labels.append({
                "ticker": ticker,
                "should_have_entered": False,
                "teacher_action": 0,
                "teacher_reward": -0.5,
                "lesson": f"loss on {ticker} — require stronger council alignment",
            })

    conf = float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.65))
    min_prob = float(getattr(cfg, "MIN_PROFIT_PROBABILITY", 0.45))
    mutations = []
    if stats.get("win_rate", 1) < 0.25:
        mutations.append({
            "param": "CONFIDENCE_THRESHOLD",
            "value": min(0.75, conf + 0.04),
            "reason": f"trade WR {stats.get('win_rate', 0):.0%} — tighten PPO entry bar",
        })
        mutations.append({
            "param": "MIN_PROFIT_PROBABILITY",
            "value": min(0.62, min_prob + 0.05),
            "reason": "repeat losses — raise profit probability gate",
        })

    stop_hits = sum(
        1 for t in trades
        if "stop" in str(t.get("exit_reason", t.get("reason", ""))).lower()
    )
    diagnosis = (
        f"Local teacher: {stats.get('win_rate', 0):.0%} WR on {stats.get('count', 0)} trades. "
        f"{stop_hits} stop exits. Repeat tickers: "
        + ", ".join(
            tk for tk, xs in by_ticker.items()
            if sum(1 for x in xs if float(x.get("pnl_usd", 0) or 0) < 0) >= 2
        )[:80]
    )
    return {
        "diagnosis": diagnosis,
        "strategy_shift": "Skip repeat losers; require higher PPO confidence before micro-fast entry",
        "trade_labels": labels,
        "mutations": mutations[:3],
        "lessons": [
            "Do not re-enter same ticker after same-bar stop loss",
            "Penalize PPO micro-fast when council confidence is neutral (50%)",
            "Wait for volume spike confirmation before chase entries",
        ],
        "ppo_focus": "Penalize entries with instant stop hits (hold_sec=0)",
        "_source": "heuristic_fallback",
    }


def _call_teacher(cfg: BotConfig, summary: str, stats: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from core.commander_learning import _parse_plan_json
    from core.council_client import CouncilClient
    from core.council_budget import PURPOSE_PPO_TEACHER
    from core.param_bounds import format_bounds_for_prompt, tunable_param_names

    try:
        from core.brain_maturity import allow_ppo_teacher_api
        ok, reason = allow_ppo_teacher_api(cfg)
        if not ok:
            log.info(f"🎓 PPO teacher: local student only ({reason})")
            return _heuristic_teacher_plan(stats, cfg)
    except Exception:
        pass

    client = CouncilClient(cfg)
    if not client.enabled():
        log.warning("PPO teacher: council API unavailable — using heuristic fallback")
        return _heuristic_teacher_plan(stats, cfg)

    bounds = format_bounds_for_prompt(cfg, 25)
    allowed = ", ".join(tunable_param_names(cfg)[:18])
    prompt = (
        "You are the TEACHER model improving student PPO and Halim reflex agents.\n"
        "The student PPO picks entries/exits on 1-min scalps. Win rate is falling — diagnose and fix.\n\n"
        f"PERFORMANCE SUMMARY:\n{summary}\n\n"
        "Analyze WHY losses cluster (bad entries, slippage, stops too tight, repeat tickers).\n"
        "For each recent trade, say what PPO SHOULD have done instead.\n\n"
        f"Tunable params (mutations max 3): {allowed}\n"
        f"{bounds}\n\n"
        "Respond ONLY with JSON:\n"
        "{\n"
        '  "diagnosis": "2-3 sentences root cause",\n'
        '  "strategy_shift": "one sentence policy change for PPO",\n'
        '  "trade_labels": [\n'
        '    {"ticker": "QS", "should_have_entered": false, "teacher_action": 0, '
        '"teacher_reward": -0.8, "lesson": "skip low micro on repeat loser"}\n'
        "  ],\n"
        '  "mutations": [{"param": "CONFIDENCE_THRESHOLD", "value": 0.68, "reason": "..."}],\n'
        '  "lessons": ["bullet 1", "bullet 2"],\n'
        '  "ppo_focus": "what feature patterns to penalize/reward"\n'
        "}\n"
        "teacher_action: 0=HOLD/skip, 1=BUY, 2=EXIT. teacher_reward: -1.0 to +1.0.\n"
        "Penalize repeat losing entries; reward skipped bad setups."
    )
    raw = None
    max_attempts = int(getattr(cfg, "PPO_TEACHER_API_RETRIES", 2))
    for attempt in range(max_attempts):
        raw = client._complete(
            prompt, priority=True, fast=True,
            purpose=PURPOSE_PPO_TEACHER,
        )
        if raw:
            break
        wait = 50.0 * (attempt + 1)
        log.warning(
            f"PPO teacher API attempt {attempt + 1}/{max_attempts} failed — "
            f"retry in {wait:.0f}s (Groq/Gemini rate limit?)"
        )
        time.sleep(wait)

    if not raw:
        log.warning("PPO teacher: cloud API unavailable after retries — heuristic fallback")
        return _heuristic_teacher_plan(stats, cfg)

    plan = _parse_plan_json(raw)
    if not plan:
        log.warning("PPO teacher: could not parse council JSON — heuristic fallback")
        return _heuristic_teacher_plan(stats, cfg)
    plan["_raw_excerpt"] = raw[:600]
    return plan


def _match_trade_label(
    trade: Dict[str, Any], labels: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    ticker = str(trade.get("ticker", "")).upper()
    for lb in labels:
        if str(lb.get("ticker", "")).upper() == ticker:
            return lb
    return None


def _build_teacher_records(
    cfg: BotConfig,
    plan: Dict[str, Any],
    trades: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    labels = plan.get("trade_labels") or []
    out: List[Dict[str, Any]] = []
    for trade in trades[-15:]:
        lb = _match_trade_label(trade, labels)
        pnl = float(trade.get("pnl_usd", 0) or 0)
        if lb:
            reward = float(lb.get("teacher_reward", 0))
            action = int(lb.get("teacher_action", 0 if pnl < 0 else 1))
            should_enter = bool(lb.get("should_have_entered", pnl > 0))
        else:
            reward = 0.4 if pnl > 0 else -0.5
            action = 1 if pnl > 0 else 0
            should_enter = pnl > 0

        feat = trade.get("features")
        obs = trade.get("obs")
        if not feat and not obs:
            continue
        out.append({
            "source": "teacher_ppo",
            "ticker": trade.get("ticker"),
            "action": action,
            "should_have_entered": should_enter,
            "teacher_reward": reward,
            "reward": reward,
            "features": feat,
            "obs": obs,
            "entry_price": trade.get("entry_price", trade.get("entry")),
            "exit_price": trade.get("exit_price", trade.get("exit")),
            "pnl_usd": pnl,
            "lesson": (lb or {}).get("lesson", plan.get("strategy_shift", "")),
            "exit_reason": trade.get("exit_reason", trade.get("reason")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return out


def _train_ppo_from_teacher(
    cfg: BotConfig,
    records: List[Dict[str, Any]],
    model: Any = None,
) -> bool:
    if not records:
        return False
    try:
        from core.ppo_entry_learning import evaluate_and_improve_ppo, get_ppo_model
        m = model or get_ppo_model()
        if m is None:
            return False
        for rec in records:
            buffer_append(rec)
        improved = evaluate_and_improve_ppo(cfg, model=m, records=records)
        steps = int(getattr(cfg, "PPO_TEACHER_MICRO_STEPS", 1024))
        from core.ppo_entry_learning import ppo_micro_improve
        improved = ppo_micro_improve(cfg, m, records) or improved
        if improved:
            m.save(cfg.MODEL_PATH)
            log.info(
                f"  🎓 PPO teacher train: {len(records)} labeled trades | "
                f"{steps} micro-steps → {cfg.MODEL_PATH}"
            )
        return improved
    except Exception as exc:
        log.warning(f"PPO teacher train failed: {exc}")
        return False


def run_ppo_teacher_session(
    cfg: BotConfig,
    *,
    model: Any = None,
    trigger: str = "manual",
    force: bool = False,
    autopilot: Any = None,
    consciousness: Any = None,
) -> Dict[str, Any]:
    """Full teacher cycle: analyze → mutate params → label trades → PPO train."""
    if not _teacher_enabled(cfg) and not force:
        return {"skipped": True, "reason": "disabled"}

    stats = trade_stats(n=int(getattr(cfg, "PPO_TEACHER_LOOKBACK", 400)))
    if stats["count"] < int(getattr(cfg, "PPO_TEACHER_MIN_TRADES", 3)) and not force:
        return {"skipped": True, "reason": "insufficient_trades", "count": stats["count"]}

    wr = stats["win_rate"]
    floor = float(getattr(cfg, "PPO_TEACHER_WIN_RATE_FLOOR", 0.38))
    if not force and wr >= floor:
        return {"skipped": True, "reason": "win_rate_ok", "win_rate": wr}

    summary = _summarize_for_teacher(stats, cfg)
    log.info(
        f"🎓 PPO TEACHER session ({trigger}) — trade WR={wr:.1%} "
        f"({stats['count']} trades) — calling cloud API…"
    )

    plan = _call_teacher(cfg, summary, stats)
    if not plan:
        return {"ok": False, "reason": "teacher_plan_empty", "win_rate": wr}

    source = plan.get("_source", "cloud_api")
    diagnosis = str(plan.get("diagnosis", ""))[:300]
    log.info(f"  🎓 Teacher ({source}): {diagnosis}")

    applied: Dict[str, Any] = {"mutations": [], "lessons": plan.get("lessons", [])}
    if plan.get("mutations"):
        from core.commander_learning import apply_commander_plan
        applied = apply_commander_plan(
            cfg, plan, autopilot=autopilot, consciousness=consciousness,
            source="ppo_teacher",
        )
        n_ok = len(applied.get("applied") or [])
        if n_ok:
            log.info(f"  🎓 Teacher applied {n_ok} param mutation(s)")

    teacher_recs = _build_teacher_records(cfg, plan, stats.get("trades") or [])
    ppo_ok = _train_ppo_from_teacher(cfg, teacher_recs, model=model)

    result = {
        "ok": True,
        "trigger": trigger,
        "win_rate": wr,
        "trade_count": stats["count"],
        "diagnosis": diagnosis,
        "strategy_shift": plan.get("strategy_shift", ""),
        "ppo_focus": plan.get("ppo_focus", ""),
        "teacher_labels": len(teacher_recs),
        "ppo_trained": ppo_ok,
        "teacher_source": source,
        "mutations_applied": len(applied.get("applied") or []),
        "lessons": plan.get("lessons", [])[:5],
    }
    state = _load_state()
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["last_win_rate"] = wr
    state["sessions"] = int(state.get("sessions", 0)) + 1
    _save_state(state)
    _append_session(result)

    try:
        from core.halim_capabilities import record_teacher_action
        out_text = (
            f"Diagnosis: {diagnosis}\n"
            f"Strategy: {plan.get('strategy_shift', '')}\n"
            f"PPO focus: {plan.get('ppo_focus', '')}\n"
            f"Lessons: {', '.join(plan.get('lessons', [])[:5])}"
        )
        record_teacher_action(
            "decision_text",
            summary[:2000],
            out_text[:2000],
            source=f"ppo_teacher:{source}",
            cfg=cfg,
        )
    except Exception:
        pass

    if plan.get("lessons"):
        try:
            from core.self_improver import GUIDELINES_PATH
            block = (
                f"\n\n# PPO Teacher {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
                f"Diagnosis: {diagnosis}\n"
                + "\n".join(f"- {x}" for x in plan.get("lessons", [])[:5])
            )
            with open(GUIDELINES_PATH, "a", encoding="utf-8") as fh:
                fh.write(block)
        except Exception:
            pass

    try:
        from core.brain_notify import notify_brain_development
        notify_brain_development(
            cfg,
            "brain_ppo_teacher",
            {
                **result,
                "summary": f"PPO teacher {trigger}: WR {wr:.0%}, source {source}",
            },
            journal=True,
        )
    except Exception:
        pass

    return result


def maybe_run_ppo_teacher_training(
    cfg: BotConfig,
    *,
    model: Any = None,
    trigger: str = "trade_closed",
    autopilot: Any = None,
    consciousness: Any = None,
) -> Optional[Dict[str, Any]]:
    """Rate-limited hook after closed trades when win rate is poor."""
    if not _teacher_enabled(cfg):
        return None

    state = _load_state()
    min_interval = float(getattr(cfg, "PPO_TEACHER_MIN_INTERVAL_SEC", 180.0))
    last = state.get("last_run", "")
    if last:
        try:
            last_ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
            if time.time() - last_ts < min_interval:
                return None
        except Exception:
            pass

    every_n = int(getattr(cfg, "PPO_TEACHER_EVERY_N_TRADES", 4))
    state["trades_since_teacher"] = int(state.get("trades_since_teacher", 0)) + 1
    _save_state(state)
    if state["trades_since_teacher"] % every_n != 0:
        return None

    stats = trade_stats(n=200)
    floor = float(getattr(cfg, "PPO_TEACHER_WIN_RATE_FLOOR", 0.38))
    if stats["count"] >= 3 and stats["win_rate"] >= floor:
        return None

    result = run_ppo_teacher_session(
        cfg, model=model, trigger=trigger,
        autopilot=autopilot, consciousness=consciousness,
    )
    if result.get("ok"):
        state = _load_state()
        state["trades_since_teacher"] = 0
        _save_state(state)
    return result
