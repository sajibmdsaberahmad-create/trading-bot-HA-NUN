#!/usr/bin/env python3
"""
core/replay_profile.py — Replay gate profile helpers.

Default: REPLAY_MATCH_LIVE=true — same entry quality rails as live paper (M2 profile).
Opt-in volume: REPLAY_GOLD_VOLUME=true — legacy loose gates for bulk gold farming.
"""

from __future__ import annotations

import os


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def is_replay_live() -> bool:
    return _truthy("REPLAY_LIVE", "false")


def replay_gold_volume_mode() -> bool:
    """Legacy loose replay — lower profit prob, relaxed copilot/council."""
    return is_replay_live() and _truthy("REPLAY_GOLD_VOLUME", "false")


def replay_match_live() -> bool:
    """Align replay entry gates with live paper (quality gold over volume)."""
    if not is_replay_live():
        return False
    if replay_gold_volume_mode():
        return False
    return _truthy("REPLAY_MATCH_LIVE", "true")


def replay_relax_council() -> bool:
    if replay_match_live():
        return False
    if not is_replay_live():
        return False
    return _truthy("REPLAY_RELAX_COUNCIL", "true")


def replay_relax_copilot() -> bool:
    if replay_match_live():
        return False
    if not is_replay_live():
        return False
    return _truthy("REPLAY_RELAX_COPILOT", "true")


def replay_profile_label() -> str:
    if not is_replay_live():
        return "live"
    if replay_gold_volume_mode():
        return "replay_gold_volume"
    if replay_match_live():
        return "replay_match_live"
    return "replay_custom"
