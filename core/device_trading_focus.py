#!/usr/bin/env python3
"""
Device-wide trading focus — during market hours, RAM/CPU go to HANOON + Halim only.

Kills IDE sidecars, stops learn-browse, and (when enabled) blocks off-hours learn
during pre-market / RTH / after-hours on low-memory Macs.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]

# Processes that compete with MLX + scalper on 8GB Macs
IDE_RAM_HOG_PATTERNS: List[str] = [
    "Amazon Q Helper",
    "cloudcode_cli duet",
    "geminicodeassist.*/agent/a2a-server",
    "amazonwebservices.codewhisperer",
]

LEARN_PATTERNS: List[str] = [
    "halim_learn_browse",
]

OTHER_HOG_PATTERNS: List[str] = [
    "ollama serve",
]

_last_enforce = 0.0
_last_remove = 0.0


def device_focus_enabled() -> bool:
    return os.getenv("DEVICE_TRADING_FOCUS", "true").lower() in ("1", "true", "yes")


def learn_off_hours_only() -> bool:
    if os.getenv("HALIM_LEARN_OFF_HOURS_ONLY", "").lower() in ("0", "false", "no"):
        return False
    if os.getenv("HALIM_LEARN_OFF_HOURS_ONLY", "").lower() in ("1", "true", "yes"):
        return True
    try:
        ram_mb = int(subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], text=True, timeout=2,
        ).strip()) // (1024 * 1024)
        return ram_mb <= 12288
    except Exception:
        return True


def is_market_trading_window(cfg=None) -> bool:
    """Pre-market, RTH, or after-hours (weekday, non-holiday)."""
    from core.config import BotConfig
    from core.market_hours import get_market_state

    cfg = cfg or BotConfig()
    return get_market_state(cfg) in ("open", "pre_market", "after_hours")


def market_focus_active(cfg=None) -> bool:
    """True when the device should dedicate RAM to trading."""
    if not device_focus_enabled():
        return False
    try:
        from core.trading_focus_guard import is_trading_session_active
        if is_trading_session_active():
            return True
    except Exception:
        pass
    return is_market_trading_window(cfg)


def learn_blocked_for_device_focus(cfg=None) -> Optional[Dict[str, Any]]:
    if not learn_off_hours_only():
        return None
    if not is_market_trading_window(cfg):
        return None
    from core.market_hours import get_market_state

    state = get_market_state(cfg)
    return {
        "ok": False,
        "reason": "market_hours",
        "message": (
            f"Market session ({state}) — learn paused so the device stays on trading. "
            "Use off-hours (overnight/weekend) or set HALIM_LEARN_OFF_HOURS_ONLY=false."
        ),
    }


def _pkill_patterns(patterns: List[str], *, signal: str = "TERM") -> int:
    killed = 0
    for pattern in patterns:
        try:
            r = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if r.returncode != 0:
                continue
            subprocess.run(
                ["pkill", f"-{signal}", "-f", pattern],
                capture_output=True,
                timeout=3,
            )
            killed += 1
        except Exception:
            pass
    return killed


def remove_ide_hogs_once() -> bool:
    """Run permanent uninstall script (throttled)."""
    global _last_remove
    now = time.time()
    if now - _last_remove < 3600:
        return False
    script = ROOT / "scripts" / "remove_ide_ram_hogs.sh"
    if not script.is_file():
        return False
    try:
        subprocess.run(
            ["bash", str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        _last_remove = now
        return True
    except Exception:
        return False


def enforce_device_trading_focus(cfg=None, *, force: bool = False) -> Dict[str, Any]:
    """
    During market/trading focus: kill RAM hogs and stop learn loop.
    Throttled to once per HALIM_DEVICE_FOCUS_SEC (default 90s).
    """
    global _last_enforce
    if not market_focus_active(cfg):
        return {"ok": True, "skipped": "not_market_focus"}

    interval = float(os.getenv("HALIM_DEVICE_FOCUS_SEC", "90"))
    now = time.time()
    if not force and now - _last_enforce < interval:
        return {"ok": True, "skipped": "throttled"}
    _last_enforce = now

    result: Dict[str, Any] = {"ok": True, "killed": []}

    if os.getenv("HALIM_REMOVE_IDE_HOGS", "true").lower() in ("1", "true", "yes"):
        if remove_ide_hogs_once() or force:
            result["removed_extensions"] = True

    for label, patterns in (
        ("ide", IDE_RAM_HOG_PATTERNS),
        ("learn", LEARN_PATTERNS),
        ("other", OTHER_HOG_PATTERNS),
    ):
        n = _pkill_patterns(patterns, signal="TERM")
        if n:
            result["killed"].append(label)
    if result["killed"]:
        time.sleep(0.5)
        _pkill_patterns(IDE_RAM_HOG_PATTERNS, signal="KILL")

    return result
