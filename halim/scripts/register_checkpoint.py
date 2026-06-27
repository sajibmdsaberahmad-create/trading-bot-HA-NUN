#!/usr/bin/env python3
"""Register an exported Halim checkpoint as halim/data/checkpoints/latest."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Register Halim checkpoint")
    parser.add_argument("name", help="Checkpoint dir name under halim/data/checkpoints/")
    parser.add_argument("--base-model", default=None, help="Set base_model in config.json if missing")
    parser.add_argument(
        "--backend",
        default=None,
        choices=("mlx", "hf"),
        help="Inference backend (use hf for Colab-trained checkpoints)",
    )
    args = parser.parse_args()

    ckpt_root = ROOT / "halim/data/checkpoints"
    src = ckpt_root / args.name
    if not src.is_dir():
        print(json.dumps({"ok": False, "reason": "checkpoint_not_found", "path": str(src)}))
        return 1

    cfg_path = src / "config.json"
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text())
    else:
        cfg = {}
    if args.base_model:
        cfg["base_model"] = args.base_model
    if args.backend:
        cfg["backend"] = args.backend
    cfg.setdefault("halim_phase", "toddler")
    cfg.setdefault("backend", "mlx")
    cfg.setdefault("model", "M. A. Halim")
    cfg["registered_at"] = datetime.now(timezone.utc).isoformat()
    cfg_path.write_text(json.dumps(cfg, indent=2))

    latest = ckpt_root / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(args.name)

    registry = ROOT / "halim/data/registry.jsonl"
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "register_checkpoint",
        "name": args.name,
        "latest": str(latest),
    }
    with open(registry, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")

    try:
        from core.halim_identity import write_halim_manifest

        write_halim_manifest()
    except Exception:
        pass

    print(json.dumps({"ok": True, "checkpoint": str(src), "latest": str(latest)}, indent=2))
    return 0


if __name__ == "__main__":
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
