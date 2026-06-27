#!/usr/bin/env python3
"""
core/halim_chat.py — Halim chat (unlocks slowly: collecting → teacher → native).

Works via Telegram, CLI, and halim serve /v1/chat. Always records gold even when locked.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log


def halim_chat(
    message: str,
    *,
    context: str = "",
    cfg: Optional[BotConfig] = None,
    purpose: str = "chat",
) -> Dict[str, Any]:
    """
    Halim dialogue — phased, never blocks trading.
    Returns {ok, text, mode, source, capability}.
    """
    cfg = cfg or BotConfig()

    try:
        from core.trading_focus_guard import halim_lm_blocked_during_trading, trading_focus_message
        if halim_lm_blocked_during_trading(purpose):
            return {
                "ok": True,
                "text": trading_focus_message(via="cli"),
                "mode": "trading_focus",
                "source": "trading_guard",
                "capability": "chat",
            }
    except Exception:
        pass

    cap = "chat"
    if purpose in ("code", "coding"):
        cap = "code_generate"
    elif purpose in ("file", "write_file"):
        cap = "file_generate"
    elif purpose in ("image_gen",):
        cap = "image_generate"

    try:
        from core.halim_unlock import capability_runtime, locked_message
        runtime = capability_runtime(cap, cfg)
        mode = runtime.get("mode", "collecting")
    except Exception:
        runtime = {"mode": "collecting"}
        mode = "collecting"

    full_prompt = message
    if context:
        full_prompt = f"{context.strip()}\n\nCommander: {message}"

    try:
        from core.halim_learn_rag import learn_rag_block
        rag = learn_rag_block(message, cfg=cfg)
        if rag:
            full_prompt = f"{rag}\n\n{full_prompt}"
    except Exception:
        pass

    # All companion dialogue goes through Halim's generative brain — no static scripts
    use_companion = purpose in (
        "chat", "commander_chat", "dialogue", "companion", "copilot",
    ) or cap == "chat"

    if use_companion:
        try:
            from core.halim_companion import build_companion_context, companion_speak
            companion_ctx = build_companion_context(
                message, cfg=cfg, extra=context, intent="dialogue",
            )
            full_prompt = companion_ctx
        except Exception:
            pass

    # Always journal — learn even when locked
    try:
        from core.halim_action_learn import record_action
        record_action(
            cap, purpose,
            input_text=message[:2000],
            output_text="",
            outcome="attempt",
            source=f"chat_{mode}",
            cfg=cfg,
        )
    except Exception:
        pass

    if mode == "locked":
        msg = locked_message(cap, cfg)
        return {"ok": True, "text": msg, "mode": mode, "source": "halim_locked", "capability": cap}

    # Generative companion path — native LM → council teacher
    if use_companion:
        try:
            from core.halim_companion import companion_speak
            runner = None
            try:
                from core.halim_runtime import get_halim_runtime
                rt = get_halim_runtime()
                if rt:
                    runner = getattr(rt, "_runner", None)
            except Exception:
                pass
            cr = companion_speak(
                message, cfg=cfg, runner=runner, extra=context, purpose=purpose,
            )
            if cr.get("text"):
                return {
                    "ok": True,
                    "text": cr["text"],
                    "mode": mode,
                    "source": cr.get("source", "companion"),
                    "capability": cap,
                }
        except Exception as exc:
            log.debug(f"Halim companion speak: {exc}")
        # Fall through — server/council may still answer (LM cold start, timeout, etc.)

    if mode == "collecting":
        use_teacher = os.getenv("HALIM_CHAT_COLLECTING_USE_TEACHER", "true").lower() in ("1", "true", "yes")
        if use_teacher:
            pass  # fall through to teacher/native attempts below
        else:
            short = (
                f"Halim heard you — {cap.replace('_', ' ')} still growing "
                f"({runtime.get('level_pct', 0)}%). Noted for training."
            )
            if len(message) > 20:
                short += f"\n\nYou said: {message[:300]}"
            return {"ok": True, "text": short, "mode": mode, "source": "halim_collecting", "capability": cap}

    # teacher or native — try Halim server then council
    text = None
    source = "unavailable"

    if mode == "native" or os.getenv("HALIM_CHAT_FORCE_SERVER", "").lower() in ("1", "true"):
        try:
            from core.halim_capabilities import try_capability_complete
            text, source = try_capability_complete(
                full_prompt, purpose=purpose if purpose != "chat" else "commander_chat", cfg=cfg,
            )
        except Exception:
            pass

    if not text and mode in ("teacher", "native", "collecting"):
        try:
            from core.halim_capabilities import try_capability_complete
            text, source = try_capability_complete(
                full_prompt, purpose="copilot", cfg=cfg,
            )
        except Exception:
            pass

    if not text and not getattr(cfg, "HALIM_NATIVE", False) and not os.getenv("HALIM_NATIVE", "").lower() in ("1", "true"):
        try:
            from core.council_client import CouncilClient
            cc = CouncilClient(cfg)
            if cc.enabled():
                text = cc.compose_notification(
                    full_prompt,
                    purpose="commander_chat",
                    copilot=True,
                )
                if text:
                    source = "council_teacher"
                    from core.halim_capabilities import record_teacher_action
                    record_teacher_action(purpose, full_prompt, text, source=source, cfg=cfg)
        except Exception as exc:
            log.debug(f"Halim chat teacher: {exc}")

    if text:
        try:
            from core.halim_action_learn import record_action
            record_action(
                cap, purpose,
                input_text=message[:2000],
                output_text=text[:4000],
                outcome="ok",
                source=source,
                cfg=cfg,
            )
        except Exception:
            pass
        return {"ok": True, "text": text, "mode": mode, "source": source, "capability": cap}

    return {"ok": False, "text": "", "mode": mode, "source": "unavailable", "capability": cap}


def halim_generate(
    kind: str,
    prompt: str,
    *,
    cfg: Optional[BotConfig] = None,
    path_hint: str = "",
) -> Dict[str, Any]:
    """
    Phased generative requests: code | file | image.
    Unlocks by phase + power — not available all at once.
    """
    cfg = cfg or BotConfig()
    cap_map = {
        "code": "code_generate",
        "file": "file_generate",
        "image": "image_generate",
    }
    cap = cap_map.get(kind.lower(), "code_generate")

    try:
        from core.halim_unlock import capability_runtime, locked_message, is_usable as cap_usable
        runtime = capability_runtime(cap, cfg)
    except Exception:
        runtime = {"mode": "locked"}
        cap_usable = lambda *_: False  # noqa: E731

    try:
        from core.halim_action_learn import record_action
        record_action(cap, kind, input_text=prompt[:2000], outcome="attempt", source="generate", cfg=cfg)
    except Exception:
        pass

    if not cap_usable(cap, cfg):
        return {
            "ok": False,
            "reason": "not_unlocked",
            "message": locked_message(cap, cfg),
            "capability": cap,
            "mode": runtime.get("mode"),
        }

    if kind == "code" and runtime.get("mode") in ("teacher", "native"):
        try:
            from core.halim_guardrails import request_action
            ok, reason = request_action("file", "code_patch", {"hint": path_hint[:200]}, cfg=cfg)
            if not ok:
                return {"ok": False, "reason": reason, "capability": cap}
        except Exception:
            pass

    chat = halim_chat(
        f"[{kind} generation request]\n{prompt}",
        cfg=cfg,
        purpose=kind,
    )
    return {
        "ok": chat.get("ok", False),
        "text": chat.get("text"),
        "capability": cap,
        "mode": runtime.get("mode"),
        "source": chat.get("source"),
        "path_hint": path_hint,
    }
