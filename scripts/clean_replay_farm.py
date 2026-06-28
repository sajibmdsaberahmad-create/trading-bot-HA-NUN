#!/usr/bin/env python3
"""Verify + clean replay intraday farm (single source of truth)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean / verify IB intraday replay farm")
    parser.add_argument("--clean", action="store_true", help="Normalize + remove duplicate daily CSVs")
    parser.add_argument("--purge", action="store_true", help="Delete all replay CSVs (post-training)")
    parser.add_argument("--trim-consumed", action="store_true", help="Remove already-trained bars from CSVs")
    parser.add_argument("--consumption", action="store_true", help="Show fresh vs consumed bar counts")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--retention-days", type=int, default=None)
    args = parser.parse_args()

    from core.replay_data_housekeeping import clean_replay_farm, farm_status, purge_replay_farm

    if args.consumption:
        from core.replay_consumption import farm_unconsumed_stats, load_ledger
        result = farm_unconsumed_stats()
        result["ledger_sessions"] = len(load_ledger())
    elif args.trim_consumed:
        from core.replay_consumption import trim_consumed_replay_bars
        result = trim_consumed_replay_bars(verbose=not args.json)
    elif args.purge:
        result = purge_replay_farm(verbose=not args.json)
    elif args.clean:
        result = clean_replay_farm(retention_days=args.retention_days, verbose=not args.json)
    else:
        result = farm_status()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif not args.clean and not args.trim_consumed and not args.consumption:
        st = result
        if not st.get("ok"):
            print(f"❌ {st.get('error', 'unknown')}")
            return 1
        dupes = st.get("hanoon_duplicate_daily") or []
        print(f"Replay farm: {st['root']}")
        print(f"  Intraday tickers: {st['intraday_tickers']}")
        print(f"  Bars min/max: {st.get('min_bars', 0):,} / {st.get('max_bars', 0):,}")
        try:
            from core.replay_consumption import farm_unconsumed_stats
            unc = farm_unconsumed_stats()
            print(
                f"  Fresh (untrained) bars: {unc.get('unconsumed_bars', 0):,} "
                f"across {unc.get('tickers', 0)} tickers"
            )
        except Exception:
            pass
        if dupes:
            print(f"  ⚠️  Duplicate daily hanoon files ({len(dupes)}): run --clean")
            for d in dupes[:10]:
                print(f"      - hanoon/{d}")
        else:
            print("  ✅ No duplicate daily sources")
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
