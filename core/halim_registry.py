#!/usr/bin/env python3
"""Halim registry — lineage for checkpoints, evolution, capability milestones."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REGISTRY_PATH = Path("halim/data/registry.jsonl")


def append_registry(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Append one registry row — never raises."""
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **(data or {}),
    }
    try:
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REGISTRY_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def append_evolution_registry(result: Dict[str, Any]) -> None:
    """Log post-session evolution to Halim registry."""
    try:
        from core.halim_identity import compute_halim_phase
        phase = compute_halim_phase()
    except Exception:
        phase = "newborn"

    steps = result.get("steps") or {}
    export = steps.get("export_dataset") or {}
    append_registry(
        "evolution",
        {
            "trigger": result.get("trigger"),
            "phase": phase,
            "stage": result.get("stage"),
            "dataset_pairs": export.get("records") or export.get("count"),
            "device_profile": result.get("device_profile"),
            "steps_ok": {k: v.get("ok", v.get("skipped", False)) for k, v in steps.items() if isinstance(v, dict)},
        },
    )


def append_capability_milestone(capability: str, level: int, phase: str) -> None:
    append_registry(
        "capability_milestone",
        {"capability": capability, "level_pct": level, "phase": phase},
    )
