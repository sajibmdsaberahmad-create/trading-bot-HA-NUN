#!/usr/bin/env python3
"""
core/startup_checks.py — Preflight dependency and capability verifier.

Ensures that every required library, GPU backend, and data path is
present and functional BEFORE the trading loop or training pipeline
starts. Fails fast with actionable errors instead of crashing mid-run.
"""
from __future__ import annotations

import os
import sys
import importlib
from typing import List, Tuple

from core.notify import log


def _check_import(module: str, install_hint: str) -> Tuple[bool, str]:
    try:
        importlib.import_module(module)
        return True, f"{module}: OK"
    except Exception as exc:
        return False, f"{module}: MISSING ({install_hint}) — {exc}"


def _check_cuda() -> Tuple[bool, str]:
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            return True, f"CUDA GPU: {name}"
        return False, "CUDA GPU: not available (CPU fallback)"
    except Exception as exc:
        return False, f"CUDA check failed: {exc}"


def _check_mps() -> Tuple[bool, str]:
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return True, "Apple Metal (MPS): available"
        return False, "Apple Metal (MPS): not available (CPU fallback)"
    except Exception as exc:
        return False, f"MPS check failed: {exc}"


def _check_tf_gpu() -> Tuple[bool, str]:
    try:
        import tensorflow as tf
        gpus = tf.config.list_physical_devices("GPU")
        if gpus:
            return True, f"TensorFlow GPU: {len(gpus)} device(s)"
        return True, "TensorFlow CPU: no GPU (OK)"
    except Exception as exc:
        return False, f"TensorFlow GPU check failed: {exc}"


def run_startup_checks(*, require_tensorflow: bool = False) -> None:
    """Preflight imports. TensorFlow only required for legacy fusion/LSTM modes."""
    if require_tensorflow:
        os.environ["KERAS_BACKEND"] = "tensorflow"

    checks: List[Tuple[bool, str]] = []

    checks.append(_check_import("numpy", "pip install numpy"))
    checks.append(_check_import("pandas", "pip install pandas"))
    checks.append(_check_import("torch", "pip install torch"))
    checks.append(_check_import("gymnasium", "pip install gymnasium"))
    checks.append(_check_import("stable_baselines3", "pip install stable-baselines3[extra]"))
    if require_tensorflow:
        checks.append(_check_import("tensorflow", "pip install -r requirements-legacy.txt"))
        checks.append(_check_import("keras", "pip install -r requirements-legacy.txt"))
        checks.append(_check_tf_gpu())
    checks.append(_check_import("sklearn", "pip install scikit-learn"))
    checks.append(_check_import("scipy", "pip install scipy"))
    checks.append(_check_import("dotenv", "pip install python-dotenv"))
    checks.append(_check_cuda())
    checks.append(_check_mps())

    failed = [msg for ok, msg in checks if not ok]

    log.info("=" * 70)
    log.info("  STARTUP CHECKS")
    log.info("=" * 70)
    for _, msg in checks:
        log.info(f"  {msg}")

    if failed:
        log.error("Startup checks failed:")
        for msg in failed:
            log.error(f"  {msg}")
        log.error("Install the missing packages and rerun.")
        sys.exit(1)

    log.info("  All startup checks passed.")
    log.info("=" * 70)