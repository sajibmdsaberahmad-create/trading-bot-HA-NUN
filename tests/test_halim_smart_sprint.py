#!/usr/bin/env python3
"""Halim Smart Sprint profile tests."""
from __future__ import annotations

from unittest.mock import patch

from core.config import BotConfig
from core.capital_discipline import allows_micro_fast_entry
from core.halim_smart_sprint import sprint_block_micro_fast, sprint_enabled, sprint_status
from core.smart_stack import strict_profit_prob_enabled


def test_sprint_enabled_default():
    with patch.dict("os.environ", {"HALIM_SMART_SPRINT": "true"}, clear=False):
        assert sprint_enabled() is True


def test_sprint_blocks_micro_fast_for_toddler():
    with patch.dict(
        "os.environ",
        {"HALIM_SMART_SPRINT": "true", "HALIM_SPRINT_BLOCK_MICRO_FAST": "true"},
        clear=False,
    ):
        with patch("core.brain_maturity.compute_stage", return_value="toddler"):
            assert sprint_block_micro_fast(BotConfig()) is True
            assert allows_micro_fast_entry(BotConfig()) is False


def test_sprint_strict_profit_prob_forced():
    with patch.dict("os.environ", {"HALIM_SMART_SPRINT": "true", "SMART_STACK": "true"}, clear=False):
        assert strict_profit_prob_enabled(BotConfig()) is True


def test_sprint_status_keys():
    with patch.dict("os.environ", {"HALIM_SMART_SPRINT": "true"}, clear=False):
        s = sprint_status(BotConfig())
        assert "council_dataset_pairs" in s
        assert "child_target_pairs" in s
