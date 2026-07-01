#!/usr/bin/env python3
"""Export curated JSON entry_decision gold for Halim v5 SFT (+ optional API teacher)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

from core.halim_json_entry_gold import export_json_entry_gold  # noqa: E402
from halim.dataset import repo_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Halim JSON entry_decision gold (v5 curriculum)",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Use Groq/Gemini teacher for unlabeled council rows (HALIM_JSON_ENTRY_API)",
    )
    parser.add_argument(
        "--api-max",
        type=int,
        default=None,
        help="Max API labels per run (default HALIM_JSON_ENTRY_API_MAX or 120)",
    )
    parser.add_argument("--no-api", action="store_true", help="Force local-only heuristic labels")
    args = parser.parse_args()

    os.environ.setdefault("HALIM_REPO_ROOT", str(repo_root()))
    use_api = None
    if args.api:
        use_api = True
        os.environ["HALIM_JSON_ENTRY_API"] = "true"
    if args.no_api:
        use_api = False

    result = export_json_entry_gold(
        root=repo_root(),
        use_api=use_api,
        api_max=args.api_max,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
