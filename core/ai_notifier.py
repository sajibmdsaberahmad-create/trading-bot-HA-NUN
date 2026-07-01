#!/usr/bin/env python3
"""
core/ai_notifier.py — Telegram alerts for HANOON.

Routine alerts use structured templates (zero API). Commander/copilot
replies may use cloud council when COUNCIL_NOTIFY_API_COPILOT=true.
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
    "commander_chat": "Reply as Halim — HANOON's companion mind. Fresh generative voice, pilot first-person.",
    "halim_companion_session": "Halim proactively greets commander at session open — warm companion, hunt tone from live data.",
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
    "brain_evolution": "Owned brain finished a growth cycle — summarize stage, dataset, proxy, PPO teacher.",
    "brain_proxy_trained": "Teacher proxy distilled from council decisions — accuracy and samples.",
    "brain_ppo_teacher": "PPO teacher session — win rate, mutations, local vs cloud.",
    "brain_stage_up": "Brain matured to next growth stage — less API, smarter students.",
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
        """Telegram text — API only for copilot / explicitly enabled events."""
        from core.council_budget import (
            classify_notify_event,
            notify_event_wants_api,
        )

        notify_on = getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True)
        if not notify_on and not copilot:
            return fallback or self._structured_fallback(event_type, context, fallback)

        max_c = max_chars if max_chars is not None else (
            int(getattr(self.cfg, "AI_TELEGRAM_COMMANDER_MAX_CHARS", 3800))
            if copilot
            else int(getattr(self.cfg, "AI_TELEGRAM_MAX_CHARS", 450))
        )

        purpose = classify_notify_event(event_type, copilot=copilot)
        use_api = notify_event_wants_api(self.cfg, event_type, copilot=copilot)

        enriched = self._enrich_context(event_type, context)
        if fallback and not enriched.get("raw_briefing"):
            enriched["raw_briefing"] = fallback[:2500]
        structured = fallback or self._structured_fallback(event_type, enriched, fallback)

        if not use_api:
            halim_text = self._try_halim_trade_notify(
                event_type, enriched, structured, max_c, copilot=copilot,
            )
            if halim_text:
                return halim_text
            return structured

        if not copilot:
            min_gap = float(getattr(self.cfg, "AI_TELEGRAM_MIN_INTERVAL_SEC", 6.0))
            now = time.time()
            if now - self._last_sent.get(event_type, 0) < min_gap and event_type in (
                "watch_pulse", "system_status", "info",
            ):
                return structured

        ai_text = self._ollama_compose(
            event_type, enriched, fallback,
            max_chars=max_c, copilot=copilot, purpose=purpose,
        )
        if ai_text:
            self._last_sent[event_type] = time.time()
            return ai_text

        halim_text = self._try_halim_trade_notify(
            event_type, enriched, structured, max_c, copilot=copilot,
        )
        if halim_text:
            return halim_text

        out = structured
        try:
            from core.halim_capabilities import record_teacher_action
            record_teacher_action(
                purpose or "notify", fallback or str(context)[:500], out,
                source="template_fallback", cfg=self.cfg,
            )
        except Exception:
            pass
        return out

    def _enrich_context(self, event_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(context)
        out.setdefault("event", event_type)
        out["market_state"] = get_market_state(self.cfg)
        out["time_et"] = format_et(fmt="%H:%M ET")

        # Session totals — IB FIFO only (never composer local accumulator)
        ib_sess = out.get("ib_fifo_session_pnl")
        if ib_sess is not None:
            out["session_pnl"] = round(float(ib_sess), 2)
            out["day_pnl"] = round(float(ib_sess), 2)
        elif out.get("session_pnl") is not None:
            out["session_pnl"] = round(float(out["session_pnl"]), 2)
        ib_trips = out.get("ib_round_trips")
        if ib_trips is not None:
            out["session_trades"] = int(ib_trips)
            out["trades_today"] = int(ib_trips)

        # Economic display — IB NetLiq only
        ib_eq = float(out.get("ib_equity") or out.get("ib_account") or out.get("equity") or 0)
        if ib_eq > 0:
            out["nav"] = ib_eq
            out["equity"] = ib_eq
            out["ib_account"] = ib_eq
        out.pop("bot_nav", None)
        out.pop("bot_cash", None)

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

        if "war_mode" not in out:
            try:
                from core.war_account import war_account_context
                out.update(war_account_context(self.cfg))
            except Exception:
                pass

        return out

    def _try_halim_trade_notify(
        self,
        event_type: str,
        context: Dict[str, Any],
        structured_fallback: str,
        max_chars: int,
        *,
        copilot: bool = False,
    ) -> str:
        if copilot:
            return ""
        try:
            from core.halim_companion import halim_trading_notify
            runner = context.get("_runner")
            return halim_trading_notify(
                event_type,
                context,
                cfg=self.cfg,
                runner=runner,
                structured_fallback=structured_fallback,
                max_chars=max_chars,
            )
        except Exception as exc:
            log.debug(f"Halim trade notify: {exc}")
            return ""

    @staticmethod
    def _war_line(ctx: Dict[str, Any]) -> str:
        if not ctx.get("war_enabled"):
            return ""
        settled = float(ctx.get("war_settled_cash", 0) or 0)
        mode = ctx.get("war_mode", "—")
        nav = float(ctx.get("war_nav", 0) or 0)
        if ctx.get("war_balance_driven"):
            left = int(ctx.get("war_bullets_remaining", 0) or 0)
            fired = int(ctx.get("war_round_trips_today", 0) or 0)
            return (
                f"War {mode} · pool ${nav:,.0f} · settled ${settled:,.0f} · "
                f"{left} bullets left · {fired} fired"
            )
        trips = int(ctx.get("war_round_trips_today", 0) or 0)
        max_t = int(ctx.get("war_round_trips_max", 0) or 0)
        return f"War {mode} · pool ${nav:,.0f} · settled ${settled:,.0f} · trips {trips}/{max_t}"

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
        purpose: str = "notify",
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

        raw = self._call_ollama(prompt, purpose=purpose, event_type=event_type, copilot=copilot)
        text = (raw or "").strip()
        if len(text) < 8:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:max_lines])[:max_chars]

    def _call_ollama(
        self,
        prompt: str,
        *,
        purpose: str = "notify",
        event_type: Optional[str] = None,
        copilot: bool = False,
    ) -> str:
        if self.ai_commander:
            try:
                return (
                    self.ai_commander.compose_telegram(
                        prompt,
                        purpose=purpose,
                        event_type=event_type,
                        copilot=copilot,
                    ) or ""
                ).strip()
            except Exception as exc:
                log.debug(f"AI telegram compose: {exc}")
        elif self.autopilot:
            core = getattr(self.autopilot, "core", None)
            council = getattr(core, "ollama", None) if core else None
            if council and hasattr(council, "compose_notification"):
                try:
                    return (
                        council.compose_notification(
                            prompt,
                            purpose=purpose,
                            event_type=event_type,
                            copilot=copilot,
                        ) or ""
                    ).strip()
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
        war_line = self._war_line(ctx)
        if war_line:
            footer = f"{war_line}\n{footer}"

        if event_type == "trade_opened":
            t = ctx.get("ticker", "?")
            sh = ctx.get("shares", "?")
            px = ctx.get("entry") or ctx.get("price", 0)
            stop = ctx.get("stop", 0)
            target = ctx.get("target", 0)
            risk = ctx.get("risk_usd") or ctx.get("risk_usd_calc", 0)
            rr = ctx.get("reward_risk_ratio", "—")
            deploy = ctx.get("deployed") or ctx.get("deploy_usd", 0)
            ib_tag = " · IB fill" if ctx.get("pnl_source") == "ib_fill" else ""
            return (
                f"🎯 PILOT ENTRY │ {t}{ib_tag}\n"
                f"{sh} sh @ ${float(px):.4f} · Deploy ${float(deploy):,.0f}\n"
                f"Stop ${float(stop):.4f} · TP ${float(target):.4f} · Risk ${float(risk):.2f}\n"
                f"R:R {rr} · IB ${float(ctx.get('ib_equity', 0)):,.0f} · Session ${float(ctx.get('session_pnl', 0)):+,.2f}\n"
                f"{footer}"
            )

        if event_type == "trade_closed":
            t = ctx.get("ticker", "?")
            pnl = float(ctx.get("pnl_usd", 0))
            pct = float(ctx.get("pnl_pct", 0))
            result = ctx.get("result", "win" if pnl >= 0 else "loss").upper()
            emoji = "✅" if pnl >= 0 else "🔴"
            ib_tag = " · IB" if ctx.get("pnl_source") in ("ib_fill", "ib_truth") else ""
            exit_px = ctx.get("exit_fill") or ctx.get("price") or 0
            entry_px = ctx.get("entry_fill") or ctx.get("entry") or 0
            fill_line = ""
            if entry_px and exit_px:
                fill_line = f"\nIB ${float(entry_px):.4f} → ${float(exit_px):.4f}"
            return (
                f"{emoji} FLIGHT CLOSED │ {t} · {result}{ib_tag}\n"
                f"P&L ${pnl:+.2f} ({pct:+.2f}%){fill_line}\n"
                f"Session ${float(ctx.get('session_pnl', 0)):+,.2f} · IB ${float(ctx.get('ib_equity', 0)):,.0f}\n"
                f"Rank {ctx.get('pilot_level', 'Cadet')} · {footer}"
            )

        if event_type == "early_exit":
            t = ctx.get("ticker", "?")
            pnl = float(ctx.get("pnl_usd", 0))
            reason = ctx.get("reason", "exit")[:60]
            ib_tag = " · IB" if ctx.get("pnl_source") in ("ib_fill", "ib_truth") else ""
            return (
                f"⚡ EARLY EXIT │ {t}{ib_tag}\n"
                f"P&L ${pnl:+.2f} · {reason}\n"
                f"Session ${float(ctx.get('session_pnl', 0)):+,.2f} · IB ${float(ctx.get('ib_equity', 0)):,.0f} · {et}"
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
            ib = ctx.get("ib_equity") or ctx.get("ib_account") or 0
            war = self._war_line(ctx)
            war_part = f"\n{war}" if war else ""
            sess = float(ctx.get("ib_fifo_session_pnl", ctx.get("session_pnl", 0)) or 0)
            return (
                f"🚀 HANOON ONLINE · IB Truth\n"
                f"Market {ctx.get('market_state', '').upper()} · Pilot {ctx.get('pilot_level', 'Cadet')}\n"
                f"IB ${float(ib):,.0f} · Session P&L ${sess:+,.2f}{war_part}\n"
                f"{footer}"
            )

        if event_type == "daily_summary":
            nav = ctx.get("ib_equity") or ctx.get("nav") or 0
            pnl = ctx.get("ib_fifo_session_pnl") or ctx.get("session_pnl", 0)
            trades = ctx.get("ib_round_trips") or ctx.get("trades_today") or 0
            return (
                f"📊 SESSION WRAP · IB\n"
                f"NetLiq ${float(nav):,.2f} · Session P&L ${float(pnl):+,.2f}\n"
                f"Round-trips {trades} · {et}"
            )

        if event_type == "brain_evolution":
            wr = ctx.get("win_rate")
            wr_s = f"{float(wr):.0%}" if wr is not None else "—"
            return (
                f"🧬 BRAIN EVOLUTION │ {str(ctx.get('stage', '?')).upper()}\n"
                f"Dataset {ctx.get('dataset_pairs', '—')} · Trades {ctx.get('trade_count', '—')} · WR {wr_s}\n"
                f"Proxy {ctx.get('proxy_status', '—')} · Git {ctx.get('git_push', 'queued')}\n"
                f"{footer}"
            )

        if event_type == "brain_proxy_trained":
            acc = ctx.get("accuracy")
            acc_s = f"{float(acc):.0%}" if acc is not None else "—"
            return (
                f"🎓 PROXY TRAINED │ acc {acc_s}\n"
                f"Samples {ctx.get('samples', '—')} · stage {ctx.get('stage', '?')}\n"
                f"{footer}"
            )

        if event_type == "brain_ppo_teacher":
            try:
                from core.halim_companion import explain_ppo_teacher_notify
                return explain_ppo_teacher_notify(ctx) + f"\n{footer}"
            except Exception:
                pass
            wr = ctx.get("win_rate")
            wr_s = f"{float(wr):.0%}" if wr is not None else "—"
            ppo_ok = bool(ctx.get("ppo_trained", False))
            ppo_note = "weights updated" if ppo_ok else "weights unchanged (normal — tuning still applied)"
            return (
                f"🧠 Halim learning │ WR {wr_s} · {ctx.get('trade_count', '?')} trades\n"
                f"Tuning {ctx.get('mutations_applied', 0)} adj · PPO {ppo_note}\n"
                f"{footer}"
            )

        if event_type == "brain_stage_up":
            return (
                f"👶 STAGE UP │ {ctx.get('from_stage', '?')} → {ctx.get('to_stage', '?')}\n"
                f"{str(ctx.get('description', ''))[:120]}\n"
                f"{footer}"
            )

        if event_type == "session_close":
            ib_chg = float(ctx.get("ib_change", ctx.get("ib_fifo_session_pnl", ctx.get("session_pnl", 0))))
            ib_eq = float(ctx.get("ib_equity") or ctx.get("ib_account") or 0)
            war = self._war_line(ctx)
            war_part = f"\n{war}" if war else ""
            trips = ctx.get("ib_round_trips") or ctx.get("trades_today", 0)
            return (
                f"🛬 SESSION CLOSE · IB\n"
                f"Session P&L ${ib_chg:+,.2f} · NetLiq ${ib_eq:,.0f}{war_part}\n"
                f"Round-trips {trips} · {et} · Mood {mood}"
            )

        if event_type.startswith("account_"):
            stmt = ctx.get("statement") or fallback
            if stmt and len(stmt) > 20:
                return stmt[:500]
            ib_eq = float(ctx.get("ib_equity") or ctx.get("ib_account") or ctx.get("nav", 0))
            ib_d = float(ctx.get("ib_change", ctx.get("ib_fifo_session_pnl", ctx.get("day_pnl", 0))))
            return (
                f"📋 ACCOUNT BRIEF · IB\n"
                f"NetLiq ${ib_eq:,.2f} · Session P&L ${ib_d:+,.2f}\n"
                f"Round-trips {ctx.get('ib_round_trips', ctx.get('trades_today', 0))} · {et}\n"
                f"{mood}"
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
    runner = context.get("_runner")
    cfg = getattr(notifier, "cfg", BotConfig())
    if runner is not None:
        try:
            from core.notify_ib_context import telegram_notify_context
            extra = {k: v for k, v in context.items() if k != "_runner"}
            context = telegram_notify_context(runner, cfg, extra, event_type=event_type)
        except Exception:
            pass

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
        if context.get("pnl_source") in ("ib_fill", "ib_truth"):
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
