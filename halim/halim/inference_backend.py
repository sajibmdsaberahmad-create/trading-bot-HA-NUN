"""Optional Halim LM backends — MLX first, lazy-loaded in halim serve."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_model_cache: Dict[str, Any] = {}


def _load_manifest(checkpoint: Path) -> Dict[str, Any]:
    cfg = checkpoint / "config.json"
    if cfg.is_file():
        try:
            return json.loads(cfg.read_text())
        except Exception:
            pass
    return {}


def _resolve_paths(checkpoint: Path) -> Tuple[str, Optional[str]]:
    manifest = _load_manifest(checkpoint)
    base = manifest.get("base_model") or os.getenv(
        "HALIM_BASE_MODEL", "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
    )
    adapter = manifest.get("adapter_path")
    if adapter:
        ap = checkpoint / adapter if not Path(adapter).is_absolute() else Path(adapter)
        if ap.is_dir() and (ap / "adapters.safetensors").is_file():
            return str(base), str(ap)
    if (checkpoint / "adapters.safetensors").is_file():
        return str(base), str(checkpoint)
    return str(base), None


def mlx_complete(
    prompt: str,
    checkpoint: Path,
    *,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> Tuple[Optional[str], str]:
    """Generate text with mlx-lm. Returns (text, error_reason)."""
    try:
        from mlx_lm import generate, load
    except ImportError:
        return None, "mlx_lm_not_installed"

    key = str(checkpoint.resolve())
    if key not in _model_cache:
        base, adapter = _resolve_paths(checkpoint)
        try:
            if adapter:
                model, tokenizer = load(base, adapter_path=adapter)
            else:
                model, tokenizer = load(base)
            _model_cache[key] = (model, tokenizer)
        except Exception as exc:
            return None, f"load_failed:{exc}"[:120]

    model, tokenizer = _model_cache[key]
    try:
        text = generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            temp=temperature,
            verbose=False,
        )
        return (text or "").strip() or None, "ok"
    except Exception as exc:
        return None, f"generate_failed:{exc}"[:120]
