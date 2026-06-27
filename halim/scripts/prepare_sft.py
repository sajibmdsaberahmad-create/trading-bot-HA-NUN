#!/usr/bin/env python3
"""Merge Halim gold → SFT train/valid JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

from halim.dataset import count_raw_sources, prepare_sft_dataset, repo_root, sft_pair_count  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Halim toddler SFT dataset")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--min-pairs", type=int, default=int(__import__("os").getenv("HALIM_TODDLER_MIN_PAIRS", "2500")))
    args = parser.parse_args()

    root = args.root or repo_root()
    raw = count_raw_sources(root)
    result = prepare_sft_dataset(root=root, min_pairs=args.min_pairs)
    result["raw_sources"] = raw
    result["deduped_estimate"] = sft_pair_count(root)

    registry = root / "halim/data/registry.jsonl"
    if result.get("ok"):
        registry.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone

        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "prepare_sft",
            "pairs_total": result.get("pairs_total"),
            "by_source": result.get("by_source"),
        }
        with open(registry, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
