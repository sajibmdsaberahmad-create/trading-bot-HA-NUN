#!/usr/bin/env python3
"""Audit which IB API services HANOON consumes vs skips."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ib_hub import audit_ib_coverage


def main() -> int:
    report = audit_ib_coverage()
    print(json.dumps(report, indent=2))
    used = report.get("api_endpoints_used", 0)
    total = report.get("api_endpoints_total", 0)
    skipped = report.get("skipped_equity_hull", [])
    print(f"\n✅ IB services active: {used}/{total}")
    if skipped:
        print("⏭ Skipped (equity scalp hull only):")
        for row in skipped:
            print(f"  - {row['call']}: {row.get('reason', '')[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
