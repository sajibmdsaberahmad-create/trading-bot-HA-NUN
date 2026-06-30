#!/usr/bin/env python3
"""Tests for swing doctrine maturity scaling."""
from core.config import BotConfig
from core.swing_doctrine import (
    swing_doctrine_enabled,
    swing_maturity_profile,
    swing_maturity_level,
)


def test_swing_doctrine_enabled_default():
    cfg = BotConfig()
    assert swing_doctrine_enabled(cfg)


def test_swing_maturity_profile_shape():
    cfg = BotConfig()
    p = swing_maturity_profile(cfg)
    assert "maturity_level" in p
    assert p["mode"] in ("advisory", "partial", "mandatory")
    assert p["max_ride_days"] >= 1


def test_swing_maturity_level_bounded():
    cfg = BotConfig()
    level = swing_maturity_level(cfg)
    assert 0.0 <= level <= 1.0
