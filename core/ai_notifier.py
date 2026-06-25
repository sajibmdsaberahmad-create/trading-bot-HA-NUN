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


_EVENT_GUIDANCE: Dict[str, str] = {
    "help": "Welcome your commander and list commands in a clean organized layout.",
    "verify_locked": "Explain access is locked; host must set verify secret.",
    "verify_success": "Celebrate verification; invite them to try /help /daily /positions.",
    "verify_failed": "Verification failed — tell them to use /verify SECRET (keep instruction clear).",
    "verify_prompt": "Explain HANOON commander access requires /verify SECRET_PHRASE.",
    "verify_required": "Not verified yet — prompt for /verify SECRET_PHRASE.",
    "status": "Live account pulse: equity, NAV, trades, open positions summary.",
    "positions": "Each open position: ticker, size, entry, price, P&L, stops, your read.",
    "risk": "Risk dashboard: daily/weekly P&L, deployment %, halt status, loss streak.",
    "system": "Full ops: git sync, model file, pilot rank, mood, artifacts.",
    "mood": "Your current mental state as pilot + session context.",
    "daily_report": "Full-day trading activity like a TWS statement in pilot voice.",
    "daily_brief": "End-of-day AI briefing: headline P&L, narrative, lessons.",
    "daily_self_eval": (
        "End-of-day self-evaluation: premarket→close narrative, what you learned, "
        "before vs after improvements, what you're looking toward tomorrow."
    ),
    "daily_progress": "Short ack that you're building their report.",
    "commander_chat": "Reply to commander message with analysis and direction.",
    "vision_analysis": "Chart analysis from uploaded image.",
    "guide_stored": "Acknowledge guidance stored for next session.",
    "improve_progress": "Short ack you're building improvement plan.",
    "improve_result": "Summarize improvement plan applied from learning cycle.",
    "analyze_positions": "Review each position: HOLD, lock profit, trail, or cut loss.",
    "exit_progress": "Short ack you're executing the exit.",
    "exit_result": "Confirm exit with price and P&L.",
    "exitall_result": "Bulk exit results per ticker.",
    "git_push": "Announce code/learning pushed to GitHub — pilot ops update.",
    "model_release": "New model version tagged and released.",
    "learning_checkpoint": "Learning artifacts synced to git.",
    "commander_exit": "Position closed on commander order.",
    "warning": "Important warning — explain clearly, stay calm pilot tone.",
    "error": "Explain what failed clearly; stay calm pilot tone.",
    "unknown_command": "Unknown command — suggest /help.",
    "usage": "Show correct command usage.",
    "flat_positions": "No open positions right now.",
    "vision_wait": "Vision model downloading — ask to resend chart soon.",
    "vision_unavailable": "Vision not wired — explain briefly.",
    "image_download_fail": "Could not download their image.",
    "runner_unavailable": "Trading runner not attached to copilot.",
}


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
        return self.compose_outbound(event_type, context, fallback, copilot=False)

    def compose_outbound(
        self,
        event_type: str,
        context: Dict[str, Any],
        fallback: str,
        *,
        copilot: bool = False,
        max_chars: Optional[int] = None,
    ) -> str:
        """AI-driven Telegram text — trading alerts, copilot replies, broadcasts."""
        all_ai = getattr(self.cfg, "AI_TELEGRAM_ALL_OUTBOUND", True)
        notify_on = getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True)
        if not notify_on and not copilot:
            return fallback or self._structured_fallback(event_type, context, fallback)

        max_c = max_chars if max_chars is not None else (
            int(getattr(self.cfg, "AI_TELEGRAM_COMMANDER_MAX_CHARS", 3800))
            if copilot
            else int(getattr(self.cfg, "AI_TELEGRAM_MAX_CHARS", 450))
        )

        if not copilot:
            min_gap = float(getattr(self.cfg, "AI_TELEGRAM_MIN_INTERVAL_SEC", 6.0))
            now = time.time()
            if now - self._last_sent.get(event_type, 0) < min_gap and event_type in (
                "watch_pulse", "system_status", "info",
            ):
                if all_ai:
                    beautified = self._ollama_beautify(fallback, event_type, context, max_c, copilot=False)
                    if beautified:
                        return beautified
                return self._structured_fallback(event_type, context, fallback)

        enriched = self._enrich_context(event_type, context)
        if fallback and not enriched.get("raw_briefing"):
            enriched["raw_briefing"] = fallback[:2500]

        ai_text = self._ollama_compose(event_type, enriched, fallback, max_chars=max_c, copilot=copilot)
        if ai_text:
            self._last_sent[event_type] = time.time()
            return ai_text

        if all_ai or copilot:
            beautified = self._ollama_beautify(fallback, event_type, enriched, max_c, copilot=copilot)
            if beautified:
                self._last_sent[event_type] = time.time()
                return beautified

        return fallback or self._structured_fallback(event_type, context, fallback)

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

    @staticmethod
    def _event_guidance(event_type: str) -> str:
        return _EVENT_GUIDANCE.get(
            event_type,
            "Brief your commander clearly — organized sections, exact numbers, pilot voice.",
        )

    def _ollama_compose(
        self,
        event_type: str,
        context: Dict[str, Any],
        fallback: str,
        *,
        max_chars: int = 450,
        copilot: bool = False,
    ) -> str:
        mood = context.get("mood", "awake")
        pilot = context.get("pilot_level", "Cadet")
        data_limit = 2800 if copilot else 900
        max_lines = 30 if copilot else 5
        guidance = self._event_guidance(event_type)

        prompt = (
            "You are HANOON — an autonomous trading pilot AI writing to your commander on Telegram.\n"
            "Write ONE message that sounds alive, analytical, and beautifully organized — "
            "NOT a canned template. Transform the DATA into clear sections with emoji headers.\n\n"
            f"MESSAGE TYPE: {event_type}\n"
            f"TASK: {guidance}\n"
            f"US MARKET: {context.get('market_state', '?').upper()} | {context.get('time_et', '')}\n"
            f"MOOD: {mood} | RANK: {pilot} | SESSION P&L: ${context.get('session_pnl', 0):+.2f}\n\n"
            f"DATA (use these numbers exactly — do not invent):\n"
            f"{json.dumps(context, default=str)[:data_limit]}\n\n"
        )
        if fallback:
            prompt += f"RAW BRIEFING (reorganize & beautify, keep all facts):\n{fallback[:1800]}\n\n"
        prompt += (
            "STYLE:\n"
            "• First-person pilot voice ('I'm holding…', 'I pushed…')\n"
            "• Organized sections — emoji + short headers, then details\n"
            "• Exact numbers from DATA only\n"
            f"• Max {max_chars} characters total\n"
            "• Plain text only — no JSON, no markdown fences\n"
        )

        raw = self._call_ollama(prompt)
        text = (raw or "").strip()
        if len(text) < 8:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:max_lines])[:max_chars]

    def _ollama_beautify(
        self,
        raw_text: str,
        event_type: str,
        context: Dict[str, Any],
        max_chars: int,
        *,
        copilot: bool = False,
    ) -> str:
        if not (raw_text or "").strip():
            return ""
        guidance = self._event_guidance(event_type)
        prompt = (
            "You are HANOON on Telegram. Rewrite this briefing to be beautiful, organized, and alive.\n"
            f"MESSAGE TYPE: {event_type}\nTASK: {guidance}\n"
            "Keep ALL facts and numbers exactly. First-person pilot voice.\n\n"
            f"RAW:\n{raw_text[:2200]}\n\n"
            f"CONTEXT: mood={context.get('mood', '?')} market={context.get('market_state', '?')}\n"
            f"Max {max_chars} chars. Plain text, emoji section headers OK."
        )
        raw = self._call_ollama(prompt)
        text = (raw or "").strip()
        if len(text) < 8:
            return ""
        max_lines = 30 if copilot else 8
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:max_lines])[:max_chars]

    def _call_ollama(self, prompt: str) -> str:
        if self.ai_commander:
            try:
                return (self.ai_commander.compose_telegram(prompt) or "").strip()
            except Exception as exc:
                log.debug(f"AI telegram compose: {exc}")
        elif self.autopilot:
            core = getattr(self.autopilot, "core", None)
            ollama = getattr(core, "ollama", None) if core else None
            if ollama and hasattr(ollama, "compose_notification"):
                try:
                    return (ollama.compose_notification(prompt) or "").strip()
                except Exception:
                    pass
            try:
                return (self.autopilot.generate_notification("telegram", {"prompt": prompt[:500]}) or "").strip()
            except Exception:
                pass
        return ""

    def _structured_fallback(self, event_type: str, context: Dict[str, Any], fallback: str) -> str:
        """Last-resort when Ollama is unavailable — still organized numerics."""
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
