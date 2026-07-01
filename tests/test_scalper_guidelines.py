"""Unit tests for core/scalper_guidelines.py."""
from __future__ import annotations

from core.scalper_guidelines import generate_scalper_guidelines


def test_generate_guidelines_stable_win_rate():
    weights = {
        "momentum": 2.0,
        "volume": 15.0,
        "institutional": 20.0,
        "win_history": [{"result": "win"}, {"result": "loss"}],
    }
    text = generate_scalper_guidelines(weights, [], bot_nav=1100.0, initial_cash=1000.0)
    assert "HANOON SELF-IMPROVEMENT GUIDELINES" in text
    assert "Win rate" in text
