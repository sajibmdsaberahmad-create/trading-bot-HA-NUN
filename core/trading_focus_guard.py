#!/usr/bin/env python3
"""Block Halim chat / heavy LM while live or replay trading is active."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import FrozenSet

ROOT = Path(__file__).resolve().parents[1]

CHAT_LIKE_PURPOSES: FrozenSet[str] = frozenset({
    "chat", "commander_chat", "dialogue", "companion", "copilot",
    "image_gen", "code", "coding", "file", "write_file",
})


def chat_allowed_during_trading() -> bool:
    return os.getenv("HALIM_CHAT_DURING_TRADING", "false").lower() in ("1", "true", "yes")


def _pid_alive(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        pid = int(path.read_text().strip())
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def is_replay_session_active() -> bool:
    """True when replay-live is running (not weekend loop parent shell)."""
    log_dir = Path(os.getenv("LOG_DIR", str(ROOT / "logs")))
    return _pid_alive(log_dir / "replay.pid")


def is_trading_session_active() -> bool:
    """True when HANOON live, replay-live, or weekend replay loop is running."""
    log_dir = Path(os.getenv("LOG_DIR", str(ROOT / "logs")))
    for name in ("hanoon.pid", "replay.pid", "weekend_replay.pid"):
        if _pid_alive(log_dir / name):
            return True
    for pattern in (
        "main.py --mode scalper",
        "main.py --mode replay-live",
    ):
        try:
            r = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                timeout=2,
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def is_chat_like_purpose(purpose: str) -> bool:
    return (purpose or "chat").lower() in CHAT_LIKE_PURPOSES


def is_live_scalper_active() -> bool:
    """True when live HANOON scalper is running (not replay-live)."""
    if os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes"):
        return False
    log_dir = Path(os.getenv("LOG_DIR", str(ROOT / "logs")))
    if _pid_alive(log_dir / "hanoon.pid"):
        return True
    try:
        r = subprocess.run(
            ["pgrep", "-f", "main.py --mode scalper"],
            capture_output=True,
            timeout=2,
        )
        return r.returncode == 0
    except Exception:
        return False


def halim_lm_blocked_during_trading(purpose: str = "chat") -> bool:
    if chat_allowed_during_trading():
        return False
    if not is_trading_session_active():
        return False
    # Replay/live: allow LM for training gold (dialogue, copilot, decisions) — block user chat only
    replay_gold = os.getenv("HALIM_REPLAY_GOLD_COLLECT", "true").lower() in ("1", "true", "yes")
    live_gold = os.getenv("HALIM_LIVE_GOLD_COLLECT", "true").lower() in ("1", "true", "yes")
    gold_purposes = frozenset({
        "decision_text", "dialogue", "copilot", "reasoning", "notify",
        "entry_decision", "exit_decision", "commander_chat", "ppo_teacher",
    })
    if (purpose or "chat").lower() in gold_purposes:
        if replay_gold and (
            is_replay_session_active()
            or os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
        ):
            return False
        if live_gold and is_live_scalper_active():
            return False
    return is_chat_like_purpose(purpose)


def trading_focus_message(*, via: str = "telegram") -> str:
    mode = "replay training" if _pid_alive(
        Path(os.getenv("LOG_DIR", str(ROOT / "logs"))) / "replay.pid"
    ) else "live trading"
    if via == "cli":
        return (
            f"Halim chat paused — {mode} has full CPU/RAM focus.\n"
            "Stop trading or use START_HALIM.command when the session ends."
        )
    return (
        f"🎯 {mode.title()} active — Halim chat paused so the algo keeps full focus.\n"
        "Try again after you stop HANOON/replay, or use /status /positions for quick facts."
    )
