#!/usr/bin/env python3
"""
core/brain_notify.py — Telegram + git journal for owned-brain development.

Notifies commander chats when the brain grows (evolution, proxy, PPO teacher,
stage changes). All Halim/PPO voice is generative — no static briefing templates.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

try:
    from core.halim_identity import HALIM_FULL_NAME
except ImportError:
    HALIM_FULL_NAME = "M. A. Halim"

JOURNAL_PATH = Path("models/owned_brain_journal.jsonl")
DEV_LOG_PATH = Path("docs/BRAIN_DEVELOPMENT_LOG.md")


def _brain_broadcast_enabled(cfg: BotConfig) -> bool:
    if not getattr(cfg, "TELEGRAM_ENABLED", True):
        return False
    if getattr(cfg, "TELEGRAM_BROADCAST_BRAIN", True):
        return bool(getattr(cfg, "TELEGRAM_BROADCAST_OPS", True))
    return bool(getattr(cfg, "TELEGRAM_BROADCAST_LEARNING", False))


def append_brain_journal(
    event: str,
    payload: Dict[str, Any],
    *,
    cfg: Optional[BotConfig] = None,
) -> None:
    """Append-only brain development log (git-synced)."""
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **payload,
    }
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.debug(f"Brain journal append: {exc}")

    summary = payload.get("summary") or payload.get("message") or event
    _append_dev_log_md(event, summary, payload)


def _append_dev_log_md(event: str, summary: str, payload: Dict[str, Any]) -> None:
    """Human-readable development timeline in docs/ (committed to git)."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        stage = payload.get("stage", "")
        stage_line = f" · stage **{stage}**" if stage else ""
        line = f"- `{ts}` **{event}**{stage_line} — {summary}\n"
        if not DEV_LOG_PATH.is_file():
            header = (
                "# Brain development log\n\n"
                "Auto-appended on each owned-brain event. See also "
                "[OWNED_BRAIN.md](OWNED_BRAIN.md).\n\n"
            )
            DEV_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEV_LOG_PATH.write_text(header)
        with open(DEV_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        log.debug(f"Brain dev log md: {exc}")


def _generative_brain_message(cfg: BotConfig, event: str, context: Dict[str, Any]) -> str:
    """Halim generates the notification — PPO/Halim voice, not static fallbacks."""
    try:
        from core.halim_companion import halim_generative_notify
        text = halim_generative_notify(event, context, cfg=cfg)
        if text and len(text) >= 12:
            return text
    except Exception as exc:
        log.debug(f"Generative brain notify ({event}): {exc}")
    return ""


def notify_brain_development(
    cfg: BotConfig,
    event: str,
    context: Dict[str, Any],
    *,
    journal: bool = True,
) -> None:
    """Telegram ops feed + optional journal row — generative Halim voice."""
    if journal:
        append_brain_journal(event, context, cfg=cfg)

    if not _brain_broadcast_enabled(cfg):
        return

    def _run():
        try:
            text = _generative_brain_message(cfg, event, context)
            if text:
                from core.telegram_broadcast import broadcast_precomposed
                broadcast_precomposed(cfg, text)
                return
            from core.telegram_broadcast import broadcast_ops
            ctx = dict(context)
            ctx["raw_event"] = event
            ctx["raw_data"] = json.dumps(context, default=str)[:2000]
            broadcast_ops(cfg, event, ctx, fallback=ctx.get("summary", event))
        except Exception as exc:
            log.debug(f"Brain notify ({event}): {exc}")

    threading.Thread(target=_run, name=f"brain-notify-{event}", daemon=True).start()


def notify_evolution_complete(cfg: BotConfig, result: Dict[str, Any]) -> None:
    steps = result.get("steps") or {}
    export = steps.get("export_dataset") or {}
    proxy = steps.get("train_proxy") or {}
    teacher = steps.get("ppo_teacher") or {}

    proxy_status = "trained" if proxy.get("ok") else proxy.get("reason") or proxy.get("error", "skipped")[:40]
    if isinstance(proxy_status, str) and len(proxy_status) > 40:
        proxy_status = proxy_status[:40]

    try:
        from core.brain_maturity import maturity_snapshot
        snap = maturity_snapshot(cfg)
        stage = snap.get("stage", result.get("stage", "?"))
    except Exception:
        stage = result.get("stage", "?")

    try:
        from core.ppo_teacher_training import trade_stats
        ts = trade_stats(n=200)
        trade_count = ts.get("count", 0)
        win_rate = ts.get("win_rate", 0)
    except Exception:
        trade_count = 0
        win_rate = None

    ctx = {
        "stage": stage,
        "trigger": result.get("trigger", "session"),
        "dataset_pairs": export.get("exported", 0),
        "trade_count": trade_count,
        "win_rate": win_rate,
        "proxy_status": proxy_status,
        "ppo_teacher_status": teacher.get("teacher_source") or teacher.get("reason") or ("ok" if teacher.get("ok") else "skip"),
        "git_push": result.get("git_push", "queued"),
        "summary": f"Evolution {result.get('trigger')} — stage {stage}, dataset {export.get('exported', 0)} pairs",
    }
    notify_brain_development(cfg, "brain_evolution", ctx)

    try:
        state_path = Path("models/owned_brain_state.json")
        if state_path.is_file():
            st = json.loads(state_path.read_text())
            prev = st.get("_notify_stage")
            if prev and prev != stage:
                from core.brain_maturity import _stage_limits
                lim = _stage_limits(stage)
                notify_brain_development(
                    cfg,
                    "brain_stage_up",
                    {
                        "from_stage": prev,
                        "to_stage": stage,
                        "description": lim.get("description", ""),
                        "summary": f"Brain grew: {prev} → {stage}",
                    },
                )
            st["_notify_stage"] = stage
            state_path.write_text(json.dumps(st, indent=2))
    except Exception:
        pass
