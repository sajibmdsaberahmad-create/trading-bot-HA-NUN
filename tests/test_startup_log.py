#!/usr/bin/env python3
"""Startup log banner helpers."""
from core.config import BotConfig
from core.startup_log import engine_mode_label, horizons_line, log_launch_banner


def test_engine_mode_not_scalper_label():
    assert "scalp" in engine_mode_label("scalper").lower()
    assert "swing" in engine_mode_label("scalper").lower()
    assert "SCALPER" not in engine_mode_label("scalper")


def test_horizons_includes_swing():
    cfg = BotConfig()
    line = horizons_line(cfg)
    assert "scalp" in line.lower()
    assert "swing" in line.lower() or "Horizons:" in line


def test_launch_banner_no_crash():
    cfg = BotConfig()
    log_launch_banner(cfg, "scalper")
