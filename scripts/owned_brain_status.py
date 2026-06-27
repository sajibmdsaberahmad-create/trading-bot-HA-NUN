#!/usr/bin/env python3
"""Show owned-model evolution status and export council training dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import BotConfig
from core.owned_brain_evolution import (
    device_limits,
    evolution_status,
    export_council_dataset,
    log_evolution_summary,
    run_post_session_evolution,
)

try:
    from core.brain_maturity import log_maturity_banner, maturity_snapshot
except ImportError:
    maturity_snapshot = None  # type: ignore
    log_maturity_banner = None  # type: ignore


def main() -> int:
    parser = argparse.ArgumentParser(description="Owned brain evolution dashboard")
    parser.add_argument("--export", action="store_true", help="Export council_training_dataset.jsonl")
    parser.add_argument("--evolve", action="store_true", help="Run full post-session evolution")
    parser.add_argument("--json", action="store_true", help="Print JSON status")
    parser.add_argument("--no-push", action="store_true", help="Skip git push during --evolve")
    args = parser.parse_args()

    cfg = BotConfig()
    if args.evolve:
        r = run_post_session_evolution(
            cfg, trigger="cli", push_git=not args.no_push,
        )
        print(json.dumps(r, indent=2))
        return 0
    if args.export:
        r = export_council_dataset()
        print(json.dumps(r, indent=2))
    if args.json:
        payload: dict = {"status": evolution_status(cfg), "device": device_limits()}
        if maturity_snapshot:
            payload["maturity"] = maturity_snapshot(cfg)
        print(json.dumps(payload, indent=2))
    elif not args.export:
        if log_maturity_banner:
            log_maturity_banner(cfg)
        log_evolution_summary(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
