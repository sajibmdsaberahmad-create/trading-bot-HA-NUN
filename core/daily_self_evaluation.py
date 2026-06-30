#!/usr/bin/env python3
"""
core/daily_self_evaluation.py — AI end-of-day self-evaluation (premarket → close).

Generates a reflective statement: what was learned, before vs after improvements,
what changed, and what the pilot is looking toward. Saved to disk and sent via
Telegram using Ollama generative thinking.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.daily_activity_report import collect_day_report, format_structured_report
from core.experience_buffer import load_all, stats as buffer_stats
from core.market_hours import MARKET_TZ, now_et
from core.notify import log

if TYPE_CHECKING:
    from core.ai_commander import AICommander
    from core.scalper_runner import ScalperRunner

REPORTS_DIR = Path("models/daily_reports")
DONE_MARKER_PREFIX = ".self_eval_done_"
IMPROVEMENT_HISTORY = Path("models/improvement_history.json")
PARAMETER_ADJUSTMENTS = Path("models/parameter_adjustments.json")
COMMANDER_LEARNING = Path("models/commander_learning.jsonl")
WEIGHTS_PATH = Path("models/scalper_weights.json")
GUIDELINES_PATH = Path("models/ai_guidelines.txt")

_lock = threading.Lock()
_last_run_day: Optional[str] = None

SESSION_BOUNDARIES = {
    "premarket": (4 * 60, 9 * 60 + 30),
    "regular_hours": (9 * 60 + 30, 16 * 60),
    "after_hours": (16 * 60, 20 * 60),
}


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _et_minutes(ts: Optional[datetime]) -> Optional[int]:
    if ts is None:
        return None
    try:
        et = ts.astimezone(MARKET_TZ)
        return et.hour * 60 + et.minute
    except Exception:
        return None


def _session_for_ts(ts: Optional[datetime]) -> str:
    mins = _et_minutes(ts)
    if mins is None:
        return "unknown"
    for name, (start, end) in SESSION_BOUNDARIES.items():
        if start <= mins < end:
            return name
    return "overnight"


def _is_same_et_day(ts: Optional[datetime], day_str: str) -> bool:
    if ts is None:
        return False
    try:
        return ts.astimezone(MARKET_TZ).strftime("%Y-%m-%d") == day_str
    except Exception:
        return False


def _segment_timeline(timeline: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "premarket": [],
        "regular_hours": [],
        "after_hours": [],
        "overnight": [],
        "unknown": [],
    }
    for ev in timeline:
        ts = _parse_ts(ev.get("timestamp"))
        bucket = _session_for_ts(ts)
        buckets.setdefault(bucket, []).append(ev)
    return buckets


def _session_summary(events: List[Dict[str, Any]], trades: List[Dict[str, Any]], session: str) -> Dict[str, Any]:
    session_trades = []
    for t in trades:
        ts = _parse_ts(t.get("exit_time") or t.get("timestamp") or t.get("time"))
        trade_session = _session_for_ts(ts)
        if trade_session == "unknown" and ts is None and session == "regular_hours":
            trade_session = "regular_hours"
        if trade_session == session:
            session_trades.append(t)
    pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in session_trades)
    wins = sum(1 for t in session_trades if t.get("result") == "win" or t.get("won") is True)
    losses = sum(1 for t in session_trades if t.get("result") == "loss" or t.get("won") is False)
    tickers = sorted({str(t.get("ticker", "?")) for t in session_trades})
    return {
        "events": len(events),
        "trades": len(session_trades),
        "wins": wins,
        "losses": losses,
        "pnl_usd": round(pnl, 2),
        "tickers": tickers[:12],
        "highlights": [
            {
                "ticker": ev.get("ticker"),
                "kind": ev.get("kind"),
                "pnl_usd": ev.get("pnl_usd"),
                "reason": (ev.get("reason") or "")[:80],
                "time": (ev.get("timestamp") or "")[:19],
            }
            for ev in events
            if ev.get("pnl_usd") is not None or ev.get("kind") in ("entry", "exit")
        ][:8],
    }


def _today_buffer_stats(day_str: str) -> Dict[str, Any]:
    sources: Dict[str, int] = {}
    wins = 0
    total = 0
    for rec in load_all():
        ts = _parse_ts(rec.get("timestamp"))
        if not _is_same_et_day(ts, day_str):
            continue
        total += 1
        src = str(rec.get("source", "unknown"))
        sources[src] = sources.get(src, 0) + 1
        if rec.get("win"):
            wins += 1
    return {
        "records_today": total,
        "sources": sources,
        "win_rate": round(wins / max(total, 1), 3),
    }


def _today_improvements(day_str: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if IMPROVEMENT_HISTORY.exists():
        try:
            history = json.loads(IMPROVEMENT_HISTORY.read_text())
            for rec in history if isinstance(history, list) else []:
                ts = _parse_ts(rec.get("timestamp"))
                if _is_same_et_day(ts, day_str):
                    out.append({
                        "source": rec.get("source"),
                        "summary": (rec.get("guidelines_summary") or "")[:200],
                        "adjustments": list((rec.get("adjustments") or {}).keys())[:8],
                        "lessons": (rec.get("lessons") or [])[:5],
                    })
        except Exception:
            pass
    if COMMANDER_LEARNING.exists():
        try:
            for line in COMMANDER_LEARNING.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                ts = _parse_ts(rec.get("timestamp"))
                if not _is_same_et_day(ts, day_str):
                    continue
                applied = rec.get("applied") or []
                out.append({
                    "source": "commander_learning",
                    "trigger": (rec.get("context_trigger") or "")[:120],
                    "adjustments": [a.get("param") for a in applied if isinstance(a, dict)][:8],
                    "summary": (rec.get("summary") or "")[:200],
                })
        except Exception:
            pass
    return out[-15:]


def _parameter_deltas() -> List[Dict[str, Any]]:
    if not PARAMETER_ADJUSTMENTS.exists():
        return []
    try:
        data = json.loads(PARAMETER_ADJUSTMENTS.read_text())
        deltas = []
        for param, info in (data if isinstance(data, dict) else {}).items():
            if not isinstance(info, dict):
                continue
            old = info.get("old")
            new = info.get("new")
            if old is not None and new is not None and old != new:
                deltas.append({
                    "param": param,
                    "before": old,
                    "after": new,
                    "reason": (info.get("reason") or "")[:120],
                })
        return deltas[:20]
    except Exception:
        return []


def _morning_snapshot(day_str: str) -> Optional[Dict[str, Any]]:
    path = Path("models/account_snapshots.jsonl")
    if not path.exists():
        return None
    morning_events = ("market_open", "session_startup", "pre_market")
    try:
        for line in reversed(path.read_text().splitlines()[-300:]):
            rec = json.loads(line)
            ts = _parse_ts(rec.get("time_utc") or rec.get("timestamp"))
            if not _is_same_et_day(ts, day_str):
                continue
            if rec.get("event") in morning_events:
                return rec
    except Exception:
        pass
    return None


def _load_weights() -> Dict[str, Any]:
    if not WEIGHTS_PATH.exists():
        return {}
    try:
        return json.loads(WEIGHTS_PATH.read_text())
    except Exception:
        return {}


def collect_self_eval_context(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    connector=None,
    day_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Build full context for end-of-day self-evaluation."""
    day_str = day_str or now_et().strftime("%Y-%m-%d")
    report = collect_day_report(cfg, runner, connector, day_str)
    segmented = _segment_timeline(report.get("timeline", []))
    trades = report.get("trades", [])

    ib_learning: Dict[str, Any] = {}
    try:
        from core.daily_ib_learning import collect_ib_learning_pack
        if getattr(cfg, "DAILY_IB_LEARNING_ENABLED", True):
            ib_learning = collect_ib_learning_pack(
                cfg, runner, connector, day_str, trigger="self_eval",
            )
    except Exception:
        pass

    sessions = {
        name: _session_summary(segmented.get(name, []), trades, name)
        for name in ("premarket", "regular_hours", "after_hours")
    }

    morning = _morning_snapshot(day_str)
    account = dict(report.get("account", {}))
    if runner is not None:
        try:
            from core.account_view import day_pnl as account_day_pnl, display_equity
            runner._refresh_account_balance()
            baseline = float(getattr(cfg, "INITIAL_CASH", 1000))
            day_pnl_usd, _ = account_day_pnl(runner, cfg)
            account.update({
                "ib_account": round(getattr(runner, "account_equity", 0), 2),
                "bot_nav": round(display_equity(runner, cfg), 2),
                "bot_cash": round(getattr(runner, "bot_cash", 0), 2),
                "day_pnl_usd": round(day_pnl_usd, 2),
                "trades_today": getattr(runner, "trades_today", 0),
            })
        except Exception:
            pass

    before_after: Dict[str, Any] = {"has_morning": morning is not None}
    if morning:
        before_after.update({
            "morning_nav": morning.get("bot_nav"),
            "morning_ib": morning.get("ib_account"),
            "morning_trades": morning.get("trades_today"),
            "morning_position": morning.get("position"),
            "close_nav": account.get("bot_nav"),
            "close_ib": account.get("ib_account"),
            "nav_delta": round(
                float(account.get("bot_nav", 0)) - float(morning.get("bot_nav", 0)), 2
            ),
            "ib_delta": round(
                float(account.get("ib_account", 0)) - float(morning.get("ib_account", 0)), 2
            ),
        })

    pilot: Dict[str, Any] = {}
    consciousness: Dict[str, Any] = {}
    if runner is not None:
        if getattr(runner, "pilot", None):
            try:
                pilot = runner.pilot.get_veteran_status()
            except Exception:
                pass
        if getattr(runner, "consciousness", None):
            try:
                consciousness = runner.consciousness.get_identity()
            except Exception:
                pass

    guidelines_excerpt = ""
    if GUIDELINES_PATH.exists():
        try:
            guidelines_excerpt = GUIDELINES_PATH.read_text()[:1500]
        except Exception:
            pass

    return {
        "day": day_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_tz": str(MARKET_TZ),
        "summary": report.get("summary", {}),
        "account": account,
        "sessions": sessions,
        "before_after": before_after,
        "learning": {
            "buffer_today": _today_buffer_stats(day_str),
            "buffer_all_time": buffer_stats(),
            "improvements_today": _today_improvements(day_str),
            "parameter_deltas": _parameter_deltas(),
            "guidelines_excerpt": guidelines_excerpt,
            "pilot": pilot,
            "consciousness": {
                "mood": consciousness.get("mood"),
                "mood_message": (consciousness.get("mood_message") or "")[:200],
                "improvements_applied": consciousness.get("improvements_applied"),
                "trades_observed": consciousness.get("trades_observed"),
            },
        },
        "weights": _load_weights(),
        "structured_activity": format_structured_report(report, max_lines=40),
        "recent_trades": trades[-15:],
        "ib_day_learning": {
            "counts": ib_learning.get("ib", {}).get("counts", {}),
            "comparison": ib_learning.get("comparison", {}),
            "orders": len(ib_learning.get("ib", {}).get("orders", [])),
            "executions": len(ib_learning.get("ib", {}).get("executions", [])),
        } if ib_learning else {},
    }


def _fallback_statement(ctx: Dict[str, Any]) -> str:
    s = ctx.get("summary", {})
    acct = ctx.get("account", {})
    ba = ctx.get("before_after", {})
    pre = ctx.get("sessions", {}).get("premarket", {})
    rth = ctx.get("sessions", {}).get("regular_hours", {})
    lines = [
        f"🧠 HANOON END-OF-DAY SELF-EVALUATION — {ctx.get('day', '?')}",
        "",
        "SESSION PERFORMANCE",
        f"  Pre-market:  {pre.get('trades', 0)} trades · P&L ${pre.get('pnl_usd', 0):+.2f} · "
        f"events {pre.get('events', 0)}",
        f"  Regular hrs: {rth.get('trades', 0)} trades · P&L ${rth.get('pnl_usd', 0):+.2f} · "
        f"{rth.get('wins', 0)}W/{rth.get('losses', 0)}L",
        f"  Day total:   {s.get('trades', 0)} trades · P&L ${s.get('day_pnl_usd', 0):+.2f} · "
        f"Win {s.get('win_rate_pct', 0):.0f}%",
        "",
        f"ACCOUNT  IB ${acct.get('ib_account', 0):,.2f} · NAV ${acct.get('bot_nav', 0):,.2f}",
    ]
    if ba.get("has_morning"):
        lines.append(
            f"  Morning → close: NAV Δ ${ba.get('nav_delta', 0):+,.2f} · "
            f"IB Δ ${ba.get('ib_delta', 0):+,.2f}"
        )
    learn = ctx.get("learning", {})
    buf = learn.get("buffer_today", {})
    lines.extend([
        "",
        "WHAT I LEARNED TODAY",
        f"  Experience records: {buf.get('records_today', 0)} "
        f"(win rate {float(buf.get('win_rate', 0)):.0%})",
    ])
    for imp in learn.get("improvements_today", [])[:5]:
        adj = ", ".join(imp.get("adjustments") or []) or "lessons"
        lines.append(f"  • {imp.get('source', 'learning')}: {adj}")
    deltas = learn.get("parameter_deltas", [])
    if deltas:
        lines.append("")
        lines.append("BEFORE → AFTER (parameters)")
        for d in deltas[:6]:
            lines.append(
                f"  {d.get('param')}: {d.get('before')} → {d.get('after')} "
                f"— {(d.get('reason') or '')[:60]}"
            )
    pilot = learn.get("pilot", {})
    if pilot:
        lines.append(
            f"\nPILOT  {pilot.get('level', 'Cadet')} · "
            f"XP {pilot.get('total_xp', 0)} · flights {pilot.get('flights_completed', 0)}"
        )
    mood = learn.get("consciousness", {})
    if mood.get("mood"):
        lines.append(f"MOOD   {mood.get('mood')} — {(mood.get('mood_message') or '')[:100]}")
    lines.extend([
        "",
        "LOOKING FORWARD",
        "  Tighten entries when win rate is soft; honor council timeouts with mechanical exits.",
        "  Watch pre-market spikes for early lock targets; protect NAV into tomorrow's open.",
    ])
    return "\n".join(lines)


def compose_self_evaluation(
    ctx: Dict[str, Any],
    think_fn: Optional[Callable[[str], str]] = None,
    cfg: Optional[BotConfig] = None,
) -> str:
    """Generative Ollama self-evaluation document."""
    fallback = _fallback_statement(ctx)
    if not think_fn:
        return fallback

    max_chars = int(getattr(cfg, "AI_DAILY_SELF_EVAL_MAX_CHARS", 3800) if cfg else 3800)
    prompt = (
        "You are HANOON — autonomous trading pilot AI working full-time to make profit. "
        "Write your END-OF-DAY SELF-EVALUATION for your commander. "
        "Profit is your only main goal — judge the day by money extracted and hunts missed. "
        "This is a reflective statement document, not a trade alert.\n\n"
        f"TRADING DAY: {ctx.get('day')} (US Eastern, includes PRE-MARKET through RTH close)\n\n"
        "SESSION BREAKDOWN (pre-market, regular hours, after hours):\n"
        f"{json.dumps(ctx.get('sessions', {}), default=str)[:1200]}\n\n"
        "DAY SUMMARY:\n"
        f"{json.dumps(ctx.get('summary', {}), default=str)}\n\n"
        "ACCOUNT NOW:\n"
        f"{json.dumps(ctx.get('account', {}), default=str)}\n\n"
        "BEFORE (morning/open) vs AFTER (close) — use exact deltas:\n"
        f"{json.dumps(ctx.get('before_after', {}), default=str)[:800]}\n\n"
        "LEARNING & IMPROVEMENTS TODAY:\n"
        f"{json.dumps(ctx.get('learning', {}), default=str)[:2000]}\n\n"
        "RECENT TRADES:\n"
        f"{json.dumps(ctx.get('recent_trades', [])[:12], default=str)[:1500]}\n\n"
        "Write a structured self-evaluation with these sections (use the headers exactly):\n"
        "1. HEADLINE — one line on how the full day went (premarket + RTH)\n"
        "2. PRE-MARKET → CLOSE NARRATIVE — what happened in each session phase\n"
        "3. WHAT I LEARNED — concrete lessons from trades, errors, council timeouts, fills\n"
        "4. BEFORE vs AFTER — parameter/behavior changes with exact old→new values where given\n"
        "5. WHAT IMPROVED — honest wins in process or results\n"
        "6. WHAT I'M LOOKING TOWARD — focus for tomorrow's premarket and open\n\n"
        "Rules: first-person pilot voice; exact $ and % numbers from the data; "
        "mention pre-market activity explicitly; no JSON; plain text; "
        f"max {max_chars} characters."
    )
    try:
        raw = (think_fn(prompt) or "").strip()
        if raw and len(raw) >= 120:
            return raw[:max_chars]
    except Exception as exc:
        log.debug(f"Self-eval Ollama compose: {exc}")
    return fallback


def _brief_notification(statement: str, ctx: Dict[str, Any]) -> str:
    """Short Telegram headline from the full statement."""
    lines = [ln.strip() for ln in statement.splitlines() if ln.strip()]
    headline = lines[0] if lines else f"Self-eval {ctx.get('day')}"
    s = ctx.get("summary", {})
    acct = ctx.get("account", {})
    pre = ctx.get("sessions", {}).get("premarket", {})
    return (
        f"🧠 EOD SELF-EVAL — {ctx.get('day')}\n"
        f"{headline[:200]}\n"
        f"Pre-mkt {pre.get('trades', 0)} trades ${pre.get('pnl_usd', 0):+.0f} · "
        f"Day {s.get('trades', 0)} trades ${s.get('day_pnl_usd', 0):+.0f} · "
        f"NAV ${acct.get('bot_nav', 0):,.0f}\n"
        f"Full report saved → models/daily_reports/self_eval_{ctx.get('day', '').replace('-', '')}.txt"
    )


def write_self_evaluation_files(
    ctx: Dict[str, Any],
    statement: str,
) -> Dict[str, str]:
    """Persist JSON + readable statement document."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    day = ctx.get("day", now_et().strftime("%Y-%m-%d"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"self_eval_{stamp}.json"
    txt_path = REPORTS_DIR / f"self_eval_{day.replace('-', '')}.txt"
    report_path = REPORTS_DIR / f"report_{day}.txt"

    payload = {
        "timestamp": ctx.get("generated_at"),
        "day": day,
        "mode": "HANOON",
        "type": "daily_self_evaluation",
        "context": ctx,
        "statement": statement,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    doc = (
        f"HANOON DAILY SELF-EVALUATION\n"
        f"{'=' * 60}\n"
        f"Date: {day} (US Eastern — premarket through RTH close)\n"
        f"Generated: {ctx.get('generated_at', '')}\n"
        f"{'=' * 60}\n\n"
        f"{statement}\n\n"
        f"{'=' * 60}\n"
        f"STRUCTURED ACTIVITY (reference)\n"
        f"{'=' * 60}\n"
        f"{ctx.get('structured_activity', '')}\n"
    )
    txt_path.write_text(doc)

    try:
        with open(report_path, "a") as f:
            f.write(f"\n\n{'=' * 60}\n")
            f.write(f"AI SELF-EVALUATION @ {ctx.get('generated_at', '')[:19]}\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(statement)
            f.write("\n")
    except Exception:
        pass

    marker = REPORTS_DIR / f"{DONE_MARKER_PREFIX}{day}"
    marker.write_text(ctx.get("generated_at", ""))

    return {
        "json": str(json_path),
        "statement": str(txt_path),
        "report_append": str(report_path),
    }


def run_daily_self_evaluation(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    *,
    notifier=None,
    ai_commander: Optional["AICommander"] = None,
    autopilot=None,
    consciousness=None,
    pilot=None,
    connector=None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Full end-of-day self-evaluation: collect → generate → save → notify.
    Idempotent per ET calendar day unless force=True.
    """
    global _last_run_day
    if not getattr(cfg, "AI_DAILY_SELF_EVALUATION", True):
        return {"status": "disabled"}

    day_str = now_et().strftime("%Y-%m-%d")
    marker = REPORTS_DIR / f"{DONE_MARKER_PREFIX}{day_str}"

    with _lock:
        if not force and (marker.exists() or _last_run_day == day_str):
            return {"status": "already_done", "day": day_str}
        _last_run_day = day_str

    log.info(f"🧠 Generating end-of-day self-evaluation for {day_str}…")

    ctx = collect_self_eval_context(cfg, runner, connector, day_str)

    think_fn: Optional[Callable[[str], str]] = None
    if getattr(cfg, "COUNCIL_DAILY_DIGEST_ENABLED", True):
        def _think(p: str) -> str:
            try:
                from core.council_client import get_council_client
                text = get_council_client(cfg).daily_digest_call(p, day_str=day_str)
                if text:
                    return text.strip()
            except Exception as exc:
                log.debug(f"Daily digest API: {exc}")
            return ""
        think_fn = _think

    statement = compose_self_evaluation(ctx, think_fn, cfg)
    paths = write_self_evaluation_files(ctx, statement)
    brief = _brief_notification(statement, ctx)

    log.info(f"🧠 Self-evaluation saved → {paths.get('statement')}")

    if notifier:
        day_pnl = float(ctx.get("summary", {}).get("day_pnl_usd", 0) or 0)
        trades = int(ctx.get("summary", {}).get("trades", 0) or 0)
        headline = statement.splitlines()[0][:200] if statement else brief
        single_msg = (
            f"🧠 DAILY REPORT — {day_str}\n"
            f"{headline}\n"
            f"Day: {trades} trades · ${day_pnl:+,.2f}\n"
            f"{'─' * 28}\n"
            f"{statement[:3200]}"
        )
        try:
            notifier.info(single_msg)
        except Exception as exc:
            log.debug(f"Self-eval notify: {exc}")

    return {
        "status": "ok",
        "day": day_str,
        "statement": statement,
        "paths": paths,
    }


def schedule_daily_self_evaluation(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    **kwargs,
) -> None:
    """Fire self-evaluation on a background thread (non-blocking)."""
    if not getattr(cfg, "AI_DAILY_SELF_EVALUATION", True):
        return

    def _worker():
        try:
            run_daily_self_evaluation(cfg, runner, **kwargs)
        except Exception as exc:
            log.warning(f"Daily self-evaluation failed: {exc}")

    if runner is not None and getattr(runner, "_worker", None):
        try:
            runner._worker._executor.submit(_worker)
            return
        except Exception:
            pass
    threading.Thread(target=_worker, name="daily-self-eval", daemon=True).start()
