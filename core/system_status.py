#!/usr/bin/env python3
"""
core/system_status.py — Full HANOON ops dashboard for Telegram / commander.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.market_hours import format_et, get_market_state

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner


def _model_info(path: str = "ppo_trader.zip") -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"path": path, "exists": False}
    st = p.stat()
    return {
        "path": path,
        "exists": True,
        "size_mb": round(st.st_size / (1024 * 1024), 2),
        "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _git_head() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "?"


def _git_dirty() -> bool:
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return bool(r.stdout.strip()) if r.returncode == 0 else False
    except Exception:
        return False


def collect_system_status(cfg: BotConfig, runner: Optional["ScalperRunner"] = None) -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "time_et": format_et(),
        "market_state": get_market_state(cfg),
        "paper": bool(getattr(cfg, "PAPER_TRADING", False)),
        "model": _model_info(),
        "git_head": _git_head(),
        "git_dirty": _git_dirty(),
    }

    try:
        from core.git_sync import get_stats
        status["git_sync"] = get_stats()
    except Exception:
        status["git_sync"] = {}

    try:
        from core.async_utils import get_background_worker
        status["background_worker"] = dict(getattr(get_background_worker(), "_stats", {}))
    except Exception:
        status["background_worker"] = {}

    try:
        from core.telegram_auth import list_verified
        status["telegram_verified_chats"] = len(list_verified(cfg))
    except Exception:
        status["telegram_verified_chats"] = 0

    status["council_model"] = getattr(cfg, "GROQ_MODEL", "") or getattr(cfg, "GEMINI_MODEL", "")
    status["ollama_model"] = status["council_model"]  # legacy key
    status["vision_model"] = getattr(cfg, "OLLAMA_VISION_MODEL", "llava")

    if runner:
        status["ib_equity"] = round(getattr(runner, "account_equity", 0), 2)
        try:
            from core.ib_truth import get_snapshot, ib_truth_enabled
            if ib_truth_enabled(cfg):
                from core.notify_ib_context import ib_telegram_account
                ib_acct = ib_telegram_account(runner, cfg)
                status["ib_equity"] = round(float(ib_acct.get("nav", 0) or 0), 2)
            snap = get_snapshot()
            if snap.refreshed_at > 0:
                status["ib_truth"] = True
                status["ib_fifo_session_pnl"] = snap.session_pnl_fifo
                status["ib_unrealized_pnl"] = snap.account.unrealized_pnl
                status["trades_today"] = len(snap.round_trips)
        except Exception:
            status["trades_today"] = 0
        if "trades_today" not in status:
            status["trades_today"] = 0
        status["open_slots"] = len(getattr(runner, "_position_slots", {}) or {})

        if getattr(runner, "consciousness", None):
            try:
                ident = runner.consciousness.get_identity()
                status["mood"] = ident.get("mood", "")
                status["mood_message"] = (ident.get("mood_message", "") or "")[:120]
            except Exception:
                pass

        if getattr(runner, "pilot", None):
            try:
                status["pilot"] = runner.pilot.get_veteran_status()
            except Exception:
                pass

        try:
            from core.position_intel import collect_risk
            risk = collect_risk(runner)
            status["risk_summary"] = {
                "daily_pnl": risk.get("daily_pnl"),
                "unrealized": risk.get("total_unrealized_pnl"),
                "deployed_pct": risk.get("deployed_pct"),
                "halted": risk.get("halted"),
            }
        except Exception:
            pass

    for rel in ("models/scalper_weights.json", "models/training_history.json", "models/pilot_experience.json"):
        p = Path(rel)
        if p.exists():
            status.setdefault("artifacts", {})[rel] = datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")

    return status


def format_system_report(status: Dict[str, Any]) -> str:
    gs = status.get("git_sync") or {}
    bw = status.get("background_worker") or {}
    model = status.get("model") or {}
    pilot = status.get("pilot") or {}
    risk = status.get("risk_summary") or {}

    last_push = gs.get("last_push_age_sec")
    push_age = f"{int(last_push // 60)}m ago" if last_push is not None else "never"

    lines = [
        "🖥 HANOON SYSTEM",
        f"{status.get('time_et', '')} · Market {str(status.get('market_state', '?')).upper()} · "
        f"{'PAPER' if status.get('paper') else 'LIVE'}",
        "",
        "💰 Account (IB)",
        f"NetLiq ${status.get('ib_equity', 0):,.2f} · "
        f"Session P&L ${status.get('ib_fifo_session_pnl', 0):+,.2f} · "
        f"{status.get('open_slots', 0)} bot slot(s) · "
        f"{status.get('trades_today', 0)} IB round-trips",
    ]
    if risk:
        lines.append(
            f"Session P&L ${risk.get('daily_pnl', 0):+,.2f} (IB) · "
            f"Unrealized ${risk.get('unrealized', 0):+,.2f} · "
            f"Deployed {risk.get('deployed_pct', 0):.1f}%"
        )
        if risk.get("halted"):
            lines.append("⛔ Risk circuit HALTED")

    try:
        from core.lottery_bank import format_status, lottery_bank_enabled
        from core.config import BotConfig
        if lottery_bank_enabled(BotConfig()):
            lb = format_status().replace("\n", "\n")
            for lb_line in lb.split("\n"):
                lines.append(lb_line)
    except Exception:
        pass

    lines.extend([
        "",
        "🧠 AI / Model",
        f"Mood: {status.get('mood', '—')}",
    ])
    if status.get("mood_message"):
        lines.append(status["mood_message"][:100])
    if pilot:
        lines.append(f"Pilot: {pilot.get('level', '?')} · XP {pilot.get('xp', 0)}")

    if model.get("exists"):
        lines.append(f"PPO {model.get('path')}: {model.get('size_mb', 0)} MB · {model.get('modified', '?')}")
    else:
        lines.append(f"PPO: {model.get('path', 'ppo_trader.zip')} not found")

    lines.extend([
        "",
        "📦 Git / Sync",
        f"HEAD {status.get('git_head', '?')}{' *dirty*' if status.get('git_dirty') else ''}",
        f"Pushes {gs.get('total_pushes', 0)} (failed {gs.get('failed_pushes', 0)}) · last {push_age}",
        f"Queue {gs.get('pending_queue', 0)} · worker commits {bw.get('git_commits', 0)}",
        "",
        "📡 Telegram",
        f"Verified commanders: {status.get('telegram_verified_chats', 0)}",
        f"Ollama: {status.get('ollama_model') or 'default'} · Vision: {status.get('vision_model', 'llava')}",
    ])

    arts = status.get("artifacts") or {}
    if arts:
        lines.append("")
        lines.append("📁 Artifacts")
        for name, mtime in list(arts.items())[:4]:
            lines.append(f"  {Path(name).name}: {mtime}")

    return "\n".join(lines)
