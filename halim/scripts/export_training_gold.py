#!/usr/bin/env python3
"""Export all Halim training gold sources — idempotent, Colab-ready."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

from halim.dataset import count_raw_sources, repo_root  # noqa: E402


def export_all(*, include_learn_cache: bool = True) -> dict:
    import os

    os.environ.setdefault("HALIM_REPO_ROOT", str(repo_root()))

    from core.halim_action_learn import export_action_gold
    from core.halim_ppo_coevolution import export_coevolution_gold
    from core.halim_ppo_dialogue import export_dialogue_gold

    action = export_action_gold(include_learn_cache=include_learn_cache)
    coev = export_coevolution_gold()
    dialogue = export_dialogue_gold()
    raw = count_raw_sources(repo_root())

    return {
        "ok": True,
        "action_gold": action,
        "coevolution_gold": coev,
        "dialogue_gold": dialogue,
        "raw_sources": raw,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Halim training gold for SFT / Colab")
    parser.add_argument("--no-learn-cache", action="store_true")
    args = parser.parse_args()
    result = export_all(include_learn_cache=not args.no_learn_cache)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
