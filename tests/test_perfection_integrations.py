"""Perfection sprint integration smokes — deliberation loop, ram-live, hot-path guard."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_run_periodic_cleanup_skipped_when_disabled():
    from core.local_cleanup import run_periodic_cleanup

    with patch.dict(os.environ, {"PERIODIC_CLEANUP_SEC": "0", "AUTO_DISK_CLEANUP": "false"}, clear=False):
        out = run_periodic_cleanup(force=False)
    assert out.get("skipped") is True
    assert "PERIODIC_CLEANUP_SEC" in str(out.get("reason", ""))


def test_halim_complete_returns_failure_reason():
    from core.halim_entry_line import HalimEntryLine
    from core.config import BotConfig

    line = HalimEntryLine(BotConfig())
    root = Path(__file__).resolve().parents[1]
    for p in (str(root / "halim"), str(root)):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import halim.client as hc  # noqa: WPS433
    except ImportError:
        pytest.skip("halim.client not importable")
    with patch.object(hc, "complete", return_value={"ok": False, "reason": "load_failed:test"}):
        text, reason = line._halim_complete("prompt")
    assert text == ""
    assert "load_failed" in reason


def test_hot_path_guard_throttles(caplog):
    import logging
    from core.hot_path_guard import log_hot_path_warning, _last_warn

    _last_warn.clear()
    with caplog.at_level(logging.WARNING):
        log_hot_path_warning("test_ctx", ValueError("boom"), ticker="SOXS")
        log_hot_path_warning("test_ctx", ValueError("boom2"), ticker="SOXS")
    warns = [r for r in caplog.records if "Hot-path test_ctx SOXS" in r.message]
    assert len(warns) == 1


def test_commander_verdict_mixin_has_emit():
    from core.ai_commander_verdict import CommanderVerdictMixin

    assert hasattr(CommanderVerdictMixin, "_emit_spike_verdict")


@pytest.mark.parametrize(
    "raw,expected_enter",
    [
        ('{"enter": true, "confidence": 0.8, "reason": "ok"}', True),
        ("", None),
    ],
)
def test_parse_entry_lm_response_json(raw, expected_enter):
    from core.halim_entry_line import _parse_entry_lm_response

    parsed = _parse_entry_lm_response(raw)
    if expected_enter is None:
        assert not parsed.get("enter")
    else:
        assert parsed.get("enter") is expected_enter
