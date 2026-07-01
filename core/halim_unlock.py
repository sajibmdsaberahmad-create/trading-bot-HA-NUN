#!/usr/bin/env python3
"""Halim phased unlock — power + phase + maturity gates each capability."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log
from core.training_dataset_paths import council_training_dataset_path


def _ensure_halim_pkg() -> None:
    root = Path(__file__).resolve().parents[1] / "halim"
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _dataset_pairs() -> int:
    p = council_training_dataset_path()
    if not p.is_file():
        return 0
    try:
        with open(p, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


def unlock_ladder(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    _ensure_halim_pkg()
    from core.halim_identity import compute_halim_phase
    from core.halim_action_learn import all_capabilities_status
    from halim.unlock import full_unlock_ladder
    from halim.device import detect_profile

    phase = compute_halim_phase(cfg)
    caps = all_capabilities_status(phase).get("capabilities", {})
    return full_unlock_ladder(
        phase=phase,
        capabilities=caps,
        dataset_pairs=_dataset_pairs(),
        profile=detect_profile(),
    )


def capability_runtime(
    cap_id: str,
    cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    ladder = unlock_ladder(cfg)
    return (ladder.get("capabilities") or {}).get(cap_id, {"mode": "locked"})


def is_usable(cap_id: str, cfg: Optional[BotConfig] = None) -> bool:
    return bool(capability_runtime(cap_id, cfg).get("usable"))


def log_unlock_banner(cfg: Optional[BotConfig] = None) -> None:
    ladder = unlock_ladder(cfg)
    power = ladder.get("power_score", 0)
    nxt = ladder.get("next_unlock") or {}
    if nxt:
        log.info(
            f"  Halim unlock ladder: power={power}/100 · next={nxt.get('label')} "
            f"({nxt.get('mode')}) · need power≥{nxt.get('power_need')}"
        )
    else:
        log.info(f"  Halim unlock ladder: power={power}/100 · frontier capabilities opening")


def locked_message(cap_id: str, cfg: Optional[BotConfig] = None) -> str:
    st = capability_runtime(cap_id, cfg)
    return (
        f"Halim {st.get('label', cap_id)} is {st.get('mode', 'locked')} "
        f"({st.get('level_pct', 0)}% · power {st.get('power', 0)}/"
        f"{st.get('power_need', '?')}). Learning from this attempt — unlocks with phase + power."
    )
