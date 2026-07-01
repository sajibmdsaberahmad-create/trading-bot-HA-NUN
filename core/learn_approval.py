#!/usr/bin/env python3
"""
core/learn_approval.py — Firewall: PPO reward training only from teacher-approved rows.

All experiences still append to the buffer for gold / audit. When
LEARN_APPROVAL_REQUIRED=true, online/off-hours PPO steps skip unapproved rows.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from core.config import BotConfig

_AUTO_APPROVED_SOURCES = frozenset({
    "commander_ib_gold",
    "teacher_ppo",
    "halim_ppo_coevolution",
    "halim_ppo_outcome",
    "deferred_council",
})


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def learn_approval_required(cfg: Optional[BotConfig] = None) -> bool:
    return _env_bool("LEARN_APPROVAL_REQUIRED", "false")


def stamp_learn_approval(
    record: Dict[str, Any],
    *,
    approved: bool,
    by: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    out = dict(record)
    out["learn_approved"] = bool(approved)
    if by:
        out["learn_approved_by"] = str(by)[:64]
    if reason:
        out["learn_approval_reason"] = str(reason)[:200]
    return out


def infer_learn_approval(record: Dict[str, Any]) -> bool:
    """Heuristic auto-approval for teacher-labelled rows (no env gate)."""
    if record.get("learn_approved") is True:
        return True
    if record.get("learn_approved") is False:
        return False
    src = str(record.get("source", ""))
    if src in _AUTO_APPROVED_SOURCES:
        return True
    if src == "ppo_entry_eval" and record.get("ollama_attached"):
        return bool(
            record.get("ollama_agrees_with_execute")
            or record.get("halim_agrees_with_execute")
        )
    if src in ("live_trade", "live_entry", "replay_live") and record.get("pnl_usd") is not None:
        if record.get("halim_outcome") or record.get("coevolution_stamped"):
            return True
        if record.get("teacher_action") is not None:
            return True
    if record.get("teacher_action") is not None or record.get("teacher_reward") is not None:
        return True
    return False


def eligible_for_ppo_training(
    record: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
) -> bool:
    if not learn_approval_required(cfg):
        return True
    return infer_learn_approval(record)


def filter_for_ppo_training(
    records: Sequence[Dict[str, Any]],
    cfg: Optional[BotConfig] = None,
) -> List[Dict[str, Any]]:
    if not learn_approval_required(cfg):
        return list(records)
    return [r for r in records if eligible_for_ppo_training(r, cfg)]
