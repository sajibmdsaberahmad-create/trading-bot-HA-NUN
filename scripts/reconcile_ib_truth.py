#!/usr/bin/env python3
"""Reconcile entire bot state against IB Gateway — single source of truth."""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="IB Truth reconcile — war + positions + PnL")
    parser.add_argument("--apply", action="store_true", help="Apply war ledger sync from IB")
    parser.add_argument("--host", default=os.getenv("IB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_PORT", "4002")))
    parser.add_argument("--client-id", type=int, default=int(os.getenv("IB_RECONCILE_CLIENT_ID", "97")))
    args = parser.parse_args()

    try:
        import ib_insync as ibi
    except ImportError:
        print("ERROR: pip install ib_insync")
        return 1

    from core.config import BotConfig
    from core.ib_truth import build_snapshot, format_snapshot_summary, refresh
    from core.war_ib_sync import build_reconcile_report, format_reconcile_report, sync_war_from_ib

    cfg = BotConfig()
    ib = ibi.IB()
    print(f"Connecting IB {args.host}:{args.port} clientId={args.client_id}...")
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as exc:
        print(f"Connection failed: {exc}")
        return 1

    report: dict = {"ok": False}
    try:
        snap = refresh(ib, cfg, force=True)
        print(format_snapshot_summary(snap))
        print()
        report = build_reconcile_report(ib, cfg)
        print(format_reconcile_report(report))
        if args.apply:
            result = sync_war_from_ib(ib, cfg, apply=True)
            print(f"\nApplied war sync: slots={result.get('positions', {}).get('war_slots', 0)}")
    finally:
        ib.disconnect()

    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
