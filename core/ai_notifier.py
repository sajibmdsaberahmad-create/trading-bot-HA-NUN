#!/usr/bin/env python3
"""
core/ai_notifier.py — Ollama-crafted Telegram alerts for HANOON.

Turns raw trading events into short, analytical pilot briefings —
not static templates. Falls back to structured numeric summaries
if Ollama is busy or unavailable.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.market_hours import get_market_state, format_et
from core.notify import log

if TYPE_CHECKING:
    from core.ai_commander import AICommander
    from core.cognitive_autopilot import CognitiveAutopilot
    from core.consciousness import AIConsciousness
    from core.pilot_experience import PilotExperienceSystem


class TelegramAIComposer:
    """Compose Telegram messages via Ollama with rate limits and rich context."""

    def __init__(
        self,
        cfg: BotConfig,
        ai_commander: Optional["AICommander"] = None,
        autopilot: Optional["CognitiveAutopilot"] = None,
        consciousness: Optional["AIConsciousness"] = None,
        pilot: Optional["PilotExperienceSystem"] = None,
    ):
        self.cfg = cfg
        self.ai_commander = ai_commander
        self.autopilot = autopilot
        self.consciousness = consciousness
        self.pilot = pilot
        self._last_sent: Dict[str, float] = {}
        self._session_trades = 0
        self._session_pnl = 0.0

    def attach(
        self,
        ai_commander=None,
        autopilot=None,
        consciousness=None,
        pilot=None,
    ):
        if ai_commander is not None:
            self.ai_commander = ai_commander
        if autopilot is not None:
            self.autopilot = autopilot
        if consciousness is not None:
            self.consciousness = consciousness
        if pilot is not None:
            self.pilot = pilot

    def record_trade(self, pnl_usd: float):
        self._session_trades += 1
        self._session_pnl += float(pnl_usd or 0)

    def compose(self, event_type: str, context: Dict[str, Any], fallback: str) -> str:
        if not getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True):
            return self._structured_fallback(event_type, context, fallback)

        min_gap = float(getattr(self.cfg, "AI_TELEGRAM_MIN_INTERVAL_SEC", 6.0))
        now = time.time()
        if now - self._last_sent.get(event_type, 0) < min_gap and event_type in (
            "watch_pulse", "system_status", "info",
        ):
            return self._structured_fallback(event_type, context, fallback)

        enriched = self._enrich_context(event_type, context)
        ai_text = self._ollama_compose(event_type, enriched, fallback)
        if ai_text:
            self._last_sent[event_type] = now
            return ai_text

        return self._structured_fallback(event_type, context, fallback)

    def _enrich_context(self, event_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(context)
        out.setdefault("event", event_type)
        out["market_state"] = get_market_state(self.cfg)
        out["time_et"] = format_et(fmt="%H:%M ET")
        out["session_trades"] = self._session_trades
        out["session_pnl"] = round(self._session_pnl, 2)

        if self.pilot:
            try:
                vs = self.pilot.get_veteran_status()
                out["pilot_level"] = vs.get("level", "Cadet")
                out["pilot_xp"] = vs.get("total_xp", 0)
                out["flights"] = vs.get("flights_completed", 0)
            except Exception:
                pass

        if self.consciousness:
            try:
                ident = self.consciousness.get_identity()
                out["mood"] = ident.get("mood", "learning")
                out["mood_message"] = ident.get("mood_message", "")
            except Exception:
                pass

        if self.autopilot and getattr(self.autopilot, "core", None):
            try:
                st = self.autopilot.core.state
                out.setdefault("mood", st.mood)
                out["ai_confidence"] = round(float(st.confidence), 2)
            except Exception:
                pass

        # Calculated fields when entry/exit numbers present
        entry = float(out.get("entry") or out.get("entry_price") or 0)
        stop = float(out.get("stop") or out.get("stop_price") or 0)
        target = float(out.get("target") or out.get("target_price") or 0)
        price = float(out.get("price") or out.get("exit") or entry or 0)
        shares = float(out.get("shares") or out.get("qty") or 0)
        if entry > 0 and stop > 0:
            out["stop_dist_pct"] = round((entry - stop) / entry * 100, 2)
            out["risk_usd_calc"] = round((entry - stop) * shares, 2) if shares else out.get("risk_usd")
        if entry > 0 and target > 0:
            risk = max(entry - stop, 1e-9) if stop > 0 else entry * 0.01
            out["reward_risk_ratio"] = round((target - entry) / risk, 2)
        if price > 0 and shares > 0:
            out["deploy_usd"] = round(price * shares, 2)

        return out

    def _ollama_compose(self, event_type: str, context: Dict[str, Any], fallback: str) -> str:
        max_chars = int(getattr(self.cfg, "AI_TELEGRAM_MAX_CHARS", 450))
        mood = context.get("mood", "awake")
        pilot = context.get("pilot_level", "Cadet")

        prompt = (
            "You are HANOON — an autonomous trading pilot AI briefing your commander on Telegram.\n"
            "Write ONE message that sounds alive, analytical, and organized — NOT a canned template.\n\n"
            f"EVENT: {event_type}\n"
            f"US MARKET: {context.get('market_state', '?').upper()} | {context.get('time_et', '')}\n"
            f"MOOD: {mood} | RANK: {pilot} | SESSION P&L: ${context.get('session_pnl', 0):+.2f}\n\n"
            f"DATA (use these numbers exactly — do not invent):\n"
            f"{json.dumps(context, default=str)[:900]}\n\n"
            "STYLE:\n"
            "• Line 1: emoji + sharp headline (what happened)\n"
            "• Lines 2-4: key numbers — price, size, stop/target, risk $, R:R, deploy %\n"
            "• Last line: your read — confidence, gut, what you're watching next\n"
            "• First-person pilot voice ('I entered…', 'I'm holding…')\n"
            f"• Max {max_chars} characters total\n"
            "• Plain text only — no JSON, no markdown fences, no bullet dashes\n"
        )

        raw = ""
        if self.ai_commander:
            try:
                raw = self.ai_commander.compose_telegram(prompt)
            except Exception as exc:
                log.debug(f"AI telegram compose: {exc}")
        elif self.autopilot:
            core = getattr(self.autopilot, "core", None)
            ollama = getattr(core, "ollama", None) if core else None
            if ollama and hasattr(ollama, "compose_notification"):
                try:
                    raw = ollama.compose_notification(prompt) or ""
                except Exception:
                    pass
            if not raw:
                try:
                    raw = self.autopilot.generate_notification(event_type, context)
                except Exception:
                    pass

        text = (raw or "").strip()
        if len(text) < 12:
            return ""
        # Single message — drop extra lines if model rambled
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        text = "\n".join(lines[:5])[:max_chars]
        return text

    def _structured_fallback(self, event_type: str, context: Dict[str, Any], fallback: str) -> str:
        """Organized numeric briefing when Ollama is unavailable."""
        ctx = self._enrich_context(event_type, context)
        et = ctx.get("time_et", "")
        mood = ctx.get("mood", "—")
        pilot_read = (ctx.get("mood_message") or ctx.get("intuition") or "").strip()[:90]
        footer = f"{et} · Mood: {mood}"
        if pilot_read:
            footer = f"{pilot_read}\n{footer}"

        if event_type == "trade_opened":
            t = ctx.get("ticker", "?")
            sh = ctx.get("shares", "?")
            px = ctx.get("entry") or ctx.get("price", 0)
            stop = ctx.get("stop", 0)
            target = ctx.get("target", 0)
            risk = ctx.get("risk_usd") or ctx.get("risk_usd_calc", 0)
            rr = ctx.get("reward_risk_ratio", "—")
            deploy = ctx.get("deployed") or ctx.get("deploy_usd", 0)
            return (
                f"🎯 PILOT ENTRY │ {t}\n"
                f"{sh} sh @ ${float(px):.4f} · Deploy ${float(deploy):,.0f}\n"
                f"Stop ${float(stop):.4f} · TP ${float(target):.4f} · Risk ${float(risk):.2f}\n"
                f"R:R {rr} · {footer}"
            )

        if event_type == "trade_closed":
            t = ctx.get("ticker", "?")
            pnl = float(ctx.get("pnl_usd", 0))
            pct = float(ctx.get("pnl_pct", 0))
            result = ctx.get("result", "win" if pnl >= 0 else "loss").upper()
            emoji = "✅" if pnl >= 0 else "🔴"
            return (
                f"{emoji} FLIGHT CLOSED │ {t} · {result}\n"
                f"P&L ${pnl:+.2f} ({pct:+.2f}%) · Session ${ctx.get('session_pnl', 0):+.2f}\n"
                f"Rank {ctx.get('pilot_level', 'Cadet')} · {footer}"
            )

        if event_type == "early_exit":
            t = ctx.get("ticker", "?")
            pnl = float(ctx.get("pnl_usd", 0))
            reason = ctx.get("reason", "exit")[:60]
            return (
                f"⚡ EARLY EXIT │ {t}\n"
                f"P&L ${pnl:+.2f} · {reason}\n"
                f"Session ${ctx.get('session_pnl', 0):+.2f} · {et}"
            )

        if event_type == "targets_locked":
            names = ctx.get("targets") or ctx.get("locked", [])
            if isinstance(names, list):
                names = ", ".join(str(x) for x in names)
            top = ctx.get("top_score", "—")
            return (
                f"🔭 TARGET LOCK │ {names}\n"
                f"Top score {top} · Market {ctx.get('market_state', '').upper()}\n"
                f"Watching for volume spikes · {et}"
            )

        if event_type == "startup":
            ib = ctx.get("ib_balance") or ctx.get("equity", 0)
            return (
                f"🚀 HANOON ONLINE\n"
                f"Market {ctx.get('market_state', '').upper()} · Pilot {ctx.get('pilot_level', 'Cadet')}\n"
                f"IB ${float(ib):,.0f} · AI notifications live\n"
                f"{footer}"
            )

        if event_type == "daily_summary":
            nav = ctx.get("nav") or ctx.get("bot_nav", 0)
            pnl = ctx.get("pnl") or ctx.get("session_pnl", 0)
            trades = ctx.get("trades_today") or ctx.get("trades", 0)
            return (
                f"📊 SESSION WRAP\n"
                f"NAV ${float(nav):,.2f} · Day P&L ${float(pnl):+,.2f}\n"
                f"Trades {trades} · {et}"
            )

        if event_type == "session_close":
            pnl = float(ctx.get("pnl", 0))
            pct = float(ctx.get("pnl_pct", 0))
            ib_chg = float(ctx.get("ib_change", 0))
            return (
                f"🛬 SESSION CLOSE\n"
                f"Bot P&L ${pnl:+,.2f} ({pct:+.2f}%) · IB Δ ${ib_chg:+,.2f}\n"
                f"Trades {ctx.get('trades_today', 0)} · {et} · Mood {mood}"
            )

        if event_type.startswith("account_"):
            stmt = ctx.get("statement") or fallback
            if stmt and len(stmt) > 20:
                return stmt[:500]
            nav_d = ctx.get("nav_delta", 0)
            ib_d = ctx.get("ib_delta", 0)
            return (
                f"📋 ACCOUNT BRIEF\n"
                f"NAV ${ctx.get('nav', 0):,.2f} (Δ ${float(nav_d):+,.2f})\n"
                f"IB ${ctx.get('ib_account', 0):,.2f} (Δ ${float(ib_d):+,.2f})\n"
                f"Day P&L ${ctx.get('day_pnl', 0):+,.2f} · Trades {ctx.get('trades_today', 0)}\n"
                f"{et}"
            )

        # Generic — still cleaner than raw fallback
        head = fallback.splitlines()[0][:80] if fallback else event_type
        return f"🧠 HANOON │ {head}\n{et} · {mood}"


def send_smart_telegram(
    notifier,
    event_type: str,
    context: Dict[str, Any],
    fallback: str,
    ai_commander=None,
    autopilot=None,
    consciousness=None,
    pilot=None,
) -> None:
    """Single entry point for AI Telegram alerts."""
    composer = getattr(notifier, "_ai_composer", None)
    if composer is None:
        composer = TelegramAIComposer(
            getattr(notifier, "cfg", BotConfig()),
            ai_commander=ai_commander,
            autopilot=autopilot,
            consciousness=consciousness,
            pilot=pilot,
        )
        notifier._ai_composer = composer

    if event_type in ("trade_closed", "early_exit") and context.get("pnl_usd") is not None:
        composer.record_trade(float(context["pnl_usd"]))

    msg = composer.compose(event_type, context, fallback)
    if ai_commander:
        try:
            ai_commander.journal(f"NOTIFY_{event_type}", msg[:200], context)
        except Exception:
            pass
    try:
        notifier.info(msg, event_type=event_type, context=context, skip_compose=True)
    except TypeError:
        notifier.info(msg)
