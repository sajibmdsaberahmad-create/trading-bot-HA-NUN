#!/usr/bin/env python3
"""
core/account_evaluator.py — AI account evaluation on every market open/close.

Captures full account snapshots (IB balance, positions, trade history),
compares against the previous session boundary, logs everything, and
sends an Ollama-crafted comparative statement to Telegram.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.account_view import day_pnl as account_day_pnl
from core.market_hours import format_et, get_market_state
from core.notify import log

if TYPE_CHECKING:
    from core.ai_commander import AICommander
    from core.scalper_runner import ScalperRunner

SNAPSHOT_LOG = Path("models/account_snapshots.jsonl")
EVAL_LOG = Path("models/account_evaluation_log.jsonl")
TRADE_JOURNAL = Path("models/trade_journal.json")
PERF_CSV = Path("performance.csv")


class AccountEvaluator:
    """Snapshot, compare, log, and AI-brief account state on session boundaries."""

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        SNAPSHOT_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._last_eval_ts: Dict[str, float] = {}
        self._min_gap = float(getattr(cfg, "AI_ACCOUNT_EVAL_MIN_SEC", 300))

    def evaluate(
        self,
        runner: "ScalperRunner",
        event: str,
        notifier=None,
        ai_commander: Optional["AICommander"] = None,
        autopilot=None,
        consciousness=None,
        pilot=None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Full evaluation cycle: snapshot → compare → log → Telegram.

        event: market_open | market_close | session_startup | session_shutdown | trade_closed
        """
        if not getattr(self.cfg, "AI_ACCOUNT_EVALUATION", True):
            return {}

        notify_events = {
            "market_open", "market_close", "session_startup", "session_shutdown",
        }
        log_only = event == "trade_closed"
        if event not in notify_events and not log_only:
            return {}

        now = time.time()
        if not force and not log_only and event in self._last_eval_ts:
            if now - self._last_eval_ts[event] < self._min_gap:
                return {}

        runner._refresh_account_balance()
        current = self._capture_snapshot(runner, event)
        previous = self._load_previous_snapshot(event)
        comparison = self._compare(current, previous)
        statement = self._compose_statement(
            event, current, previous, comparison, ai_commander,
        )

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "snapshot": current,
            "previous_event": previous.get("event") if previous else None,
            "comparison": comparison,
            "statement": statement,
        }
        self._append_jsonl(SNAPSHOT_LOG, current)
        self._append_jsonl(EVAL_LOG, record)
        self._last_eval_ts[event] = now

        log.info(f"📋 ACCOUNT EVAL [{event}] │ {statement.splitlines()[0][:100]}")

        if (
            event in notify_events
            and notifier
            and getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True)
        ):
            from core.pilot_mode import send_dynamic_notification
            ctx = {
                **comparison,
                "event": event,
                "statement": statement,
                "market_state": get_market_state(self.cfg),
                "time_et": format_et(),
            }
            send_dynamic_notification(
                notifier, autopilot, f"account_{event}",
                ctx, statement,
                ai_commander=ai_commander,
                consciousness=consciousness,
                pilot=pilot,
            )

        if event in notify_events or event == "trade_closed":
            if getattr(self.cfg, "GIT_PUSH_DURING_SESSION", False):
                try:
                    from core.git_sync import push_learning_checkpoint_async
                    push_learning_checkpoint_async(f"account_{event}")
                except Exception:
                    pass

        return record

    def on_market_transition(
        self,
        runner: "ScalperRunner",
        old_state: str,
        new_state: str,
        notifier=None,
        ai_commander=None,
        autopilot=None,
        consciousness=None,
        pilot=None,
    ):
        """Fire evaluation when market crosses open/close boundaries."""
        if new_state == "open" and old_state in ("closed", "pre_market"):
            self.evaluate(
                runner, "market_open", notifier, ai_commander,
                autopilot, consciousness, pilot,
            )
            if getattr(self.cfg, "DAILY_IB_LEARNING_ON_MARKET_OPEN", True):
                try:
                    from core.daily_ib_learning import schedule_daily_ib_learning
                    schedule_daily_ib_learning(
                        self.cfg, runner,
                        trigger="market_open",
                        connector=getattr(runner, "conn", None),
                    )
                except Exception as exc:
                    log.debug(f"Market-open IB learning: {exc}")
        elif new_state == "closed" and old_state in ("open", "after_hours", "pre_market", "overnight"):
            self.evaluate(
                runner, "market_close", notifier, ai_commander,
                autopilot, consciousness, pilot,
            )
            self._schedule_self_evaluation(
                runner, notifier, ai_commander, autopilot, consciousness, pilot,
            )
        elif new_state == "after_hours" and old_state == "open":
            self.evaluate(
                runner, "market_close", notifier, ai_commander,
                autopilot, consciousness, pilot,
            )
            self._schedule_self_evaluation(
                runner, notifier, ai_commander, autopilot, consciousness, pilot,
            )

    def _capture_snapshot(self, runner: "ScalperRunner", event: str) -> Dict[str, Any]:
        baseline = float(getattr(self.cfg, "INITIAL_CASH", 1000))
        ib_start = getattr(runner, "_ib_starting_balance", None) or runner.account_equity
        bot_nav = getattr(runner, "bot_nav", runner.bot_cash)
        day_pnl_usd, day_pnl_pct = account_day_pnl(runner, self.cfg)
        day_pnl = day_pnl_usd
        ib_change = runner.account_equity - ib_start

        trades = self._all_trades(runner)
        wins = sum(1 for t in trades if t.get("result") == "win" or t.get("won"))
        losses = sum(1 for t in trades if t.get("result") == "loss" or (t.get("won") is False))

        snap = {
            "event": event,
            "time_et": format_et(),
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "market_state": get_market_state(self.cfg),
            "ib_account": round(runner.account_equity, 2),
            "ib_start": round(ib_start, 2),
            "ib_change": round(ib_change, 2),
            "ib_change_pct": round(ib_change / ib_start * 100, 2) if ib_start else 0,
            "bot_cash": round(runner.bot_cash, 2),
            "bot_nav": round(bot_nav, 2),
            "baseline": baseline,
            "day_pnl": round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl / baseline * 100, 2) if baseline else 0,
            "trades_today": getattr(runner, "trades_today", 0),
            "trades_total": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / max(wins + losses, 1) * 100, 1),
            "position": self._position_summary(runner),
            "ib_positions": self._ib_positions(runner),
            "open_orders": self._open_orders(runner),
            "recent_trades": trades[-10:],
            "pilot_level": self._pilot_level(runner),
        }
        return snap

    def _compare(self, current: Dict, previous: Optional[Dict]) -> Dict[str, Any]:
        if not previous:
            return {
                "has_previous": False,
                "message": "First snapshot — no prior session to compare.",
                "nav": current.get("bot_nav", 0),
                "ib_account": current.get("ib_account", 0),
                "day_pnl": current.get("day_pnl", 0),
                "trades_today": current.get("trades_today", 0),
            }

        prev_nav = float(previous.get("bot_nav", 0))
        cur_nav = float(current.get("bot_nav", 0))
        prev_ib = float(previous.get("ib_account", 0))
        cur_ib = float(current.get("ib_account", 0))
        prev_trades = int(previous.get("trades_total", 0))
        cur_trades = int(current.get("trades_total", 0))

        return {
            "has_previous": True,
            "previous_event": previous.get("event"),
            "previous_time_et": previous.get("time_et"),
            "nav_delta": round(cur_nav - prev_nav, 2),
            "nav_delta_pct": round((cur_nav - prev_nav) / (prev_nav + 1e-9) * 100, 2),
            "ib_delta": round(cur_ib - prev_ib, 2),
            "ib_delta_pct": round((cur_ib - prev_ib) / (prev_ib + 1e-9) * 100, 2),
            "trades_since": cur_trades - prev_trades,
            "wins_since": int(current.get("wins", 0)) - int(previous.get("wins", 0)),
            "losses_since": int(current.get("losses", 0)) - int(previous.get("losses", 0)),
            "position_was": previous.get("position"),
            "position_now": current.get("position"),
            "nav": cur_nav,
            "ib_account": cur_ib,
            "day_pnl": current.get("day_pnl", 0),
            "trades_today": current.get("trades_today", 0),
        }

    def _compose_statement(
        self,
        event: str,
        current: Dict,
        previous: Optional[Dict],
        comparison: Dict,
        ai_commander: Optional["AICommander"],
    ) -> str:
        fallback = self._structured_statement(event, current, comparison)
        if event == "session_startup":
            return fallback
        if not ai_commander or not getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True):
            return fallback

        prompt = (
            "You are HANOON — autonomous trading pilot AI. Write a detailed ACCOUNT BRIEFING "
            "for your commander on Telegram.\n\n"
            f"SESSION EVENT: {event}\n"
            f"US MARKET: {current.get('market_state', '').upper()} | {current.get('time_et', '')}\n\n"
            f"CURRENT ACCOUNT:\n{json.dumps(current, default=str)[:1400]}\n\n"
        )
        if previous:
            prompt += (
                f"PREVIOUS SNAPSHOT ({previous.get('event')} @ {previous.get('time_et')}):\n"
                f"{json.dumps({k: previous[k] for k in ('ib_account', 'bot_nav', 'day_pnl', 'position', 'trades_total', 'wins', 'losses') if k in previous}, default=str)}\n\n"
            )
        prompt += (
            f"CHANGES SINCE LAST:\n{json.dumps(comparison, default=str)[:600]}\n\n"
            "Write 5-8 lines:\n"
            "• Headline: what happened (open/close/startup)\n"
            "• IB account & bot NAV with exact $ deltas vs last snapshot\n"
            "• Trades: count, W/L, win rate, notable P&L\n"
            "• Open positions & orders right now\n"
            "• Your pilot read: what improved, what to watch next session\n"
            "First-person voice. Exact numbers only. Plain text, no JSON. Max 500 chars."
        )

        try:
            raw = ai_commander.compose_telegram(prompt)
            if raw and len(raw.strip()) >= 40:
                return raw.strip()[:500]
        except Exception as exc:
            log.debug(f"Account eval AI compose: {exc}")
        return fallback

    def _structured_statement(
        self, event: str, current: Dict, comparison: Dict
    ) -> str:
        labels = {
            "market_open": "🌅 MARKET OPEN — ACCOUNT BRIEF",
            "market_close": "🌆 MARKET CLOSE — ACCOUNT BRIEF",
            "session_startup": "🚀 SESSION START — ACCOUNT BRIEF",
            "session_shutdown": "🛬 SESSION END — ACCOUNT BRIEF",
        }
        head = labels.get(event, f"📋 ACCOUNT — {event.upper()}")
        lines = [
            head,
            f"IB ${current.get('ib_account', 0):,.2f} "
            f"(Δ ${current.get('ib_change', 0):+,.2f}) · "
            f"NAV ${current.get('bot_nav', 0):,.2f} · "
            f"Day P&L ${current.get('day_pnl', 0):+,.2f}",
            f"Trades {current.get('trades_today', 0)} today · "
            f"{current.get('wins', 0)}W/{current.get('losses', 0)}L · "
            f"Win {current.get('win_rate_pct', 0):.0f}%",
        ]
        pos = current.get("position")
        if pos:
            lines.append(f"Position: {pos}")
        ib_pos = current.get("ib_positions") or []
        if ib_pos:
            names = ", ".join(
                f"{p['symbol']} {p['shares']:.0f}@${p.get('avg_cost', 0):.2f}"
                for p in ib_pos[:5]
            )
            lines.append(f"IB holdings: {names}")

        if comparison.get("has_previous"):
            lines.append(
                f"vs last ({comparison.get('previous_event')}): "
                f"NAV Δ ${comparison.get('nav_delta', 0):+,.2f} · "
                f"IB Δ ${comparison.get('ib_delta', 0):+,.2f} · "
                f"+{comparison.get('trades_since', 0)} trades"
            )
        lines.append(current.get("time_et", ""))
        return "\n".join(lines)

    def _load_previous_snapshot(self, current_event: str) -> Optional[Dict]:
        """Load the most recent snapshot from the opposite session boundary."""
        pair = {
            "market_open": ("market_close", "session_shutdown"),
            "market_close": ("market_open", "session_startup"),
            "session_startup": ("market_close", "session_shutdown"),
            "session_shutdown": ("market_open", "market_close"),
        }
        targets = pair.get(current_event, ())
        if not SNAPSHOT_LOG.exists():
            return None
        try:
            lines = SNAPSHOT_LOG.read_text().strip().splitlines()
            for line in reversed(lines[-200:]):
                try:
                    rec = json.loads(line)
                    if rec.get("event") in targets:
                        return rec
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return None

    def _all_trades(self, runner: "ScalperRunner") -> List[Dict]:
        trades: List[Dict] = list(getattr(runner, "trade_journal", []) or [])
        if TRADE_JOURNAL.exists():
            try:
                persisted = json.loads(TRADE_JOURNAL.read_text())
                if isinstance(persisted, list):
                    seen = {f"{t.get('ticker')}_{t.get('entry')}_{t.get('exit')}" for t in trades}
                    for t in persisted:
                        key = f"{t.get('ticker')}_{t.get('entry')}_{t.get('exit')}"
                        if key not in seen:
                            trades.append(t)
                            seen.add(key)
            except Exception:
                pass
        return trades

    def _position_summary(self, runner: "ScalperRunner") -> Optional[str]:
        if runner.shares > 0 and runner.current_ticker:
            px = 0.0
            try:
                px = runner._latest_price()
            except Exception:
                pass
            val = runner.shares * px
            return (
                f"{runner.shares:.0f} {runner.current_ticker} "
                f"@ ~${px:.4f} (${val:,.0f})"
            )
        return None

    def _ib_positions(self, runner: "ScalperRunner") -> List[Dict]:
        try:
            from core.ib_truth import get_snapshot, refresh
            ib = getattr(runner, "ib", None)
            if ib is not None:
                refresh(ib, getattr(runner, "cfg", None), force=False)
            snap = get_snapshot()
            if snap.refreshed_at > 0:
                return [
                    {
                        "symbol": p.symbol,
                        "shares": p.qty,
                        "avg_cost": round(p.avg_cost, 4),
                        "unrealized_pnl": round(p.unrealized_pnl, 2),
                    }
                    for p in snap.positions
                ]
        except Exception as exc:
            log.debug(f"IB positions snapshot: {exc}")
        return []

    def _open_orders(self, runner: "ScalperRunner") -> List[Dict]:
        try:
            from core.ib_truth import get_snapshot
            snap = get_snapshot()
            if snap.refreshed_at > 0:
                return [
                    {
                        "symbol": o.symbol,
                        "action": o.action,
                        "qty": o.qty,
                        "type": o.order_type,
                        "status": o.status,
                        "lmt": o.lmt_price,
                        "stop": o.aux_price,
                    }
                    for o in snap.open_orders
                    if o.status not in ("Filled", "Cancelled", "Inactive")
                ][:15]
        except Exception as exc:
            log.debug(f"Open orders snapshot: {exc}")
        return []

    def _pilot_level(self, runner: "ScalperRunner") -> str:
        try:
            if hasattr(runner, "pilot"):
                return runner.pilot.get_veteran_status().get("level", "Cadet")
        except Exception:
            pass
        return "Cadet"

    def _schedule_self_evaluation(
        self,
        runner: "ScalperRunner",
        notifier=None,
        ai_commander=None,
        autopilot=None,
        consciousness=None,
        pilot=None,
    ) -> None:
        """Background end-of-day self-evaluation (premarket → close)."""
        try:
            from core.daily_self_evaluation import schedule_daily_self_evaluation
            schedule_daily_self_evaluation(
                self.cfg,
                runner,
                notifier=notifier,
                ai_commander=ai_commander,
                autopilot=autopilot,
                consciousness=consciousness,
                pilot=pilot,
                connector=getattr(runner, "conn", None),
            )
        except Exception as exc:
            log.debug(f"Self-eval schedule: {exc}")

    @staticmethod
    def _append_jsonl(path: Path, record: Dict):
        try:
            with open(path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            log.debug(f"Account log write failed: {exc}")
