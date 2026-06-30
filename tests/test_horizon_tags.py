#!/usr/bin/env python3
"""Horizon orderRef tag tests."""
from __future__ import annotations

from core.horizon_tags import build_order_ref, parse_order_ref


def test_order_ref_roundtrip():
    ref = build_order_ref(
        horizon="swing",
        capital_phase="premarket_full",
        pipeline="uptrend_1h",
    )
    parsed = parse_order_ref(ref)
    assert parsed["horizon"] == "swing"
    assert parsed["capital_phase"] == "premarket_full"
    assert parsed["pipeline"] == "uptrend_1h"


def test_parse_unknown_ref():
    assert parse_order_ref("") == {}
    assert parse_order_ref("legacy") == {}
