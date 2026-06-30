#!/usr/bin/env python3
"""
core/horizon_tags.py — IB orderRef + log tags for scalp vs swing vs capital phase.

Format: HN|<horizon>|<capital_phase>|<pipeline>
Example: HN|swing|premarket_full|shadow_long
"""
from __future__ import annotations

from typing import Any, Dict, Optional

PREFIX = "HN"
_SEP = "|"


def build_order_ref(
    *,
    horizon: str,
    capital_phase: str = "",
    pipeline: str = "",
    extra: str = "",
) -> str:
    """Compact orderRef for IB open orders / fill reconciliation."""
    parts = [
        PREFIX,
        (horizon or "scalp")[:12],
        (capital_phase or "na")[:20],
        (pipeline or "na")[:24],
    ]
    if extra:
        parts.append(str(extra)[:16])
    ref = _SEP.join(parts)
    return ref[:64]


def parse_order_ref(order_ref: str) -> Dict[str, str]:
    """Decode HN-tagged orderRef; empty dict if unknown."""
    raw = (order_ref or "").strip()
    if not raw.startswith(PREFIX + _SEP):
        return {}
    bits = raw.split(_SEP)
    if len(bits) < 3:
        return {}
    out: Dict[str, str] = {
        "tag": PREFIX,
        "horizon": bits[1] if len(bits) > 1 else "",
        "capital_phase": bits[2] if len(bits) > 2 else "",
        "pipeline": bits[3] if len(bits) > 3 else "",
    }
    if len(bits) > 4:
        out["extra"] = bits[4]
    return out


def tag_learning_row(
    row: Dict[str, Any],
    *,
    horizon: str,
    capital_phase: str = "",
    pipeline: str = "",
) -> Dict[str, Any]:
    """Stamp standard tags on verdict / experience rows."""
    row["horizon"] = horizon
    if capital_phase:
        row["capital_phase"] = capital_phase
    if pipeline:
        row["pipeline"] = pipeline[:80]
    return row
