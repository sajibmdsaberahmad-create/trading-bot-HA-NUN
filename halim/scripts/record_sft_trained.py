#!/usr/bin/env python3
"""Record SFT train.jsonl hashes after Colab — enables core+delta incremental packs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

from halim.dataset import record_trained_from_sft, repo_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Record trained SFT hashes for incremental Colab")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--build-id", default="")
    parser.add_argument("--train-pairs", type=int, default=0)
    args = parser.parse_args()

    root = args.root or repo_root()
    build_id = args.build_id
    if not build_id:
        meta = root / "models/halim_sft_package.meta.json"
        manifest = root / "halim/data/training/sft/manifest.json"
        for path in (meta, manifest, root / "halim/data/training/sft/colab_manifest.json"):
            if path.is_file():
                try:
                    build_id = str(json.loads(path.read_text()).get("build_id", ""))
                    if build_id:
                        break
                except Exception:
                    pass

    train_pairs = args.train_pairs
    if not train_pairs:
        manifest = root / "halim/data/training/sft/manifest.json"
        if manifest.is_file():
            try:
                train_pairs = int(json.loads(manifest.read_text()).get("train_pairs", 0))
            except Exception:
                pass

    result = record_trained_from_sft(root=root, build_id=build_id, train_pairs=train_pairs)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
