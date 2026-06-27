#!/usr/bin/env python3
"""
core/halim_capabilities.py — Phased capability router.

Halim grows little by little: each capability collects action gold in newborn,
then uses owned LM when toddler+ checkpoint exists. Never blocks trading.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig
from core.notify import log


def _ensure_halim_caps():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "halim"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def current_phase(cfg: Optional[BotConfig] = None) -> str:
    try:
        from core.halim_identity import compute_halim_phase
        return compute_halim_phase(cfg)
    except Exception:
        return "newborn"


def resolve_capability(purpose: str) -> str:
    _ensure_halim_caps()
    from halim.capabilities import purpose_to_capability
    return purpose_to_capability(purpose)


def capability_enabled(capability: str, cfg: Optional[BotConfig] = None) -> bool:
    """True if phase allows this capability (action collection always on)."""
    _ensure_halim_caps()
    from halim.capabilities import CAPABILITIES, phase_index

    spec = CAPABILITIES.get(capability)
    if not spec:
        return False
    phase = current_phase(cfg)
    return phase_index(phase) >= phase_index(str(spec.get("phase_min", "newborn")))


def try_capability_complete(
    prompt: str,
    *,
    purpose: str = "reasoning",
    system: Optional[str] = None,
    cfg: Optional[BotConfig] = None,
) -> Tuple[Optional[str], str]:
    """
    Route slow-path text by capability. Returns (text, source).
    Sources: halim_server | collecting | disabled | unavailable
    """
    cap = resolve_capability(purpose)
    if not capability_enabled(cap, cfg):
        return None, "phase_locked"

    from core.halim_inference import try_reasoning_complete

    full_prompt = prompt
    if system:
        full_prompt = f"{system.strip()}\n\n{prompt}"

    text, src = try_reasoning_complete(full_prompt, purpose=purpose, cfg=cfg)
    if text:
        try:
            from core.halim_action_learn import record_action
            record_action(
                cap, purpose,
                input_text=prompt[:2000],
                output_text=text,
                outcome="ok",
                source=src,
                cfg=cfg,
            )
        except Exception:
            pass
        return text, src

    return None, "collecting" if cap else "unavailable"


def record_teacher_action(
    purpose: str,
    prompt: str,
    output: str,
    *,
    source: str = "council_teacher",
    cfg: Optional[BotConfig] = None,
) -> None:
    """Record external teacher output as gold — Halim learns by watching work get done."""
    if not output or len(output.strip()) < 8:
        return
    try:
        from core.halim_action_learn import record_action
        cap = resolve_capability(purpose)
        record_action(
            cap, purpose,
            input_text=prompt[:2000],
            output_text=output[:4000],
            outcome="teacher",
            source=source,
            cfg=cfg,
        )
    except Exception:
        pass


def status_snapshot(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    phase = current_phase(cfg)
    try:
        from core.halim_action_learn import all_capabilities_status
        from core.halim_unlock import unlock_ladder
        caps = all_capabilities_status(phase)
        caps["unlock_ladder"] = unlock_ladder(cfg)
        return caps
    except Exception as exc:
        return {"phase": phase, "error": str(exc)[:80]}


def log_capability_banner(cfg: Optional[BotConfig] = None) -> None:
    snap = status_snapshot(cfg)
    caps = snap.get("capabilities") or {}
    if not caps:
        return
    parts = []
    for cid, info in caps.items():
        if info.get("actions", 0) > 0:
            parts.append(f"{cid}={info.get('level_pct', 0)}%")
    if parts:
        log.info(f"  Halim capabilities (learn-by-action): {', '.join(parts[:6])}")
    else:
        log.info("  Halim capabilities: collecting action gold from every task")
