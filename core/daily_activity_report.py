#!/usr/bin/env python3
"""
core/daily_activity_report.py — Full-day trading activity aggregation.

Pulls bot journals, post-mortem telemetry, AI decisions, and IB executions
into one chronological ledger for Telegram briefings and commander chat.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.market_hours import MARKET_TZ, now_et
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner
    from core.connector import IBConnector

TRADE_JOURNAL = Path("models/trade_journal.json")
POST_MORTEM = Path("models/post_mortem_audit.jsonl")
AI_DECISION_LOG = Path("models/ai_decision_log.jsonl")
ACCOUNT_SNAPSHOTS = Path("models/account_snapshots.jsonl")
AUDIT_TRAIL = Path("audit_trail.jsonl")
PERF_CSV = Path("performance.csv")


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


def _is_same_et_day(ts: Optional[datetime], day_str: str) -> bool:
    if ts is None:
        return False
    try:
        et = ts.astimezone(MARKET_TZ)
        return et.strftime("%Y-%m-%d") == day_str
    except Exception:
        return False


def _read_jsonl(path: Path, day_str: str) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(rec.get("timestamp") or rec.get("ts") or rec.get("time_utc"))
            if _is_same_et_day(ts, day_str):
                rec["_sort_ts"] = ts.timestamp() if ts else 0
                rows.append(rec)
    except Exception as exc:
        log.debug(f"read_jsonl {path}: {exc}")
    return rows


def _runner_trades(runner: Optional["ScalperRunner"], day_str: str) -> List[Dict[str, Any]]:
    trades: List[Dict] = []
    if runner is not None:
        trades.extend(list(getattr(runner, "trade_journal", []) or []))
    if TRADE_JOURNAL.exists():
        try:
            persisted = json.loads(TRADE_JOURNAL.read_text())
            if isinstance(persisted, list):
                seen = {
                    f"{t.get('ticker')}_{t.get('entry')}_{t.get('exit')}"
                    for t in trades
                }
                for t in persisted:
                    key = f"{t.get('ticker')}_{t.get('entry')}_{t.get('exit')}"
                    if key not in seen:
                        trades.append(t)
                        seen.add(key)
        except Exception:
            pass

    out = []
    for t in trades:
        ts = _parse_ts(t.get("exit_time") or t.get("timestamp") or t.get("time"))
        if ts is None or _is_same_et_day(ts, day_str):
            rec = dict(t)
            rec["_sort_ts"] = ts.timestamp() if ts else 0
            rec["_kind"] = "trade"
            out.append(rec)
    return out


def fetch_ib_day_executions(
    connector: Optional["IBConnector"],
    day_str: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Pull IB fills/executions for the ET trading day."""
    if connector is None or not connector.is_connected():
        return []

    day_str = day_str or now_et().strftime("%Y-%m-%d")
    out: List[Dict[str, Any]] = []

    try:
        ib = connector.ib
        ib.reqExecutions()
        ib.sleep(0.4)
        for fill in ib.fills():
            ex = fill.execution
            ts = _parse_ts(getattr(ex, "time", None))
            if ts is None:
                continue
            if not _is_same_et_day(ts, day_str):
                continue
            contract = fill.contract
            out.append({
                "symbol": getattr(contract, "symbol", "?"),
                "side": getattr(ex, "side", "?"),
                "shares": float(getattr(ex, "shares", 0) or 0),
                "price": round(float(getattr(ex, "price", 0) or 0), 4),
                "order_id": getattr(ex, "orderId", 0),
                "exec_id": getattr(ex, "execId", ""),
                "commission": round(float(getattr(fill, "commissionReport", None) and fill.commissionReport.commission or 0), 4),
                "timestamp": ts.isoformat(),
                "_sort_ts": ts.timestamp(),
                "_kind": "ib_execution",
            })
    except Exception as exc:
        log.debug(f"IB executions fetch: {exc}")

    out.sort(key=lambda r: r.get("_sort_ts", 0))
    return out


def collect_day_report(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    connector: Optional["IBConnector"] = None,
    day_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate all activity for one ET calendar day."""
    day_str = day_str or now_et().strftime("%Y-%m-%d")

    trades = _runner_trades(runner, day_str)
    post_mortem = _read_jsonl(POST_MORTEM, day_str)
    ai_decisions = _read_jsonl(AI_DECISION_LOG, day_str)
    snapshots = _read_jsonl(ACCOUNT_SNAPSHOTS, day_str)
    audit = _read_jsonl(AUDIT_TRAIL, day_str)
    ib_execs = fetch_ib_day_executions(connector, day_str)

    entries = [t for t in post_mortem if t.get("event") == "entry_fill"]
    exits = [t for t in post_mortem if t.get("event") == "exit_postmortem"]
    aborts = [t for t in post_mortem if "abort" in str(t.get("event", ""))]

    wins = sum(1 for t in trades if t.get("result") == "win" or t.get("won") is True)
    losses = sum(1 for t in trades if t.get("result") == "loss" or t.get("won") is False)
    day_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in trades)

    timeline: List[Dict[str, Any]] = []
    for bucket, kind in (
        (entries, "entry"),
        (exits, "exit"),
        (aborts, "abort"),
        (ib_execs, "ib"),
        (trades, "journal"),
    ):
        for rec in bucket:
            timeline.append({
                "kind": kind,
                "ticker": rec.get("ticker") or rec.get("symbol", "?"),
                "event": rec.get("event", kind),
                "pnl_usd": rec.get("pnl_usd"),
                "price": rec.get("fill_px") or rec.get("price") or rec.get("exit") or rec.get("entry"),
                "shares": rec.get("shares") or rec.get("qty"),
                "side": rec.get("side"),
                "reason": (rec.get("reason") or rec.get("exit_reason") or "")[:120],
                "timestamp": rec.get("timestamp"),
                "_sort_ts": rec.get("_sort_ts", 0),
            })
    timeline.sort(key=lambda r: r.get("_sort_ts", 0))

    account = {}
    if runner is not None:
        try:
            runner._refresh_account_balance()
            account = {
                "ib_account": round(getattr(runner, "account_equity", 0), 2),
                "bot_nav": round(getattr(runner, "bot_nav", 0), 2),
                "bot_cash": round(getattr(runner, "bot_cash", 0), 2),
                "trades_today": getattr(runner, "trades_today", 0),
                "position": getattr(runner, "current_ticker", None),
                "shares": getattr(runner, "shares", 0),
            }
        except Exception:
            pass

    return {
        "day": day_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_tz": str(MARKET_TZ),
        "summary": {
            "trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / max(wins + losses, 1) * 100, 1),
            "day_pnl_usd": round(day_pnl, 2),
            "entries": len(entries),
            "exits": len(exits),
            "aborts": len(aborts),
            "ib_fills": len(ib_execs),
            "ai_decisions": len(ai_decisions),
            "snapshots": len(snapshots),
        },
        "account": account,
        "trades": trades,
        "timeline": timeline,
        "post_mortem": post_mortem,
        "ib_executions": ib_execs,
        "ai_decisions": ai_decisions[-50:],
        "snapshots": snapshots,
        "audit_events": audit[-30:],
    }


def format_structured_report(report: Dict[str, Any], max_lines: int = 80) -> str:
    """Human-readable activity ledger (TWS-style, no AI)."""
    s = report.get("summary", {})
    acct = report.get("account", {})
    lines = [
        f"📊 DAILY ACTIVITY — {report.get('day', '?')}",
        f"Trades {s.get('trades', 0)} · {s.get('wins', 0)}W/{s.get('losses', 0)}L · "
        f"Win {s.get('win_rate_pct', 0):.0f}% · P&L ${s.get('day_pnl_usd', 0):+.2f}",
        f"Entries {s.get('entries', 0)} · Exits {s.get('exits', 0)} · "
        f"Aborts {s.get('aborts', 0)} · IB fills {s.get('ib_fills', 0)}",
    ]
    if acct:
        lines.append(
            f"IB ${acct.get('ib_account', 0):,.2f} · NAV ${acct.get('bot_nav', 0):,.2f} · "
            f"Cash ${acct.get('bot_cash', 0):,.2f}"
        )
        if acct.get("position"):
            lines.append(f"Open: {acct.get('shares', 0):.0f} {acct.get('position')}")

    lines.append("— ACTIVITY —")
    for i, ev in enumerate(report.get("timeline", [])[:max_lines]):
        ts = (ev.get("timestamp") or "")[:19].replace("T", " ")
        ticker = ev.get("ticker", "?")
        kind = ev.get("kind", "?").upper()
        px = ev.get("price")
        sh = ev.get("shares")
        pnl = ev.get("pnl_usd")
        detail = f"{kind} {ticker}"
        if sh:
            detail += f" {sh}sh"
        if px:
            detail += f" @ ${float(px):.4f}"
        if pnl is not None:
            detail += f" P&L ${float(pnl):+.2f}"
        reason = ev.get("reason")
        if reason:
            detail += f" ({reason})"
        lines.append(f"{ts or '—':>19} │ {detail}")

    remaining = len(report.get("timeline", [])) - max_lines
    if remaining > 0:
        lines.append(f"… +{remaining} more events")
    return "\n".join(lines)
