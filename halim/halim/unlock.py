"""Phased unlock ladder — capabilities grow with phase, power, and actions."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from halim.capabilities import CAPABILITIES, PHASE_ORDER, phase_index
from halim.device import detect_profile, profile_spec

# Power score 0–100 from hardware tier (Halim LM + multimodal need RAM/GPU)
PROFILE_POWER: Dict[str, int] = {
    "minimal": 5,
    "m2_8gb": 15,
    "m2_16gb": 45,
    "m2_32gb_plus": 75,
    "gpu_cloud": 100,
}

PHASE_POWER: Dict[str, int] = {
    "newborn": 10,
    "toddler": 30,
    "child": 55,
    "adult": 80,
    "frontier": 100,
}

# Modes: locked → collecting → teacher → native
MODES = ("locked", "collecting", "teacher", "native")


def compute_power_score(
    *,
    phase: str = "newborn",
    profile: str | None = None,
    dataset_pairs: int = 0,
    avg_maturity_pct: float = 0.0,
) -> int:
    """Combined phase + device + dataset + experience → 0–100 power."""
    prof = profile or detect_profile()
    base = max(PHASE_POWER.get(phase, 10), PROFILE_POWER.get(prof, 15))
    ds_boost = min(20, int(dataset_pairs / 250))
    mat_boost = min(15, int(avg_maturity_pct / 7))
    return min(100, base + ds_boost + mat_boost)


def capability_mode(
    cap_id: str,
    *,
    phase: str,
    power: int,
    level_pct: int,
    phase_unlocked: bool,
) -> str:
    """
    locked      — phase not reached (still log attempts as collecting gold)
    collecting  — phase ok, gathering action gold, no generation yet
    teacher     — council/cloud teacher fills in while Halim learns
    native      — Halim owned LM / local tools
    """
    spec = CAPABILITIES.get(cap_id, {})
    power_need = int(spec.get("power_min", 0))
    native_at = int(spec.get("native_at_pct", 70))
    teacher_at = int(spec.get("teacher_at_pct", 25))

    if not phase_unlocked:
        return "locked"
    if level_pct < teacher_at:
        return "collecting"
    if power < power_need:
        return "collecting"
    if level_pct >= native_at and power >= power_need + 10:
        return "native"
    if level_pct >= teacher_at:
        return "teacher"
    return "collecting"


def unlock_status(
    cap_id: str,
    *,
    phase: str,
    power: int,
    level_pct: int,
    phase_unlocked: bool,
) -> Dict[str, Any]:
    spec = CAPABILITIES.get(cap_id, {})
    mode = capability_mode(
        cap_id, phase=phase, power=power, level_pct=level_pct, phase_unlocked=phase_unlocked,
    )
    need_power = int(spec.get("power_min", 0))
    return {
        "capability": cap_id,
        "label": spec.get("label", cap_id),
        "mode": mode,
        "level_pct": level_pct,
        "power": power,
        "power_need": need_power,
        "phase_min": spec.get("phase_min"),
        "phase_unlocked": phase_unlocked,
        "usable": mode in ("teacher", "native"),
        "generates": mode == "native",
    }


def full_unlock_ladder(
    *,
    phase: str,
    capabilities: Dict[str, Dict[str, Any]],
    dataset_pairs: int = 0,
    profile: str | None = None,
) -> Dict[str, Any]:
    levels = [c.get("level_pct", 0) for c in capabilities.values()]
    avg = sum(levels) / max(1, len(levels))
    power = compute_power_score(
        phase=phase,
        profile=profile,
        dataset_pairs=dataset_pairs,
        avg_maturity_pct=avg,
    )
    prof = profile_spec(profile)
    ladder = {
        cid: unlock_status(
            cid,
            phase=phase,
            power=power,
            level_pct=capabilities.get(cid, {}).get("level_pct", 0),
            phase_unlocked=capabilities.get(cid, {}).get("phase_unlocked", False),
        )
        for cid in CAPABILITIES
    }
    next_up = None
    for cid in CAPABILITIES:
        st = ladder[cid]
        if st["mode"] in ("locked", "collecting"):
            next_up = st
            break
    return {
        "phase": phase,
        "power_score": power,
        "device_profile": prof.get("profile"),
        "lm_enabled": prof.get("lm_enabled"),
        "capabilities": ladder,
        "next_unlock": next_up,
    }
