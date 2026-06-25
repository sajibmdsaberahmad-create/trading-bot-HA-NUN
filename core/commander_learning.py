#!/usr/bin/env python3
"""
core/commander_learning.py — Turn commander chat + session data into applied self-improvements.

Reads Telegram guidance, daily activity, and performance telemetry; asks Ollama for a
structured improvement plan; applies guardrailed parameter mutations and stores lessons.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.daily_activity_report import collect_day_report, format_structured_report
from core.experience_buffer import stats as buffer_stats
from core.notify import log
from core.self_improver import (
    ADJUSTMENTS_PATH,
    GUIDELINES_PATH,
    HISTORY_PATH,
    _apply_adjustments,
)
from core.param_bounds import (
    bounds_snapshot,
    clamp_param_value,
    format_bounds_for_prompt,
    is_locked,
    is_tunable,
    normalize_param,
    tunable_param_names,
)
from core.paper_mode import is_paper_free_learning
GUIDANCE_PATH = Path("models/commander_guidance.jsonl")


def load_commander_guidance(limit: int = 20) -> List[str]:
    """Recent commander note text for AI prompts."""
    if not GUIDANCE_PATH.exists():
        return []
    lines: List[str] = []
    try:
        for line in GUIDANCE_PATH.read_text().splitlines()[-limit:]:
            try:
                rec = json.loads(line)
                t = rec.get("text", "").strip()
                if t:
                    lines.append(t)
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return lines

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

LEARNING_LOG = Path("models/commander_learning.jsonl")
_last_apply_ts = 0.0


def _param_snapshot(cfg: BotConfig) -> Dict[str, Any]:
    return bounds_snapshot(cfg)


def load_guidance_records(limit: int = 30) -> List[Dict[str, Any]]:
    if not GUIDANCE_PATH.exists():
        return []
    rows = []
    try:
        for line in GUIDANCE_PATH.read_text().splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return rows


def build_learning_context(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    *,
    trigger: str = "",
) -> Dict[str, Any]:
    connector = getattr(runner, "conn", None) if runner else None
    day_report = collect_day_report(cfg, runner, connector)
    guidance = load_guidance_records(25)
    guidance_text = load_commander_guidance(15)

    ctx: Dict[str, Any] = {
        "trigger": trigger[:500],
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "params": _param_snapshot(cfg),
        "buffer_stats": buffer_stats(),
        "day_summary": day_report.get("summary", {}),
        "day_activity_excerpt": format_structured_report(day_report, max_lines=35)[:2500],
        "commander_guidance": guidance_text[-10:],
        "recent_trades": day_report.get("trades", [])[-12:],
    }

    if runner:
        ctx["live"] = {
            "nav": round(getattr(runner, "bot_nav", 0), 2),
            "trades_today": getattr(runner, "trades_today", 0),
            "win_rate": round(getattr(getattr(runner, "risk", None), "win_rate", 0) * 100, 1),
        }
        if getattr(runner, "consciousness", None):
            ctx["identity"] = runner.consciousness.get_identity()
        if getattr(runner, "autopilot", None) and getattr(runner.autopilot, "core", None):
            core = runner.autopilot.core
            ctx["cognitive"] = {
                "mood": core.state.mood,
                "mood_message": getattr(core.state, "mood_message", ""),
                "lessons": list(core.state.learned_lessons)[-5:],
            }
    return ctx


def _parse_plan_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return {}


def generate_runtime_event_plan(
    cfg: BotConfig,
    context: Dict[str, Any],
    think_fn: Callable[[str], str],
) -> Dict[str, Any]:
    """5W real-time diagnosis → mutations + lessons (same JSON shape as commander plan)."""
    from core.paper_mode import is_paper_free_learning

    bounds_text = format_bounds_for_prompt(cfg, 30)
    allowed = ", ".join(tunable_param_names(cfg)[:20])
    event = context.get("runtime_event", "event")
    detail = context.get("runtime_detail", {})
    paper = (
        "PAPER FREE LEARNING — apply fixes aggressively within bounds.\n"
        if is_paper_free_learning(cfg) else ""
    )
    prompt = (
        "You are HANOON pilot AI — full-time profit hunter watching the trading algo.\n"
        "PRIMARY MISSION: making profit is your only main goal — every fix must increase "
        "opportunistic extraction within guardrails.\n"
        f"{paper}"
        "A runtime event just occurred. Use rigorous 5W reasoning:\n"
        "  WHY did this happen? (root cause)\n"
        "  WHEN does it tend to occur? (session, regime)\n"
        "  HOW should the algo change? (params, logic hints)\n"
        "  WHAT prevents recurrence? (concrete mutations)\n\n"
        "If event is missed_profit_hunt: lower SPIKE_TOP_MIN_GAIN_PCT or "
        "SPIKE_TOP_MIN_VOL_RATIO within bounds; prioritize opportunistic exits.\n"
        "If event is council_timeout or bad entry: tune MIN_PROFIT_PROBABILITY, "
        "ENTRY_QUALITY_BLEND_WEIGHT, or COUNCIL_TIMEOUT_MIN_SCAN_SCORE — not hardcoded skips.\n"
        "If event is market_data_failure: add ticker to mental skip list — "
        "no profit hunting without live IB bars (162/420 errors).\n\n"
        f"EVENT: {event}\n"
        f"DETAIL:\n{json.dumps(detail, default=str)[:1500]}\n\n"
        f"TRIGGER:\n{context.get('trigger', '')[:800]}\n\n"
        f"SESSION:\n{json.dumps({k: context[k] for k in ('day_summary', 'live', 'buffer_stats', 'market_state') if k in context}, default=str)[:1200]}\n\n"
        f"BOUNDS:\n{bounds_text}\n"
        f"TUNABLE: {allowed}\n\n"
        "Respond ONLY JSON:\n"
        '{"summary":"...","understanding":"...","lessons":["..."],'
        '"mutations":[{"param":"CONFIDENCE_THRESHOLD","value":0.58,"reason":"..."}],'
        '"guidelines":["..."]}'
    )
    raw = think_fn(prompt)
    parsed = _parse_plan_json(raw)
    if parsed:
        return parsed
    return {
        "summary": f"Observed {event} — recorded for learning.",
        "understanding": str(detail.get("reason", ""))[:200],
        "lessons": [f"Review {event} on {detail.get('ticker', '?')}"],
        "mutations": [],
        "guidelines": [],
    }


def generate_commander_plan(
    cfg: BotConfig,
    context: Dict[str, Any],
    think_fn: Callable[[str], str],
) -> Dict[str, Any]:
    """Ollama produces mutations + lessons from commander input and session data."""
    allowed = ", ".join(tunable_param_names(cfg)[:25]) + " …"
    bounds_text = format_bounds_for_prompt(cfg, 35)
    paper_note = (
        "PAPER ACCOUNT FREE LEARNING — size and risk from ~$1M equity. "
        "Learn aggressively from mistakes; bounds are wide.\n\n"
        if is_paper_free_learning(cfg) else ""
    )
    prompt = (
        "You are HANOON — autonomous trading pilot AI working full-time to make profit.\n"
        f"{paper_note}"
        "Study commander guidance, today's activity, and current parameters.\n"
        "PRIMARY MISSION: profit is your ONLY main goal — tune params to extract more money "
        "every session; never passive hold; use any lawful tactic within bounds.\n"
        "You control ALL entry-quality policy within bounds: MIN_PROFIT_PROBABILITY, "
        "MAX_FAKEOUT_RISK_ENTER, EQ_WEIGHT_*, ENTRY_QUALITY_HARDNESS (0=advisory only, "
        "≥0.5 enables code veto), LIKELY_FAKEOUT_BLOCK_LEVEL, COUNCIL_TIMEOUT_MIN_SCAN_SCORE.\n"
        "Default: quality signals advise council — you learn thresholds from outcomes, "
        "not hardcoded skip rules.\n"
        "All profit hunt events are in models/profit_hunt_ledger.jsonl — learn from every line.\n"
        "Propose SMALL, evidence-based tuning — max 3 mutations. You MAY adjust risk & judgment "
        "params (stops, risk $, daily loss %, confidence, sizing) WITHIN the bounds below.\n\n"
        f"COMMANDER GUIDANCE:\n{json.dumps(context.get('commander_guidance', []), default=str)}\n\n"
        f"TRIGGER (latest message):\n{context.get('trigger', '')}\n\n"
        f"SESSION:\n{json.dumps({k: context[k] for k in ('day_summary', 'live', 'buffer_stats') if k in context}, default=str)}\n\n"
        f"ACTIVITY:\n{context.get('day_activity_excerpt', '')[:1800]}\n\n"
        f"CURRENT PARAMS (value + min/max):\n{json.dumps(context.get('params', {}), default=str)[:2000]}\n\n"
        f"LEARNING BOUNDS (stay inside these):\n{bounds_text}\n\n"
        f"TUNABLE PARAMS (sample): {allowed}\n"
        "LOCKED FOREVER: secrets, PAPER_TRADING, IB credentials.\n\n"
        "Respond ONLY valid JSON:\n"
        "{\n"
        '  "summary": "2-3 sentences what you learned",\n'
        '  "understanding": "how commander feedback changes your approach",\n'
        '  "lessons": ["short lesson 1", "lesson 2"],\n'
        '  "mutations": [{"param": "SCALP_MIN_RR", "value": 1.8, "reason": "..."}],\n'
        '  "guidelines": ["bullet for ai_guidelines.txt"]\n'
        "}"
    )
    raw = think_fn(prompt)
    parsed = _parse_plan_json(raw)
    if not parsed:
        return {
            "summary": "Could not parse AI plan — retained guidance for prompts only.",
            "understanding": context.get("trigger", "")[:200],
            "lessons": load_commander_guidance(3),
            "mutations": [],
            "guidelines": [],
            "raw": (raw or "")[:500],
        }
    parsed.setdefault("mutations", [])
    parsed.setdefault("lessons", [])
    parsed.setdefault("guidelines", [])
    return parsed


def _apply_mutation(
    cfg: BotConfig,
    param: str,
    value: Any,
    reason: str,
    autopilot=None,
) -> tuple[bool, str]:
    param = normalize_param(param)
    if is_locked(param):
        return False, f"locked: {param}"
    if not is_tunable(param, cfg):
        return False, f"not in learning bounds: {param}"
    if not hasattr(cfg, param):
        return False, f"unknown param: {param}"

    current = getattr(cfg, param)
    clamped, ok, clamp_msg = clamp_param_value(param, value, current=current, cfg=cfg)
    if not ok:
        return False, clamp_msg

    if autopilot:
        approved, msg = autopilot.propose_improvement(param, clamped, reason)
        if not approved:
            return False, msg

    old = current
    try:
        if isinstance(old, bool):
            new_val = bool(clamped)
        elif isinstance(old, int) and not isinstance(old, bool):
            new_val = int(round(float(clamped)))
        else:
            new_val = float(clamped)
    except (TypeError, ValueError):
        return False, f"invalid value for {param}: {value}"

    setattr(cfg, param, new_val)
    note = f" ({clamp_msg})" if clamp_msg != "ok" else ""
    log.info(f"🧬 Commander learning applied {param}: {old} → {new_val}{note} — {reason}")
    return True, "applied"


def apply_commander_plan(
    cfg: BotConfig,
    plan: Dict[str, Any],
    *,
    autopilot=None,
    consciousness=None,
    source: str = "commander",
) -> Dict[str, Any]:
    """Apply mutations and persist lessons/guidelines."""
    applied: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    for mut in (plan.get("mutations") or [])[:3]:
        param = mut.get("param", "")
        value = mut.get("value")
        reason = mut.get("reason", "commander plan")
        if value is None:
            continue
        from core.param_bounds import is_runtime_blocked, normalize_param
        if str(source).startswith("runtime_") and is_runtime_blocked(normalize_param(param)):
            rejected.append({
                "param": normalize_param(param), "value": value,
                "reason": reason, "ok": False, "msg": "runtime-blocked param",
            })
            continue
        ok, msg = _apply_mutation(cfg, param, value, reason, autopilot)
        rec = {"param": normalize_param(param), "value": value, "reason": reason, "ok": ok, "msg": msg}
        if ok:
            applied.append(rec)
        else:
            rejected.append(rec)

    # Also apply dict-style adjustments for self_improver compatibility
    adj = {}
    for a in applied:
        adj[a["param"]] = {
            "old": "—",
            "new": a["value"],
            "reason": a["reason"],
            "confidence": 0.7,
        }
    if adj:
        _apply_adjustments(cfg, adj)

    lessons = [str(x).strip() for x in (plan.get("lessons") or []) if str(x).strip()]
    if autopilot and getattr(autopilot, "core", None):
        core = autopilot.core
        for lesson in lessons:
            if lesson not in core.state.learned_lessons:
                core.state.learned_lessons.append(lesson)
                if len(core.state.learned_lessons) > 100:
                    core.state.learned_lessons = core.state.learned_lessons[-100:]
        understanding = (plan.get("understanding") or plan.get("summary") or "").strip()
        if understanding:
            line = f"Commander: {understanding[:240]}"
            if line not in core.state.learned_lessons:
                core.state.learned_lessons.append(line)
        try:
            core._persist_state(push_git=False)
        except Exception:
            pass

    if consciousness and hasattr(consciousness, "apply_improvement"):
        try:
            consciousness.apply_improvement({
                "source": source,
                "summary": plan.get("summary", ""),
                "applied": applied,
                "lessons": lessons,
            })
        except Exception:
            pass

    guidelines_lines = [
        f"🧭 COMMANDER LEARNING | {datetime.now(timezone.utc).isoformat()}",
        plan.get("summary", ""),
        plan.get("understanding", ""),
    ]
    for g in plan.get("guidelines") or []:
        guidelines_lines.append(f"• {g}")
    if applied:
        guidelines_lines.append("• Applied mutations:")
        for a in applied:
            guidelines_lines.append(f"  - {a['param']} → {a['value']} ({a['reason']})")
    guidelines_text = "\n".join(line for line in guidelines_lines if line)

    try:
        GUIDELINES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(GUIDELINES_PATH, "a") as f:
            f.write(guidelines_text + "\n\n")
        with open(ADJUSTMENTS_PATH, "w") as f:
            json.dump({a["param"]: a for a in applied}, f, indent=2)
    except Exception as exc:
        log.debug(f"Guidelines write: {exc}")

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "summary": plan.get("summary", ""),
        "understanding": plan.get("understanding", ""),
        "applied": applied,
        "rejected": rejected,
        "lessons": lessons,
    }
    _append_learning_log(record)
    _append_improvement_history(record)

    return record


def _append_learning_log(record: Dict[str, Any]) -> None:
    try:
        LEARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LEARNING_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        log.debug(f"Learning log: {exc}")


def _append_improvement_history(record: Dict[str, Any]) -> None:
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text())
        except Exception:
            pass
    history.append({
        "timestamp": record.get("timestamp"),
        "source": record.get("source", "commander"),
        "adjustments": {a["param"]: a for a in record.get("applied", [])},
        "guidelines_summary": (record.get("summary") or "")[:300],
        "lessons": record.get("lessons", []),
    })
    try:
        with open(HISTORY_PATH, "w") as f:
            json.dump(history[-100:], f, indent=2)
    except Exception:
        pass


def run_commander_learning_cycle(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    *,
    think_fn: Optional[Callable[[str], str]] = None,
    trigger: str = "",
    autopilot=None,
    consciousness=None,
    apply: bool = True,
) -> Dict[str, Any]:
    """Full cycle: context → AI plan → apply → return result."""
    if not getattr(cfg, "COMMANDER_LEARNING_ENABLED", True):
        return {"status": "disabled"}

    if think_fn is None and runner and getattr(runner, "ai_commander", None):
        think_fn = runner.ai_commander.compose_telegram
    if not think_fn:
        return {"status": "no_ai"}

    autopilot = autopilot or (getattr(runner, "autopilot", None) if runner else None)
    consciousness = consciousness or (getattr(runner, "consciousness", None) if runner else None)

    context = build_learning_context(cfg, runner, trigger=trigger)
    plan = generate_commander_plan(cfg, context, think_fn)

    result: Dict[str, Any] = {
        "status": "planned",
        "plan": plan,
        "context_trigger": trigger[:200],
    }

    if apply and (plan.get("mutations") or plan.get("lessons")):
        applied = apply_commander_plan(
            cfg, plan,
            autopilot=autopilot,
            consciousness=consciousness,
            source="commander_chat" if trigger else "commander_cycle",
        )
        result["applied"] = applied
        result["status"] = "applied" if applied.get("applied") else "lessons_only"

    return result


def maybe_auto_apply_from_chat(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"],
    trigger: str,
    think_fn: Callable[[str], str],
) -> None:
    """Throttled background apply after commander messages."""
    global _last_apply_ts
    if not getattr(cfg, "COMMANDER_AUTO_APPLY_FROM_CHAT", True):
        return
    if not (trigger or "").strip():
        return

    min_gap = float(getattr(cfg, "COMMANDER_AUTO_APPLY_MIN_SEC", 90.0))
    now = time.time()
    if now - _last_apply_ts < min_gap:
        return
    _last_apply_ts = now

    try:
        run_commander_learning_cycle(
            cfg, runner,
            think_fn=think_fn,
            trigger=trigger,
            apply=True,
        )
    except Exception as exc:
        log.debug(f"Commander auto-apply: {exc}")


def format_apply_report(result: Dict[str, Any]) -> str:
    """Telegram-friendly summary of what changed."""
    plan = result.get("plan") or {}
    applied = (result.get("applied") or {}).get("applied") or []
    rejected = (result.get("applied") or {}).get("rejected") or []

    lines = ["🧬 SELF-IMPROVEMENT PLAN"]
    if plan.get("summary"):
        lines.append(plan["summary"][:400])
    if plan.get("understanding"):
        lines.append(f"\nUnderstanding: {plan['understanding'][:300]}")

    if applied:
        lines.append("\n✅ Applied:")
        for a in applied:
            lines.append(f"• {a['param']} → {a['value']}")
    elif plan.get("mutations"):
        lines.append("\n(no mutations passed guardrails)")

    if rejected:
        lines.append("\n⛔ Rejected:")
        for r in rejected[:3]:
            lines.append(f"• {r.get('param')}: {r.get('msg', '')[:60]}")

    lessons = plan.get("lessons") or []
    if lessons:
        lines.append("\nLessons stored:")
        for lesson in lessons[:4]:
            lines.append(f"• {lesson[:100]}")

    return "\n".join(lines)[:3800]
