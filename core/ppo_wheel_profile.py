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


def _env_on(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def ppo_only_execution_enabled(cfg=None) -> bool:
    """Live entries follow PPO BUY after green — no quality_flash / Halim / council bypass."""
    if not ppo_wheel_profile_lock() and not _env_on("PPO_ONLY_EXECUTION", "false"):
        return _env_on("PPO_ONLY_EXECUTION", "false")
    return _env_on("PPO_ONLY_EXECUTION", "true")


def ppo_lead_exits_enabled(cfg=None) -> bool:
    """PPO SELL executes after green survival; council cannot override hold."""
    if not ppo_wheel_profile_lock() and not _env_on("PPO_LEAD_EXITS", "false"):
        return _env_on("PPO_LEAD_EXITS", "false")
    return _env_on("PPO_LEAD_EXITS", "true")


def council_execution_advisory_only(cfg=None) -> bool:
    """Groq/Halim council labels for learn — never submit buy/sell alone."""
    if not ppo_wheel_profile_lock() and not _env_on("COUNCIL_EXECUTION_ADVISORY_ONLY", "false"):
        return _env_on("COUNCIL_EXECUTION_ADVISORY_ONLY", "false")
    return _env_on("COUNCIL_EXECUTION_ADVISORY_ONLY", "true")


def log_ppo_wheel_banner() -> None:
    if not ppo_wheel_profile_lock():
        return
    await_sec = os.getenv("HALIM_ENTRY_AWAIT_SEC", "?")
    soft = os.getenv("HALIM_ENTRY_SOFT_VETO", "?")
    war_adv = os.getenv("WAR_ENTRY_ADVISORY_ONLY", "?")
    conf = os.getenv("CONFIDENCE_THRESHOLD", "?")
    ppo_in = "on" if ppo_only_execution_enabled() else "off"
    ppo_out = "on" if ppo_lead_exits_enabled() else "off"
    council = "advisory" if council_execution_advisory_only() else "blocking"
    log.info(
        f"  🎡 PPO wheel profile: Halim await={await_sec}s soft_veto={soft} "
        f"war_advisory={war_adv} conf={conf} "
        f"ppo_entry={ppo_in} ppo_exit={ppo_out} council={council}"
    )
