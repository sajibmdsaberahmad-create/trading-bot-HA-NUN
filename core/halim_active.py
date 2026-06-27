#!/usr/bin/env python3
"""Ensure Halim stays an active writable model — never inference-only like Ollama."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

from core.notify import log


def _ensure_halim_pkg() -> None:
    root = Path(__file__).resolve().parents[1] / "halim"
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


def enforce_active_halim(*, context: str = "hanoon") -> bool:
    """Block HALIM_INFERENCE_ONLY / HALIM_READ_ONLY — log once at startup."""
    _ensure_halim_pkg()
    from halim.active_model import enforce_active_runtime, runtime_envelope

    ok, msg = enforce_active_runtime(context=context)
    if not ok:
        log.warning(f"🧠 {msg}")
    elif os.getenv("HALIM_LOG_ACTIVE_RUNTIME", "true").lower() in ("1", "true", "yes"):
        env = runtime_envelope()
        log.info(
            f"  Halim runtime: ACTIVE (not read-only) · writable={len(env.get('writable_assets', []))} "
            f"asset classes · server=learn+write when enabled"
        )
    return ok


def active_runtime_status() -> Dict[str, Any]:
    _ensure_halim_pkg()
    from halim.active_model import runtime_envelope
    return runtime_envelope()
