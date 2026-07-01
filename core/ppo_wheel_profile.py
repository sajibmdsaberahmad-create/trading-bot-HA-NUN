#!/usr/bin/env python3
"""PPO wheel profile helpers — lock params Halim developer must not raise mid-profile."""

from __future__ import annotations

import os
from typing import FrozenSet

from core.notify import log

PPO_WHEEL_LOCKED_PARAMS: FrozenSet[str] = frozenset({
    "CONFIDENCE_THRESHOLD",
    "MIN_PROFIT_PROBABILITY",
    "CAPITAL_MIN_CONFIDENCE",
    "CAPITAL_MIN_PROFIT_PROBABILITY",
    "WAR_MIN_PROFIT_PROBABILITY",
    "WAR_PAPER_MIN_PROFIT_PROBABILITY",
    "SCAN_INTERVAL_SECONDS",
    "HALIM_ENTRY_AWAIT_SEC",
    "HALIM_ENTRY_SOFT_VETO",
})


def ppo_wheel_profile_lock() -> bool:
    return os.getenv("PPO_WHEEL_PROFILE_LOCK", "true").lower() in ("1", "true", "yes")


def is_ppo_wheel_locked_param(param: str) -> bool:
    if not ppo_wheel_profile_lock():
        return False
    key = (param or "").strip().upper()
    return key in PPO_WHEEL_LOCKED_PARAMS


def log_ppo_wheel_banner() -> None:
    if not ppo_wheel_profile_lock():
        return
    await_sec = os.getenv("HALIM_ENTRY_AWAIT_SEC", "?")
    soft = os.getenv("HALIM_ENTRY_SOFT_VETO", "?")
    war_adv = os.getenv("WAR_ENTRY_ADVISORY_ONLY", "?")
    conf = os.getenv("CONFIDENCE_THRESHOLD", "?")
    log.info(
        f"  🎡 PPO wheel profile: Halim await={await_sec}s soft_veto={soft} "
        f"war_advisory={war_adv} conf={conf}"
    )
