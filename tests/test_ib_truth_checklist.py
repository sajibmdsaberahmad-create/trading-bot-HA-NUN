#!/usr/bin/env python3
"""Tests for IB Truth startup checklist."""
from core.config import BotConfig
from core.ib_truth import IBTruthSnapshot, IBAccountSnapshot
from core.ib_truth_checklist import (
    checklist_enabled,
    evaluate_ib_truth_snapshot,
)


def test_checklist_enabled_when_ib_truth_on():
    cfg = BotConfig()
    assert checklist_enabled(cfg) or not __import__("os").getenv("REQUIRE_IB_FILL_SYNC", "true")


def test_evaluate_stale_snapshot_fails():
    cfg = BotConfig()
    snap = IBTruthSnapshot(refreshed_at=0.0, connected=False)
    r = evaluate_ib_truth_snapshot(snap, cfg)
    assert r.get("ok") is False
    assert r.get("blockers")


def test_evaluate_fresh_snapshot_ok():
    import time
    cfg = BotConfig()
    acct = IBAccountSnapshot(net_liquidation=10000.0, total_cash=5000.0)
    snap = IBTruthSnapshot(
        account=acct,
        refreshed_at=time.time(),
        connected=True,
        server_time="2026-07-01 10:00:00",
    )
    r = evaluate_ib_truth_snapshot(snap, cfg)
    assert r.get("ok") is True
