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


def _merged_model_dir(checkpoint: Path) -> Optional[Path]:
    manifest = _load_manifest(checkpoint)
    rel = manifest.get("merged_path", "merged")
    merged = checkpoint / rel
    if merged.is_dir() and (merged / "config.json").is_file():
        return merged
    if (checkpoint / "config.json").is_file() and (checkpoint / "model.safetensors.index.json").is_file():
        return checkpoint
    if (checkpoint / "config.json").is_file() and list(checkpoint.glob("*.safetensors")):
        return checkpoint
    return None


def hf_complete(
    prompt: str,
    checkpoint: Path,
    *,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> Tuple[Optional[str], str]:
    """Generate with HuggingFace merged model (Colab export). Works on Mac MPS/CPU."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError:
        return None, "transformers_not_installed"

    model_dir = _merged_model_dir(checkpoint)
    if not model_dir:
        return None, "no_merged_model_in_checkpoint"

    key = str(model_dir.resolve())
    if key not in _model_cache:
        try:
            if torch.backends.mps.is_available():
                device = "mps"
                dtype = torch.float16
            elif torch.cuda.is_available():
                device = "cuda"
                dtype = torch.float16
            else:
                device = "cpu"
                dtype = torch.float32
            tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(
                str(model_dir),
                torch_dtype=dtype,
                trust_remote_code=True,
            ).to(device)
            model.eval()
            _model_cache[key] = (model, tokenizer, device)
        except Exception as exc:
            return None, f"load_failed:{exc}"[:120]

    model, tokenizer, device = _model_cache[key]
    try:
        messages = [{"role": "user", "content": prompt}]
        text_in = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        inputs = tokenizer(text_in, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=max(temperature, 0.01),
                do_sample=temperature > 0,
                pad_token_id=tokenizer.eos_token_id,
            )
        new_tokens = out[0][inputs["input_ids"].shape[1]:]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
        return text or None, "ok"
    except Exception as exc:
        return None, f"generate_failed:{exc}"[:120]
