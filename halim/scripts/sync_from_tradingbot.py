#!/usr/bin/env python3
"""Copy Halim training assets from HANOON tradingbot into this repo."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ASSETS = [
    ("models/council_training_dataset.jsonl", "data/trading/council_training_dataset.jsonl"),
    ("models/teacher_proxy.joblib", "data/students/teacher_proxy.joblib"),
    ("models/scalper_weights.json", "data/students/scalper_weights.json"),
    ("models/halim_identity.json", "HALIM_IDENTITY.json"),
    ("models/halim_manifest.json", "HALIM_MANIFEST.json"),
    ("models/owned_brain_manifest.json", "data/trading/owned_brain_manifest.json"),
    ("models/experience_buffer.jsonl", "data/trading/experience_buffer.jsonl"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Halim assets from tradingbot")
    parser.add_argument("--source", type=Path, default=Path(".."), help="tradingbot root")
    args = parser.parse_args()
    root = args.source.resolve()
    dest_root = Path(__file__).resolve().parents[1]

    copied = []
    for rel_src, rel_dst in ASSETS:
        src = root / rel_src
        dst = dest_root / rel_dst
        if not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel_dst)

    registry = dest_root / "data" / "registry.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "sync_from_tradingbot",
        "source": str(root),
        "files": copied,
    }
    with open(registry, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")

    print(json.dumps({"ok": True, "copied": copied, "count": len(copied)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
