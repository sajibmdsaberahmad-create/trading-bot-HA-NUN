#!/usr/bin/env python3
"""Tests for Halim JSON entry gold export."""

from __future__ import annotations

import json

from core.halim_json_entry_gold import (
    build_entry_user_prompt,
    format_entry_json_assistant,
    parse_entry_json_assistant,
)


def test_format_and_parse_roundtrip():
    text = format_entry_json_assistant(enter=True, confidence=0.72, reason="clean momentum")
    assert text is not None
    parsed = parse_entry_json_assistant(text)
    assert parsed is not None
    assert parsed["enter"] is True
    assert parsed["confidence"] == 0.72


def test_rejects_ramble():
    bad = 'agree=False on enter. reprice=False on enter.'
    assert parse_entry_json_assistant(bad) is None


def test_user_prompt_has_json_contract():
    user = build_entry_user_prompt(ticker="SOXS", spike=2.5, scan=88, ppo_buy=False, ppo_conf=0.54)
    assert "ENTRY SOXS" in user
    assert '"enter":true' in user
    assert "Reply ONE json object only" in user


def test_assistant_is_compact_json():
    text = format_entry_json_assistant(enter=False, confidence=55, reason="chop fakeout skip")
    obj = json.loads(text)
    assert obj["enter"] is False
    assert obj["confidence"] == 0.55
