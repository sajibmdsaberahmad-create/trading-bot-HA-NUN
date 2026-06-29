#!/usr/bin/env python3
"""Run commander counterfactual replay + live trip analysis (Lane B, read-only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Commander replay + coach recommendations")
    parser.add_argument("--day", default=None, help="YYYY-MM-DD live trips filter (default: all recent)")
    parser.add_argument("--equity", type=float, default=1000.0, help="Equity for live trip classification")
    parser.add_argument("--coach", action="store_true", help="Also run slow-coach post-session (no live apply if interval not met)")
    args = parser.parse_args()

    from core.config import BotConfig
    from core.commander_replay import run_full_replay

    cfg = BotConfig()
    result = run_full_replay(cfg, day=args.day, equity=args.equity, persist=True)
    print(json.dumps(result, indent=2))

    if args.coach:
        from core.slow_coach import run_post_session_coach
        coach = run_post_session_coach(cfg, runner=None, day=args.day)
        print("\n--- coach ---")
        print(json.dumps(coach, indent=2))

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
