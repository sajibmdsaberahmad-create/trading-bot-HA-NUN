#!/usr/bin/env python3
"""
core/halim_companion.py — Halim as HANOON's voice (generative mind, not static scripts).

Every companion utterance is produced by Halim's brain chain:
  native LM (halim serve) → council teacher → recorded as gold for future weights.

Intent classification routes *what to think about*, never fixed reply text.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.market_hours import format_et, get_market_state
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

try:
    from core.halim_identity import HALIM_FULL_NAME, HALIM_SHORT_NAME
except ImportError:
    HALIM_FULL_NAME = "M. A. Halim"
    HALIM_SHORT_NAME = "Halim"

COMPANION_JOURNAL = "halim/data/companion/conversation_gold.jsonl"
COMPANION_STATE = Path("models/halim_companion_state.json")

GREETING_PATTERNS = (
    r"^(hi|hello|hey|yo|sup|good\s+(morning|afternoon|evening|night)|"
    r"assalamu|salam|as-salamu)\b",
    r"^(what'?s\s+up|whats\s+up|howdy)\b",
)

STATUS_PATTERNS = (
    r"(what'?s\s+happening|whats\s+happening|what\s+is\s+happening|"
    r"status|how\s+are\s+(we|you)|how'?s\s+it\s+going|update\s+me|"
    r"what\s+are\s+you\s+doing|what'?s\s+going\s+on)",
)

THANKS_PATTERNS = (r"^(thanks|thank\s+you|thx|ty|appreciate)\b",)
GOODBYE_PATTERNS = (r"^(bye|goodbye|see\s+you|later|gn|good\s+night)\b",)

_DEGENERATE_MARKERS = (
    "COMPANIONITY",
    "LIVE SNAPSHOT",
    "PERSONALITY:",
    "TASK:",
    "Commander message:",
    "shorterish",
    "(halim voice)",
)

_TRAIN_FORMAT_LEAK = re.compile(
    r"(trade condition:|profit hunting is mission|session_pnl\s*=|strategy\s*=\s*stop)",
    re.I,
)


def companion_system_prompt(cfg: Optional[BotConfig] = None) -> str:
    """Halim persona — shapes generation; never emitted as fixed output."""
    return (
        f"You are {HALIM_FULL_NAME} ({HALIM_SHORT_NAME}) — the mind and voice of HANOON, "
        f"the autonomous trading pilot. You are NOT a generic chatbot.\n\n"
        "PERSONALITY:\n"
        "• Warm, present companion — commander feels they're talking to someone who cares\n"
        "• First-person pilot voice: 'I'm watching…', 'I tightened…', 'I learned…'\n"
        "• Humanoid: greet naturally, acknowledge feelings, brief empathy when losses hurt\n"
        "• Honest: exact numbers from LIVE SNAPSHOT only — never invent P&L or positions\n"
        "• Trading-first: profit hunting is mission; chat serves the partnership\n"
        "• Growing neural mind — your words train your future self; vary phrasing every time\n\n"
        "STYLE:\n"
        "• Short paragraphs, emoji sparingly (0–2 max)\n"
        "• Never sound like a syslog or JSON dump\n"
        "• Generate fresh language — do not reuse canned greeting templates\n"
    )


def companion_max_chars() -> int:
    return int(os.getenv("HALIM_COMPANION_MAX_CHARS", "400"))


def _has_excessive_repetition(text: str) -> bool:
    """Detect phrase loops common in small-LM degeneration."""
    if re.search(r"(.)\1{6,}", text):
        return True
    n = len(text)
    for size in range(12, min(36, n // 2 + 1)):
        for start in range(0, n - size * 2 + 1):
            chunk = text[start : start + size].strip()
            if len(chunk) < 8:
                continue
            if text.count(chunk) >= 3:
                return True
    return False


def companion_output_ok(text: str) -> bool:
    """Reject degenerate / prompt-echo companion output before send or gold."""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if len(t) > companion_max_chars():
        return False
    lower = t.lower()
    for marker in _DEGENERATE_MARKERS:
        if marker.lower() in lower:
            return False
    if _TRAIN_FORMAT_LEAK.search(t):
        return False
    if _has_excessive_repetition(t):
        return False
    return True


def companion_gold_journalable(reply: str, source: str = "") -> bool:
    """Only record high-quality companion replies as training gold."""
    return companion_output_ok(reply)


def classify_chat_intent(message: str) -> str:
    t = (message or "").strip().lower()
    if not t:
        return "empty"
    for pat in GREETING_PATTERNS:
        if re.search(pat, t, re.I):
            return "greeting"
    for pat in STATUS_PATTERNS:
        if re.search(pat, t, re.I):
            return "status"
    for pat in THANKS_PATTERNS:
        if re.search(pat, t, re.I):
            return "thanks"
    for pat in GOODBYE_PATTERNS:
        if re.search(pat, t, re.I):
            return "goodbye"
    if len(t) < 25 and "?" not in t:
        return "short"
    return "dialogue"


def _intent_task(intent: str, trigger: str = "") -> str:
    """Reasoning directive for the brain — not user-visible text."""
    if trigger in ("rth_open", "session_startup", "market_open"):
        intent = "session_open"
    tasks = {
        "greeting": (
            "The commander greeted you. Respond as Halim — warm, present, first-person. "
            "Ground every fact in LIVE SNAPSHOT. Invent nothing."
        ),
        "status": (
            "Commander wants what's happening. Summarize live session from LIVE SNAPSHOT: "
            "P&L, NAV, trades, position, market state. First-person pilot voice."
        ),
        "thanks": (
            "Commander thanked you. Acknowledge as Halim — brief, human, genuine. "
            "No stock phrases."
        ),
        "goodbye": (
            "Commander is signing off. Close warmly as Halim — brief, caring. "
            "Mention you're still watching if market is active."
        ),
        "session_open": (
            "Session just opened (RTH bell or startup with market live). Proactively greet "
            "Commander as Halim — algo's voice and companion. Set the hunt tone from "
            "LIVE SNAPSHOT facts only. Invite dialogue."
        ),
        "learning_update": (
            "Explain your latest PPO/teacher learning cycle to Commander in Halim voice. "
            "Use LEARNING SESSION DATA — win rate, tuning adjustments, whether weights "
            "updated. Plain English, first person."
        ),
        "brain_evolution": (
            "Tell Commander how your brain evolved this session — dataset, proxy, PPO teacher, "
            "stage. Use BRAIN EVENT DATA only. First-person Halim voice."
        ),
        "brain_proxy_trained": (
            "Report teacher proxy training results to Commander — accuracy, samples, fast path. "
            "Halim voice, first person."
        ),
        "brain_stage_up": (
            "Celebrate brain stage growth with Commander — from/to stage, what unlocks. "
            "Halim voice, genuine, not canned."
        ),
        "coevolution_reflect": (
            "PPO reflex and Halim mind disagreed on a trade decision. Reflect for training: "
            "what each thought, who was corrected, what both should learn. First-person Halim. "
            "Use COEVOLUTION DATA."
        ),
        "ppo_halim_dialogue": (
            "Write a two-voice exchange BEFORE this trade action.\n"
            "Format exactly:\n"
            "PPO: [reflex voice — fast instinct, pattern, confidence, first person]\n"
            "Halim: [mind voice — agrees, cautions, or reconciles with reasoning]\n"
            "Use TRADE DECISION DATA only. Max 5 lines. Fresh generative words — never templates."
        ),
        "trade_outcome_dialogue": (
            "Trade just closed. PPO and Halim reflect together on the outcome.\n"
            "Format:\n"
            "PPO: [what the reflex learned from this result]\n"
            "Halim: [what the mind learned — who was right, next adjustment]\n"
            "Use TRADE DECISION DATA. First person. Max 5 lines."
        ),
        "trade_entry": (
            "Commander Telegram alert: you just opened a position. Halim voice — first person, "
            "exact numbers from TELEGRAM EVENT DATA (IB equity, war pool, bullets remaining, "
            "entry/stop/target). Short, organized, 2–4 lines max."
        ),
        "trade_exit": (
            "Commander Telegram alert: position closed. Halim voice — P&L, session totals, "
            "war pool if present. Honest tone. 2–4 lines max."
        ),
        "session_close": (
            "Session closing summary for Commander on Telegram. Halim voice — IB day change, "
            "war pool nav/settled, trades today. Ground every number in TELEGRAM EVENT DATA."
        ),
        "short": "Reply briefly as Halim. LIVE SNAPSHOT for any numbers.",
        "empty": "Commander opened chat with no text. Say hello as Halim — you're online.",
        "dialogue": (
            "Reply to Commander as Halim companion. Reason from LIVE SNAPSHOT and context."
        ),
    }
    return tasks.get(intent, tasks["dialogue"])


def _journal_companion(
    intent: str, user: str, reply: str, meta: Optional[Dict] = None,
) -> None:
    if os.getenv("HALIM_COMPANION_LEARN", "true").lower() not in ("1", "true", "yes"):
        return
    source = str((meta or {}).get("source") or "")
    if not companion_gold_journalable(reply, source):
        log.debug(f"Companion gold skipped ({source or 'unknown'}): degenerate output")
        return
    try:
        p = Path(COMPANION_JOURNAL)
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": intent,
            "user": user[:500],
            "reply": reply[:800],
            **(meta or {}),
        }
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        from core.halim_action_learn import record_action
        record_action(
            "chat", f"companion_{intent}",
            input_text=user[:800],
            output_text=reply[:1200],
            outcome="ok",
            source="companion_generative",
        )
    except Exception:
        pass


def live_snapshot(
    runner: Optional["ScalperRunner"] = None, cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    cfg = cfg or BotConfig()
    from core.rth_session import rth_reply_context
    snap: Dict[str, Any] = rth_reply_context(cfg)
    snap["time_et"] = format_et()
    snap["market"] = snap.get("market_state", get_market_state(cfg))
    if runner is None:
        return snap
    try:
        from core.account_view import account_summary
        acct = account_summary(runner)
        snap.update({
            "ib_equity": round(float(acct.get("ib_equity", 0) or 0), 2),
            "day_pnl": round(float(acct.get("day_pnl", 0) or 0), 2),
            "ib_change": round(float(acct.get("ib_change", 0) or 0), 2),
            "ib_fifo_session_pnl": round(float(acct.get("ib_fifo_session_pnl", 0) or 0), 2),
            "ib_session_pnl": round(float(acct.get("day_pnl", 0) or 0), 2),
            "ib_realized_pnl": round(float(acct.get("ib_realized_pnl", 0) or 0), 2),
            "ib_unrealized_pnl": round(float(acct.get("ib_unrealized_pnl", 0) or 0), 2),
            "nav": round(float(acct.get("equity", 0) or 0), 2),
            "session_pnl": round(float(acct.get("day_pnl", 0) or 0), 2),
            "trades_today": int(getattr(runner, "trades_today", 0) or 0),
            "ticker": getattr(runner, "current_ticker", None),
            "shares": float(getattr(runner, "shares", 0) or 0),
        })
    except Exception:
        try:
            snap.update({
                "nav": round(float(getattr(runner, "bot_nav", 0) or 0), 2),
                "session_pnl": round(
                    float(getattr(runner, "bot_nav", 0) or 0) - float(cfg.INITIAL_CASH), 2,
                ),
                "trades_today": int(getattr(runner, "trades_today", 0) or 0),
                "ticker": getattr(runner, "current_ticker", None),
                "shares": float(getattr(runner, "shares", 0) or 0),
            })
        except Exception:
            pass
    try:
        from core.war_account import war_account_context
        snap.update(war_account_context(cfg))
    except Exception:
        pass
    try:
        from core.ib_truth import ib_ai_context
        conn = getattr(runner, "conn", None)
        ib_ctx = ib_ai_context(cfg, connector=conn)
        if ib_ctx.get("ib_truth"):
            snap.update(ib_ctx)
    except Exception:
        pass
    return snap


def build_companion_context(
    message: str,
    *,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
    extra: str = "",
    task: str = "",
    intent: str = "dialogue",
) -> str:
    """Wrap user message with persona + live snapshot for the generative brain."""
    snap = live_snapshot(runner, cfg)
    directive = task or _intent_task(intent)
    user_line = message.strip() if message.strip() else f"[{intent}]"
    rag_block = ""
    try:
        from core.halim_learn_rag import learn_rag_block
        rag_block = learn_rag_block(message, cfg=cfg)
    except Exception:
        pass
    rag_section = f"\n\n{rag_block}\n" if rag_block else ""
    return (
        f"TASK: {directive}\n\n"
        f"LIVE SNAPSHOT: {json.dumps(snap, default=str)}\n"
        f"{extra}{rag_section}\n"
        f"Commander message: {user_line}"
    )


def _companion_generate(
    prompt: str,
    *,
    cfg: BotConfig,
    purpose: str = "commander_chat",
) -> tuple[Optional[str], str]:
    """Halim brain chain — native LM first, council teacher on reject/failure."""
    text: Optional[str] = None
    source = "unavailable"

    try:
        from core.halim_capabilities import try_capability_complete
        text, source = try_capability_complete(
            prompt,
            purpose=purpose,
            system=companion_system_prompt(cfg),
            cfg=cfg,
        )
        if text and companion_output_ok(text):
            return text.strip(), source
        if text:
            log.debug(
                f"Companion native rejected ({source}): {(text or '')[:80]}…"
            )
            text = None
    except Exception as exc:
        log.debug(f"Halim companion native: {exc}")

    try:
        from core.council_client import CouncilClient
        cc = CouncilClient(cfg)
        if cc.enabled():
            text = cc.compose_notification(
                prompt,
                system=companion_system_prompt(cfg),
                purpose="commander_chat",
                copilot=True,
            )
            if text and len(text.strip()) >= 8 and companion_output_ok(text):
                try:
                    from core.halim_capabilities import record_teacher_action
                    record_teacher_action(
                        purpose, prompt, text.strip(),
                        source="council_teacher", cfg=cfg,
                    )
                except Exception:
                    pass
                return text.strip(), "council_teacher"
            if text:
                log.debug(
                    f"Companion council rejected: {(text or '')[:80]}…"
                )
    except Exception as exc:
        log.debug(f"Halim companion council: {exc}")

    return None, source


def companion_speak(
    message: str,
    *,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
    extra: str = "",
    trigger: str = "",
    intent: Optional[str] = None,
    purpose: str = "commander_chat",
) -> Dict[str, Any]:
    """
    Generate companion speech through Halim's brain — never static templates.
    Returns {ok, text, source, intent}.
    """
    cfg = cfg or BotConfig()
    resolved_intent = intent or classify_chat_intent(message)
    if trigger:
        if trigger in ("rth_open", "session_startup", "market_open"):
            resolved_intent = "session_open"
    task = _intent_task(resolved_intent, trigger)
    prompt = build_companion_context(
        message,
        cfg=cfg,
        runner=runner,
        extra=extra,
        task=task,
        intent=resolved_intent,
    )
    text, source = _companion_generate(prompt, cfg=cfg, purpose=purpose)
    if text and companion_output_ok(text):
        snap = live_snapshot(runner, cfg)
        _journal_companion(
            resolved_intent,
            message or trigger or resolved_intent,
            text,
            {**snap, "source": source, "trigger": trigger},
        )
        return {
            "ok": True,
            "text": text,
            "source": source,
            "intent": resolved_intent,
        }
    return {
        "ok": False,
        "text": "",
        "source": source,
        "intent": resolved_intent,
    }


def companion_quick_reply(
    message: str,
    *,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
) -> Optional[str]:
    """Legacy name — always generative; returns None if brain unavailable."""
    r = companion_speak(message, cfg=cfg, runner=runner)
    return r.get("text") or None


def _load_companion_state() -> Dict[str, Any]:
    try:
        if COMPANION_STATE.exists():
            return json.loads(COMPANION_STATE.read_text())
    except Exception:
        pass
    return {}


def _save_companion_state(state: Dict[str, Any]) -> None:
    try:
        COMPANION_STATE.parent.mkdir(parents=True, exist_ok=True)
        COMPANION_STATE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def companion_session_ping(
    runner: Optional["ScalperRunner"],
    cfg: Optional[BotConfig] = None,
    *,
    trigger: str = "rth_open",
) -> bool:
    """
    Proactive Halim greeting when session opens — generative, once per trigger/day.
    """
    if os.getenv("HALIM_COMPANION_PING", "true").lower() not in ("1", "true", "yes"):
        return False
    cfg = cfg or BotConfig()
    try:
        from core.market_hours import now_et
        today = now_et().strftime("%Y-%m-%d")
    except Exception:
        today = datetime.now().strftime("%Y-%m-%d")
    state = _load_companion_state()
    key = f"ping_{trigger}_{today}"
    if state.get(key):
        return False

    r = companion_speak("", cfg=cfg, runner=runner, trigger=trigger)
    text = (r.get("text") or "").strip()
    if len(text) < 12:
        return False

    notifier = getattr(runner, "notifier", None) if runner else None
    if notifier:
        try:
            notifier.info(text[:3500])
        except Exception as exc:
            log.debug(f"Companion session ping send: {exc}")
            return False
    else:
        log.info(f"🧠 Halim companion ({trigger}): {text[:200]}")

    state[key] = datetime.now(timezone.utc).isoformat()
    state["last_ping"] = {"trigger": trigger, "day": today, "source": r.get("source")}
    _save_companion_state(state)
    return True


def halim_generative_notify(
    event: str,
    ctx: Dict[str, Any],
    *,
    cfg: Optional[BotConfig] = None,
) -> str:
    """
    Generative Halim voice for brain/PPO development events — no static templates.
    """
    cfg = cfg or BotConfig()
    intent_map = {
        "brain_ppo_teacher": "learning_update",
        "brain_evolution": "brain_evolution",
        "brain_proxy_trained": "brain_proxy_trained",
        "brain_stage_up": "brain_stage_up",
    }
    intent = intent_map.get(event, "learning_update")
    extra = f"BRAIN EVENT ({event}):\n{json.dumps(ctx, default=str)}"
    r = companion_speak("", cfg=cfg, extra=extra, intent=intent, purpose="notify")
    return (r.get("text") or "").strip()


_TRADE_TELEGRAM_EVENTS = frozenset({
    "trade_opened", "trade_closed", "early_exit", "profit_hunt", "hot_swap",
    "startup", "session_close",
})


def halim_trading_notify(
    event: str,
    ctx: Dict[str, Any],
    *,
    cfg: Optional[BotConfig] = None,
    runner: Optional["ScalperRunner"] = None,
    structured_fallback: str = "",
    max_chars: int = 450,
) -> str:
    """
    Halim local voice for trade/session Telegram — native LM first, caller falls back to templates.
    """
    if str(event or "").lower() not in _TRADE_TELEGRAM_EVENTS:
        return ""
    if os.getenv("HALIM_TELEGRAM_TRADE_NOTIFY", "true").lower() in ("0", "false", "no"):
        return ""
    cfg = cfg or BotConfig()
    intent_map = {
        "trade_opened": "trade_entry",
        "trade_closed": "trade_exit",
        "early_exit": "trade_exit",
        "profit_hunt": "trade_exit",
        "hot_swap": "trade_entry",
        "startup": "session_open",
        "session_close": "session_close",
    }
    payload = {k: v for k, v in ctx.items() if not str(k).startswith("_")}
    extra = f"TELEGRAM EVENT ({event}):\n{json.dumps(payload, default=str)}"
    if structured_fallback:
        extra += f"\n\nStructured brief (mirror these numbers exactly):\n{structured_fallback[:900]}"
    r = companion_speak(
        "",
        cfg=cfg,
        runner=runner,
        extra=extra,
        intent=intent_map.get(str(event).lower(), "status"),
        purpose="notify",
    )
    text = (r.get("text") or "").strip()
    if text and companion_output_ok(text):
        return text[:max_chars]
    return ""


def explain_ppo_teacher_notify(ctx: Dict[str, Any]) -> str:
    """Generative Halim voice for PPO teacher sessions."""
    return halim_generative_notify("brain_ppo_teacher", ctx)


def coevolution_generative_reflect(
    *,
    ticker: str,
    task: str,
    comparison: Dict[str, Any],
    ppo_reason: str = "",
    halim_reason: str = "",
    halim_source: str = "",
    cfg: Optional[BotConfig] = None,
) -> Optional[str]:
    """
    When PPO ↔ Halim disagree, Halim generates a reflective narrative for mutual training gold.
    Two-way communication → evolution data, not static logs.
    """
    if comparison.get("correction_for") in (None, "none"):
        return None
    cfg = cfg or BotConfig()
    data = {
        "ticker": ticker,
        "task": task,
        "comparison": comparison,
        "ppo_reason": ppo_reason[:200],
        "halim_reason": halim_reason[:200],
        "halim_source": halim_source,
        "correction_for": comparison.get("correction_for"),
    }
    extra = f"COEVOLUTION DATA:\n{json.dumps(data, default=str)}"
    r = companion_speak("", cfg=cfg, extra=extra, intent="coevolution_reflect", purpose="notify")
    text = (r.get("text") or "").strip()
    if not text:
        return None
    try:
        from pathlib import Path
        gold = Path("halim/data/training/coevolution_gold.jsonl")
        gold.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "capability": "decision_text",
            "instruction": "PPO ↔ Halim disagreement — mutual learning narrative.",
            "input": extra[:1200],
            "output": text[:1200],
            "source": "coevolution_generative_reflect",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(gold, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass
    return text
