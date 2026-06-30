#!/usr/bin/env python3
"""
core/daily_ib_learning.py — Full IB day bundle → Ollama analyze + PPO train.

At session end: fetch ALL IB data (executions, orders, trades, account, positions)
for the finished market day, merge bot journals/AI telemetry, summarize with Ollama,
ingest into the experience buffer, and train PPO so the next session beats yesterday.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.daily_activity_report import (
    _is_same_et_day,
    _parse_ts,
    collect_day_report,
    fetch_ib_day_executions,
)
from core.experience_buffer import append as buffer_append, load_all
from core.market_hours import (
    MARKET_TZ,
    learning_day_for_trigger,
    now_et,
    previous_market_day,
)
from core.notify import log

if TYPE_CHECKING:
    from core.connector import IBConnector
    from core.scalper_runner import ScalperRunner

PACK_DIR = Path("models/daily_ib_learning")
DONE_PREFIX = ".ib_learn_done_"
HISTORY_PATH = Path("models/daily_ib_learning_history.jsonl")
PPO_LEDGER = Path("models/ppo_entry_ledger.jsonl")
PROFIT_HUNT_LEDGER = Path("models/profit_hunt_ledger.jsonl")

_lock = threading.Lock()


def _day_marker(day_str: str) -> Path:
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    return PACK_DIR / f"{DONE_PREFIX}{day_str}"


def _pack_path(day_str: str) -> Path:
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    return PACK_DIR / f"pack_{day_str}.json"


def fetch_ib_account_snapshot(connector: Optional["IBConnector"]) -> Dict[str, Any]:
    """Pull live IB account values and portfolio summary — delegates to ib_truth."""
    if connector is None or not connector.is_connected():
        return {}
    try:
        from core.ib_truth import build_snapshot, ib_truth_context

        snap = build_snapshot(connector.ib, connector.cfg if hasattr(connector, "cfg") else None)
        ctx = ib_truth_context()
        return {
            "values": snap.account.tags,
            "portfolio": ctx.get("ib_positions", []),
            "open_orders": len(snap.open_orders),
        }
    except Exception as exc:
        log.debug(f"IB account snapshot: {exc}")
        return {}


def fetch_ib_day_orders(
    connector: Optional["IBConnector"],
    day_str: str,
) -> List[Dict[str, Any]]:
    """Open + recently completed IB orders touching the ET day."""
    if connector is None or not connector.is_connected():
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    try:
        ib = connector.ib
        ib.reqAllOpenOrders()
        ib.sleep(0.35)
        sources = list(ib.openTrades()) + list(ib.trades())
        for t in sources:
            contract = t.contract
            order = t.order
            status = t.orderStatus
            oid = getattr(order, "orderId", 0)
            sym = getattr(contract, "symbol", "?")
            key = (oid, sym)
            if key in seen:
                continue
            fill_times: List[datetime] = []
            for fill in getattr(t, "fills", []) or []:
                ts = _parse_ts(getattr(fill.execution, "time", None))
                if ts and _is_same_et_day(ts, day_str):
                    fill_times.append(ts)
            st = getattr(status, "status", "?") if status else "?"
            if not fill_times and st not in ("Submitted", "PreSubmitted", "PendingSubmit", "ApiPending"):
                continue
            if fill_times or st in ("Submitted", "PreSubmitted", "PendingSubmit", "ApiPending"):
                seen.add(key)
                out.append({
                    "symbol": sym,
                    "order_id": oid,
                    "action": getattr(order, "action", "?"),
                    "order_type": type(order).__name__,
                    "qty": float(getattr(order, "totalQuantity", 0) or 0),
                    "status": st,
                    "filled": float(getattr(status, "filled", 0) or 0) if status else 0,
                    "avg_fill_price": round(
                        float(getattr(status, "avgFillPrice", 0) or 0), 4,
                    ) if status else 0,
                    "fills_on_day": len(fill_times),
                    "_kind": "ib_order",
                })
    except Exception as exc:
        log.debug(f"IB orders fetch: {exc}")
    return out


def fetch_ib_day_trades(
    connector: Optional["IBConnector"],
    day_str: str,
) -> List[Dict[str, Any]]:
    """IB Trade objects with fills on the ET day."""
    if connector is None or not connector.is_connected():
        return []
    out: List[Dict[str, Any]] = []
    try:
        ib = connector.ib
        ib.reqExecutions()
        ib.sleep(0.35)
        for trade in ib.trades():
            day_fills: List[Dict[str, Any]] = []
            for fill in getattr(trade, "fills", []) or []:
                ex = fill.execution
                ts = _parse_ts(getattr(ex, "time", None))
                if ts is None or not _is_same_et_day(ts, day_str):
                    continue
                comm = 0.0
                cr = getattr(fill, "commissionReport", None)
                if cr:
                    comm = float(getattr(cr, "commission", 0) or 0)
                day_fills.append({
                    "side": getattr(ex, "side", "?"),
                    "shares": float(getattr(ex, "shares", 0) or 0),
                    "price": round(float(getattr(ex, "price", 0) or 0), 4),
                    "exec_id": getattr(ex, "execId", ""),
                    "commission": round(comm, 4),
                    "timestamp": ts.isoformat(),
                    "_sort_ts": ts.timestamp(),
                })
            if not day_fills:
                continue
            order = trade.order
            status = trade.orderStatus
            out.append({
                "symbol": getattr(trade.contract, "symbol", "?"),
                "order_id": getattr(order, "orderId", 0),
                "action": getattr(order, "action", "?"),
                "status": getattr(status, "status", "?") if status else "?",
                "fills": sorted(day_fills, key=lambda f: f.get("_sort_ts", 0)),
                "fill_count": len(day_fills),
                "_kind": "ib_trade",
            })
    except Exception as exc:
        log.debug(f"IB trades fetch: {exc}")
    return out


def fetch_full_ib_day_bundle(
    connector: Optional["IBConnector"],
    day_str: str,
) -> Dict[str, Any]:
    """All IB-provided data for one ET market day."""
    executions = fetch_ib_day_executions(connector, day_str)
    orders = fetch_ib_day_orders(connector, day_str)
    trades = fetch_ib_day_trades(connector, day_str)
    account = fetch_ib_account_snapshot(connector)
    return {
        "day": day_str,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "executions": executions,
        "orders": orders,
        "trades": trades,
        "account": account,
        "counts": {
            "executions": len(executions),
            "orders": len(orders),
            "trades": len(trades),
            "positions": len(account.get("portfolio", [])),
        },
    }


def _read_jsonl_day(path: Path, day_str: str, limit: int = 500) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(rec.get("timestamp") or rec.get("ts") or rec.get("time_utc"))
            if _is_same_et_day(ts, day_str):
                rows.append(rec)
    except Exception:
        pass
    return rows[-limit:]


def _load_prior_pack(day_str: str) -> Optional[Dict[str, Any]]:
    prior = previous_market_day(
        datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=MARKET_TZ)
    )
    path = _pack_path(prior)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def compare_vs_prior_day(pack: Dict[str, Any]) -> Dict[str, Any]:
    """Day-over-day metrics — goal: today must beat yesterday."""
    day = pack.get("day", "")
    prior = _load_prior_pack(day)
    cur_summary = pack.get("bot", {}).get("summary", {})
    cur_pnl = float(cur_summary.get("day_pnl_usd", 0) or 0)
    cur_wr = float(cur_summary.get("win_rate_pct", 0) or 0)
    cur_trades = int(cur_summary.get("trades", 0) or 0)

    if not prior:
        return {
            "has_prior": False,
            "message": "First saved IB learning pack — no prior day to beat.",
            "day_pnl_usd": cur_pnl,
            "win_rate_pct": cur_wr,
            "trades": cur_trades,
        }

    ps = prior.get("bot", {}).get("summary", {})
    prior_pnl = float(ps.get("day_pnl_usd", 0) or 0)
    prior_wr = float(ps.get("win_rate_pct", 0) or 0)
    prior_trades = int(ps.get("trades", 0) or 0)
    pnl_delta = round(cur_pnl - prior_pnl, 2)
    wr_delta = round(cur_wr - prior_wr, 1)

    improved = pnl_delta > 0 or (pnl_delta == 0 and wr_delta > 0)
    return {
        "has_prior": True,
        "prior_day": prior.get("day"),
        "day_pnl_usd": cur_pnl,
        "prior_pnl_usd": prior_pnl,
        "pnl_delta_usd": pnl_delta,
        "win_rate_pct": cur_wr,
        "prior_win_rate_pct": prior_wr,
        "win_rate_delta": wr_delta,
        "trades": cur_trades,
        "prior_trades": prior_trades,
        "beat_yesterday": improved,
        "goal": "next session must exceed prior day P&L and process quality",
    }


def collect_ib_learning_pack(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    connector: Optional["IBConnector"] = None,
    day_str: Optional[str] = None,
    *,
    trigger: str = "session_end",
) -> Dict[str, Any]:
    """Full learning pack: IB + bot journals + AI ledgers for one market day."""
    day_str = day_str or learning_day_for_trigger(trigger)
    connector = connector or (getattr(runner, "conn", None) if runner else None)

    log.info(f"📦 Collecting full IB learning pack for {day_str} ({trigger})…")
    ib_bundle = fetch_full_ib_day_bundle(connector, day_str)
    bot_report = collect_day_report(cfg, runner, connector, day_str)

    pack = {
        "day": day_str,
        "trigger": trigger,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_tz": str(MARKET_TZ),
        "ib": ib_bundle,
        "bot": bot_report,
        "ppo_entries": _read_jsonl_day(PPO_LEDGER, day_str, 200),
        "profit_hunts": _read_jsonl_day(PROFIT_HUNT_LEDGER, day_str, 150),
        "experience_today": _day_buffer_stats(day_str),
    }
    pack["comparison"] = compare_vs_prior_day(pack)
    return pack


def _day_buffer_stats(day_str: str) -> Dict[str, Any]:
    total = wins = 0
    sources: Dict[str, int] = {}
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
        "records": total,
        "sources": sources,
        "win_rate": round(wins / max(total, 1), 3),
    }


def ingest_day_into_experience_buffer(pack: Dict[str, Any]) -> int:
    """Push IB executions + bot trades into the experience buffer for PPO."""
    day = pack.get("day", "")
    count = 0
    trades = pack.get("bot", {}).get("trades", [])
    for t in trades:
        pnl = float(t.get("pnl_usd", 0) or 0)
        buffer_append({
            "source": "ib_day_trade",
            "ticker": t.get("ticker", ""),
            "action": "TRADE",
            "pnl_usd": pnl,
            "win": 1 if pnl > 0 else (0 if pnl < 0 else None),
            "reward": pnl,
            "exit_reason": t.get("exit_reason", t.get("reason", "")),
            "confidence": float(t.get("confidence", 0.5) or 0.5),
            "timestamp": t.get("exit_time") or t.get("timestamp") or day,
            "learning_day": day,
        })
        count += 1

    for ex in pack.get("ib", {}).get("executions", []):
        buffer_append({
            "source": "ib_execution",
            "ticker": ex.get("symbol", ""),
            "action": ex.get("side", ""),
            "entry_price": ex.get("price"),
            "shares": ex.get("shares"),
            "pnl_usd": 0,
            "timestamp": ex.get("timestamp", day),
            "learning_day": day,
        })
        count += 1

    for row in pack.get("ppo_entries", []):
        if row.get("features"):
            buffer_append({
                "source": "ppo_entry",
                "ticker": row.get("ticker", ""),
                "action": "BUY" if row.get("ppo_action") == 1 else "HOLD",
                "features": row.get("features"),
                "confidence": float(row.get("ppo_conf", 0) or 0),
                "reward": float(row.get("reward", 0) or 0),
                "win": row.get("win"),
                "timestamp": row.get("timestamp", day),
                "learning_day": day,
            })
            count += 1
    return count


def train_ppo_from_day(cfg: BotConfig, day_str: str, steps: Optional[int] = None) -> bool:
    """PPO micro-train on experience buffer records from one market day."""
    steps = steps or int(getattr(cfg, "DAILY_IB_PPO_TRAIN_STEPS", 15000))
    day_recs = []
    for rec in load_all():
        ts = _parse_ts(rec.get("timestamp"))
        if rec.get("learning_day") == day_str or _is_same_et_day(ts, day_str):
            day_recs.append(rec)
    if not day_recs:
        log.info(f"🧠 PPO day-train: no buffer records for {day_str}")
        return False
    feature_recs = [r for r in day_recs if r.get("features")]
    if not feature_recs:
        log.info(
            f"🧠 PPO day-train: {len(day_recs)} records but no features — "
            "running unified buffer train"
        )
        try:
            from core.online_trainer import run_unified_training
            run_unified_training(cfg, ppo_steps=min(steps, 20000))
            return True
        except Exception as exc:
            log.debug(f"Unified training fallback: {exc}")
            return False
    try:
        from core.online_trainer import _train_ppo_on_buffer
        return bool(_train_ppo_on_buffer(cfg, steps=steps))
    except Exception as exc:
        log.debug(f"PPO day train: {exc}")
        return False


def compose_ollama_day_analysis(
    pack: Dict[str, Any],
    think_fn: Optional[Callable[[str], str]],
    cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    """Ollama analyzes full IB day — lessons + tomorrow beat-yesterday plan."""
    cmp_ = pack.get("comparison", {})
    summary = pack.get("bot", {}).get("summary", {})
    ib_counts = pack.get("ib", {}).get("counts", {})

    fallback = {
        "headline": (
            f"IB day {pack.get('day')}: {summary.get('trades', 0)} trades, "
            f"P&L ${summary.get('day_pnl_usd', 0):+.2f}"
        ),
        "lessons": [
            f"IB fills: {ib_counts.get('executions', 0)}, orders: {ib_counts.get('orders', 0)}",
            f"Win rate {summary.get('win_rate_pct', 0):.0f}%",
        ],
        "beat_yesterday_plan": [
            "Tighter entries on weak council alignment",
            "Lock green profits faster when AI stalls",
        ],
        "beat_yesterday": cmp_.get("beat_yesterday", False),
        "raw": "",
    }

    if not think_fn:
        return fallback

    max_chars = 3500
    prompt = (
        "You are HANOON pilot AI. Analyze the FULL IB trading day bundle below.\n"
        "GOAL: tomorrow/next session MUST beat yesterday on P&L and process quality.\n\n"
        f"DAY: {pack.get('day')} | TRIGGER: {pack.get('trigger')}\n\n"
        "DAY vs PRIOR DAY:\n"
        f"{json.dumps(cmp_, default=str)[:800]}\n\n"
        "BOT SUMMARY:\n"
        f"{json.dumps(summary, default=str)}\n\n"
        "IB COUNTS:\n"
        f"{json.dumps(ib_counts, default=str)}\n\n"
        "IB EXECUTIONS (sample):\n"
        f"{json.dumps(pack.get('ib', {}).get('executions', [])[:20], default=str)[:2000]}\n\n"
        "BOT TRADES:\n"
        f"{json.dumps(pack.get('bot', {}).get('trades', [])[:15], default=str)[:2000]}\n\n"
        "PPO ENTRIES TODAY:\n"
        f"{json.dumps(pack.get('ppo_entries', [])[:10], default=str)[:1200]}\n\n"
        "PROFIT HUNTS:\n"
        f"{json.dumps(pack.get('profit_hunts', [])[:10], default=str)[:1000]}\n\n"
        "Respond ONLY valid JSON:\n"
        "{\n"
        '  "headline": "one line verdict on the day",\n'
        '  "ib_insights": ["what IB fills/orders reveal"],\n'
        '  "lessons": ["concrete lesson from trades and AI decisions"],\n'
        '  "mistakes": ["what cost money"],\n'
        '  "wins": ["what worked"],\n'
        '  "beat_yesterday_plan": ["specific action for next session"],\n'
        '  "ppo_focus": ["what PPO should weight more/less"],\n'
        '  "beat_yesterday": true\n'
        "}"
    )
    try:
        raw = (think_fn(prompt) or "").strip()
        text = raw
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            parsed["raw"] = raw[:max_chars]
            parsed.setdefault("beat_yesterday", cmp_.get("beat_yesterday", False))
            return parsed
    except Exception as exc:
        log.debug(f"Ollama day analysis: {exc}")
    fallback["raw"] = ""
    return fallback


def _save_pack(pack: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, str]:
    day = pack.get("day", "")
    path = _pack_path(day)
    payload = {**pack, "analysis": analysis}
    path.write_text(json.dumps(payload, indent=2, default=str))

    summary_path = PACK_DIR / f"analysis_{day}.txt"
    lines = [
        f"HANOON IB DAY LEARNING — {day}",
        "=" * 50,
        analysis.get("headline", ""),
        "",
        "LESSONS:",
    ]
    for lesson in analysis.get("lessons", [])[:8]:
        lines.append(f"  • {lesson}")
    lines.append("")
    lines.append("BEAT YESTERDAY PLAN:")
    for item in analysis.get("beat_yesterday_plan", [])[:6]:
        lines.append(f"  → {item}")
    cmp_ = pack.get("comparison", {})
    if cmp_.get("has_prior"):
        lines.append("")
        lines.append(
            f"vs {cmp_.get('prior_day')}: P&L Δ ${cmp_.get('pnl_delta_usd', 0):+.2f} · "
            f"beat yesterday: {cmp_.get('beat_yesterday')}"
        )
    summary_path.write_text("\n".join(lines))

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "day": day,
        "trigger": pack.get("trigger"),
        "summary": pack.get("bot", {}).get("summary", {}),
        "comparison": cmp_,
        "analysis_headline": analysis.get("headline", ""),
        "beat_yesterday": analysis.get("beat_yesterday", cmp_.get("beat_yesterday")),
        "ingested": pack.get("_ingested", 0),
        "ppo_trained": pack.get("_ppo_trained", False),
    }
    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass

    marker = _day_marker(day)
    marker.write_text(pack.get("generated_at", ""))

    return {"pack": str(path), "analysis": str(summary_path)}


def run_daily_ib_learning_cycle(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    *,
    connector: Optional["IBConnector"] = None,
    think_fn: Optional[Callable[[str], str]] = None,
    trigger: str = "session_end",
    day_str: Optional[str] = None,
    force: bool = False,
    train_ppo: bool = True,
    apply_commander: bool = True,
) -> Dict[str, Any]:
    """
    Full cycle: fetch IB day → analyze → ingest → PPO train → save.
  Idempotent per ET day unless force=True.
    """
    if not getattr(cfg, "DAILY_IB_LEARNING_ENABLED", True):
        return {"status": "disabled"}

    day_str = day_str or learning_day_for_trigger(trigger)
    marker = _day_marker(day_str)

    with _lock:
        if not force and marker.exists():
            return {"status": "already_done", "day": day_str}

    connector = connector or (getattr(runner, "conn", None) if runner else None)
    if think_fn is None and runner and getattr(runner, "ai_commander", None):
        ac = runner.ai_commander
        def _think(p: str) -> str:
            try:
                return (ac.think(p, task="reason") or ac.compose_telegram(p) or "").strip()
            except Exception:
                return (ac.compose_telegram(p) or "").strip()
        think_fn = _think

    pack = collect_ib_learning_pack(cfg, runner, connector, day_str, trigger=trigger)
    analysis = compose_ollama_day_analysis(pack, think_fn, cfg)

    ingested = ingest_day_into_experience_buffer(pack)
    pack["_ingested"] = ingested
    log.info(f"📚 Ingested {ingested} records into experience buffer for {day_str}")

    ppo_ok = False
    if train_ppo:
        ppo_ok = train_ppo_from_day(cfg, day_str)
        pack["_ppo_trained"] = ppo_ok
        log.info(f"🧠 PPO day training {'ok' if ppo_ok else 'skipped'} for {day_str}")

    paths = _save_pack(pack, analysis)

    if apply_commander and runner and getattr(runner, "ai_commander", None):
        try:
            from core.commander_learning import run_commander_learning_cycle
            run_commander_learning_cycle(
                cfg, runner,
                think_fn=think_fn or runner.ai_commander.compose_telegram,
                trigger=f"ib_day_learning:{day_str}:{analysis.get('headline', '')[:80]}",
                apply=True,
            )
        except Exception as exc:
            log.debug(f"Commander apply after IB learning: {exc}")

    if getattr(runner, "consciousness", None):
        try:
            for lesson in analysis.get("lessons", [])[:5]:
                runner.consciousness.observe_trade({
                    "ticker": "SESSION",
                    "action": "IB_DAY_LESSON",
                    "reason": lesson,
                    "pnl": pack.get("comparison", {}).get("pnl_delta_usd", 0),
                })
        except Exception:
            pass

    headline = analysis.get("headline", f"IB learning {day_str}")
    log.info(f"✅ IB day learning complete — {day_str}: {headline[:100]}")

    notifier = getattr(runner, "notifier", None) if runner else None
    if notifier and trigger in ("session_end", "market_close"):
        try:
            cmp_ = pack.get("comparison", {})
            notifier.info(
                f"📚 IB DAY LEARNING — {day_str}\n{headline[:200]}\n"
                f"Ingested {ingested} · PPO {'✓' if ppo_ok else '—'}\n"
                f"vs prior: ${cmp_.get('pnl_delta_usd', 0):+.2f} · "
                f"beat yesterday: {cmp_.get('beat_yesterday', '?')}"
            )
        except Exception:
            pass

    try:
        from core.hanoon_clean_publish import schedule_clean_repo_publish
        schedule_clean_repo_publish(cfg, trigger="daily_learning")
    except Exception:
        pass

    return {
        "status": "ok",
        "day": day_str,
        "trigger": trigger,
        "analysis": analysis,
        "ingested": ingested,
        "ppo_trained": ppo_ok,
        "paths": paths,
        "comparison": pack.get("comparison", {}),
    }


def schedule_daily_ib_learning(
    cfg: BotConfig,
    runner: Optional["ScalperRunner"] = None,
    *,
    trigger: str = "session_end",
    connector: Optional["IBConnector"] = None,
    force: bool = False,
) -> None:
    """Background thread — non-blocking."""
    if not getattr(cfg, "DAILY_IB_LEARNING_ENABLED", True):
        return

    def _worker():
        try:
            run_daily_ib_learning_cycle(
                cfg, runner,
                connector=connector or (getattr(runner, "conn", None) if runner else None),
                trigger=trigger,
                force=force,
            )
        except Exception as exc:
            log.warning(f"Daily IB learning failed: {exc}")

    if runner is not None and getattr(runner, "_worker", None):
        try:
            runner._worker._executor.submit(_worker)
            return
        except Exception:
            pass
    threading.Thread(target=_worker, name="daily-ib-learning", daemon=True).start()
