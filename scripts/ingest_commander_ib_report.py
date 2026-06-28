#!/usr/bin/env python3
"""Ingest commander IB report — full Halim consume path (cache + gold + buffer + action gold)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Teach Halim from commander IB report (good + bad + calculated lottery)",
    )
    parser.add_argument("--force", action="store_true", help="Re-write commander gold rows")
    parser.add_argument("--no-buffer", action="store_true", help="Skip experience buffer seed")
    parser.add_argument("--no-export", action="store_true", help="Skip action_gold export")
    parser.add_argument(
        "--section",
        choices=[
            "overview", "calculated_lottery", "human_failures",
            "human_wins", "turnover_fees", "trade_cases", "all",
        ],
        default="all",
        help="Consume one section or all (default: full consume pipeline)",
    )
    args = parser.parse_args()

    if args.section != "all":
        from core.halim_commander_report_learn import fetch_commander_report_section
        result = fetch_commander_report_section(args.section)
    else:
        from core.halim_commander_report_learn import consume_commander_report
        result = consume_commander_report(
            force_gold=args.force,
            seed_buffer=not args.no_buffer,
            export_action_gold=not args.no_export,
        )

    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
