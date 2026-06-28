#!/usr/bin/env python3
"""Train Halim toddler LM — MLX LoRA on capable Mac, or print GPU instructions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

from halim.dataset import prepare_sft_dataset, repo_root  # noqa: E402
from halim.device import detect_profile, profile_spec  # noqa: E402
from halim.scaffold import SCAFFOLD_MLX_4BIT  # noqa: E402

DEFAULT_BASE = SCAFFOLD_MLX_4BIT
DEFAULT_ITERS = 600
DEFAULT_BATCH = 2
DEFAULT_LORA = 8


def _sft_dir(root: Path) -> Path:
    return root / "halim/data/training/sft"


def _ensure_sft(root: Path) -> dict:
    manifest = _sft_dir(root) / "manifest.json"
    if manifest.is_file():
        return json.loads(manifest.read_text())
    result = prepare_sft_dataset(root=root)
    if not result.get("ok"):
        raise SystemExit(json.dumps(result, indent=2))
    return result


def _write_checkpoint_config(
    out_dir: Path,
    *,
    base_model: str,
    sft_pairs: int,
    profile: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "halim_phase": "toddler",
        "model": "M. A. Halim",
        "base_model": base_model,
        "adapter_path": ".",
        "backend": "mlx",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "sft_pairs": sft_pairs,
        "device_profile": profile,
    }
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2))


def _train_mlx(
    root: Path,
    *,
    base_model: str,
    out_name: str,
    iters: int,
    batch_size: int,
    lora_layers: int,
) -> dict:
    sft = _ensure_sft(root)
    data_dir = _sft_dir(root)
    out_dir = root / "halim/data/checkpoints" / out_name
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "mlx_lm.lora",
        "--model", base_model,
        "--train",
        "--data", str(data_dir),
        "--adapter-path", str(out_dir),
        "--iters", str(iters),
        "--batch-size", str(batch_size),
        "--num-layers", str(lora_layers),
        "--learning-rate", "1e-5",
        "--steps-per-report", "25",
        "--steps-per-eval", "50",
        "--val-batches", "5",
    ]

    print("Running:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(root))
    if proc.returncode != 0:
        return {"ok": False, "reason": "mlx_train_failed", "returncode": proc.returncode}

    _write_checkpoint_config(
        out_dir,
        base_model=base_model,
        sft_pairs=int(sft.get("pairs_total", 0)),
        profile=detect_profile(),
    )

    latest = root / "halim/data/checkpoints/latest"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(out_dir.name)

    registry = root / "halim/data/registry.jsonl"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "train_toddler",
        "checkpoint": str(out_dir.relative_to(root)),
        "base_model": base_model,
        "pairs": sft.get("pairs_total"),
    }
    with open(registry, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")

    return {
        "ok": True,
        "checkpoint": str(out_dir),
        "latest_link": str(latest),
        "pairs": sft.get("pairs_total"),
        "next": [
            "export HALIM_LM_BACKEND=mlx",
            "export HALIM_MODEL_PATH=halim/data/checkpoints/latest",
            "./scripts/halim_serve.sh",
        ],
    }


def _print_gpu_instructions(root: Path, profile: str) -> dict:
    sft = _ensure_sft(root)
    data_dir = _sft_dir(root)
    return {
        "ok": False,
        "reason": "train_on_gpu_machine",
        "device_profile": profile,
        "sft_pairs": sft.get("pairs_total"),
        "data_dir": str(data_dir),
        "steps": [
            "1. Run ./scripts/halim_prepare_train.sh on this Mac (done if sft/manifest.json exists)",
            f"2. Copy {data_dir} to a 16GB+ Mac or Colab GPU",
            "3. pip install mlx-lm mlx   # Apple Silicon",
            "   OR pip install transformers peft trl datasets accelerate bitsandbytes  # NVIDIA Colab",
            f"4. python halim/scripts/train_toddler.py --profile gpu_cloud --force",
            "5. Copy halim/data/checkpoints/toddler_v1/ back to this repo",
            "6. ./scripts/halim_register_checkpoint.sh toddler_v1",
            "7. export HALIM_LM_BACKEND=mlx && ./scripts/halim_serve.sh",
        ],
        "colab_mlx_note": "mlx-lm only runs on Apple Silicon. For Colab use HF path (future) or train on a Mac mini 16GB.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Halim toddler checkpoint")
    parser.add_argument("--profile", default=None, help="Override HALIM_DEVICE profile")
    parser.add_argument("--base-model", default=os.getenv("HALIM_BASE_MODEL", DEFAULT_BASE))
    parser.add_argument("--out-name", default="toddler_v1")
    parser.add_argument("--iters", type=int, default=int(os.getenv("HALIM_TRAIN_ITERS", DEFAULT_ITERS)))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--lora-layers", type=int, default=DEFAULT_LORA)
    parser.add_argument("--force", action="store_true", help="Run MLX train even on m2_8gb (may OOM)")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    profile = args.profile or detect_profile()
    prof = profile_spec(profile)

    if args.prepare_only:
        result = prepare_sft_dataset(root=root)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    can_train_local = prof.get("lm_enabled") or profile == "gpu_cloud" or args.force
    if not can_train_local:
        print(json.dumps(_print_gpu_instructions(root, profile), indent=2))
        return 1

    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        out = _print_gpu_instructions(root, profile)
        out["reason"] = "mlx_lm_not_installed"
        out["install"] = "pip install mlx-lm mlx"
        print(json.dumps(out, indent=2))
        return 1

    result = _train_mlx(
        root,
        base_model=args.base_model,
        out_name=args.out_name,
        iters=args.iters,
        batch_size=args.batch_size,
        lora_layers=args.lora_layers,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
