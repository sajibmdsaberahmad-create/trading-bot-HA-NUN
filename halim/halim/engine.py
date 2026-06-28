"""Halim engine — reflex always local; reasoning optional via server checkpoint."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from halim.device import detect_profile, profile_spec
from halim.protocol import MODEL_NAME, PHASES, REFLEX_COMPONENTS, REASONING_COMPONENTS
from halim.active_model import runtime_envelope

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _root() -> Path:
    env = os.getenv("HALIM_REPO_ROOT", "").strip()
    if env:
        return Path(env)
    if (_REPO_ROOT / "models").is_dir():
        return _REPO_ROOT
    return Path.cwd()


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


def _asset(path: str) -> Dict[str, Any]:
    p = _root() / path
    return {
        "path": path,
        "exists": p.is_file(),
        "size_kb": round(p.stat().st_size / 1024, 1) if p.is_file() else 0,
    }


def read_phase() -> str:
    ident = _root() / "models/halim_identity.json"
    if ident.is_file():
        try:
            return json.loads(ident.read_text()).get("phase", "newborn")
        except Exception:
            pass
    return "newborn"


def checkpoint_path() -> Optional[Path]:
    raw = os.getenv("HALIM_MODEL_PATH", "halim/data/checkpoints/latest")
    p = Path(raw)
    if not p.is_absolute():
        p = _root() / raw
    if (p / "config.json").is_file() or p.with_suffix(".gguf").is_file():
        return p
    # LoRA-only checkpoint (no merge): config + lora_adapter/
    if (p / "lora_adapter" / "adapter_model.safetensors").is_file():
        return p
    gguf = p if str(p).endswith(".gguf") else None
    if gguf and gguf.is_file():
        return gguf
    return None


def reasoning_available() -> bool:
    spec = profile_spec()
    if spec.get("reflex_only") and not os.getenv("HALIM_FORCE_LM"):
        return False
    return checkpoint_path() is not None


def collect_status() -> Dict[str, Any]:
    """Full Halim engine snapshot — safe on any device."""
    root = _root()
    prof = profile_spec()
    ckpt = checkpoint_path()
    ds = _count_jsonl(root / "models/council_training_dataset.jsonl")

    return {
        "model": MODEL_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": read_phase(),
        "device_profile": prof,
        "dataset_pairs": ds,
        "reflex": {k: _asset({
            "ppo": "models/ppo_trader_replay.zip",
            "proxy": "models/teacher_proxy.joblib",
            "scalper_weights": "models/scalper_weights.json",
        }[k]) for k in REFLEX_COMPONENTS},
        "reasoning": {
            "enabled": reasoning_available(),
            "checkpoint": str(ckpt) if ckpt else None,
            "components": list(REASONING_COMPONENTS),
            "backend": os.getenv("HALIM_LM_BACKEND", "none"),  # mlx | llama_cpp | none
        },
        "architecture": {
            "fast_path": "inline_in_hanoon",
            "slow_path": "halim_server_optional",
            "never_block_trading": True,
            "learn_by_action": True,
        },
        "capabilities": _capability_summary(),
        "runtime_mode": runtime_envelope(),
        "unlock_ladder": _unlock_summary(),
    }


def _unlock_summary() -> Dict[str, Any]:
    try:
        root = _root()
        if str(root) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(root))
        from core.halim_unlock import unlock_ladder
        ladder = unlock_ladder()
        return {
            "power_score": ladder.get("power_score"),
            "next_unlock": ladder.get("next_unlock"),
            "modes": {
                k: v.get("mode")
                for k, v in (ladder.get("capabilities") or {}).items()
            },
        }
    except Exception:
        return {}


def _capability_summary() -> Dict[str, Any]:
    """Action counts per capability — safe without importing core."""
    log_path = _root() / "halim/data/actions/action_log.jsonl"
    counts: Dict[str, int] = {}
    if log_path.is_file():
        try:
            with open(log_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json as _json
                        cap = _json.loads(line).get("capability", "?")
                        counts[cap] = counts.get(cap, 0) + 1
                    except Exception:
                        continue
        except Exception:
            pass
    gold_path = _root() / "halim/data/training/action_gold.jsonl"
    gold_n = _count_jsonl(gold_path)
    return {"action_counts": counts, "action_gold_pairs": gold_n}


def complete_reasoning(prompt: str, purpose: str = "reasoning") -> Dict[str, Any]:
    """
    Slow path — Halim LM inference. Returns structured result; never raises.
    """
    max_tokens = int(os.getenv("HALIM_MAX_TOKENS", "512"))
    temperature = float(os.getenv("HALIM_TEMPERATURE", "0.7"))
    if purpose == "entry_decision":
        max_tokens = int(os.getenv("HALIM_ENTRY_MAX_TOKENS", "72"))
        temperature = float(os.getenv("HALIM_ENTRY_TEMPERATURE", "0.12"))
    if not reasoning_available():
        return {
            "ok": False,
            "reason": "no_checkpoint",
            "message": "Halim LM not trained yet — reflex students active; collect dataset",
            "phase": read_phase(),
            "dataset_pairs": collect_status().get("dataset_pairs", 0),
        }

    backend = os.getenv("HALIM_LM_BACKEND", "").lower()
    ckpt = checkpoint_path()
    if backend == "hf" and ckpt:
        try:
            from halim.inference_backend import hf_complete

            text, err = hf_complete(prompt, ckpt, max_tokens=max_tokens, temperature=temperature)
            if text:
                return {
                    "ok": True,
                    "text": text,
                    "source": "halim_lm",
                    "backend": "hf",
                    "purpose": purpose,
                }
            return {
                "ok": False,
                "reason": err,
                "message": "HF inference failed — pip install torch transformers",
                "backend": "hf",
            }
        except Exception as exc:
            return {
                "ok": False,
                "reason": "hf_error",
                "message": str(exc)[:200],
                "backend": "hf",
            }

    if backend == "mlx" and ckpt:
        try:
            from halim.inference_backend import mlx_complete

            text, err = mlx_complete(prompt, ckpt, max_tokens=max_tokens, temperature=temperature)
            if text:
                return {
                    "ok": True,
                    "text": text,
                    "source": "halim_lm",
                    "backend": "mlx",
                    "purpose": purpose,
                }
            return {
                "ok": False,
                "reason": err,
                "message": "MLX inference failed — check mlx-lm install and checkpoint",
                "backend": "mlx",
            }
        except Exception as exc:
            return {
                "ok": False,
                "reason": "mlx_error",
                "message": str(exc)[:200],
                "backend": "mlx",
            }

    if backend in ("mlx", "llama_cpp"):
        return {
            "ok": False,
            "reason": "backend_not_wired",
            "message": f"Checkpoint found; set HALIM_LM_BACKEND=mlx and install mlx-lm",
            "backend": backend,
        }

    return {
        "ok": False,
        "reason": "backend_not_configured",
        "message": "Set HALIM_LM_BACKEND=mlx when checkpoint is ready",
    }
