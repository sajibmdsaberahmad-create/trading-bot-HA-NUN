"""Halim active runtime — never inference-only like Ollama or rented APIs."""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

RUNTIME_TYPE = "active"
RUNTIME_VERSION = "1"

# Owned assets Halim may read AND write (guardrailed)
WRITABLE_ASSETS = (
    "ppo_weights",
    "teacher_proxy",
    "scalper_weights",
    "council_dataset",
    "action_log",
    "action_gold",
    "learn_cache",
    "checkpoints",
    "registry",
    "manifest",
    "constitution_audit",
)

# Server routes that mutate / learn — not just POST /complete
ACTIVE_ENDPOINTS = (
    "POST /v1/complete",
    "POST /v1/record",
    "POST /v1/export",
    "POST /v1/evolve",
)

# External sites: read-only. Halim itself is NOT read-only.
EXTERNAL_WEB_POLICY = "read_only_get"


def inference_only_requested() -> bool:
    return os.getenv("HALIM_INFERENCE_ONLY", "false").lower() in ("1", "true", "yes")


def read_only_model_requested() -> bool:
    return os.getenv("HALIM_READ_ONLY", "false").lower() in ("1", "true", "yes")


def enforce_active_runtime(*, context: str = "halim") -> Tuple[bool, str]:
    """
    Reject inference-only / read-only model modes.
    Halim dedicated server = active mind with writable owned weights, not Ollama-style serve.
    """
    if inference_only_requested():
        os.environ["HALIM_INFERENCE_ONLY"] = "false"
        return False, (
            f"{context}: HALIM_INFERENCE_ONLY blocked — Halim is an active model "
            "(learns, writes weights, evolves). External web is read-only; Halim is not."
        )
    if read_only_model_requested():
        os.environ["HALIM_READ_ONLY"] = "false"
        return False, (
            f"{context}: HALIM_READ_ONLY blocked — Halim owns writable checkpoints and datasets."
        )
    return True, "active"


def runtime_envelope() -> Dict[str, Any]:
    """Included in every server response and status snapshot."""
    return {
        "type": RUNTIME_TYPE,
        "version": RUNTIME_VERSION,
        "inference_only": False,
        "read_only_model": False,
        "learn_by_action": True,
        "writable_assets": list(WRITABLE_ASSETS),
        "active_endpoints": list(ACTIVE_ENDPOINTS),
        "external_web": EXTERNAL_WEB_POLICY,
        "not_ollama": True,
        "description": (
            "Active owned mind — generates, decides, learns, writes weights. "
            "Dedicated server adds reasoning; reflex + learning stay live."
        ),
    }
