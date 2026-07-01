"""Brain maturity — confidence floors and ai-sure ladder."""
from __future__ import annotations

from unittest.mock import patch

from core.brain_maturity import maturity_ai_sure_entry, apply_maturity_to_config
from core.config import BotConfig


def test_maturity_ai_sure_off_for_toddler():
    snap_limits = {"ai_sure_entry": False}
    with patch("core.brain_maturity.maturity_snapshot", return_value={"limits": snap_limits}):
        assert maturity_ai_sure_entry() is False


def test_maturity_ai_sure_on_for_child():
    snap_limits = {"ai_sure_entry": True}
    with patch("core.brain_maturity.maturity_snapshot", return_value={"limits": snap_limits}):
        assert maturity_ai_sure_entry() is True


def test_apply_maturity_raises_confidence_floors():
    cfg = BotConfig()
    cfg.CONFIDENCE_THRESHOLD = 0.55
    snap = {
        "stage": "child",
        "limits": {
            "ppo_micro_steps": 384,
            "copilot_refresh_sec": 180.0,
            "proxy_min_trades": 20,
            "min_confidence": 0.60,
            "min_profit_prob": 0.60,
            "ai_sure_entry": True,
        },
    }
    with patch("core.brain_maturity.maturity_snapshot", return_value=snap):
        with patch("core.brain_maturity.ensure_birth", return_value={}):
            with patch("core.brain_maturity._save_state"):
                with patch("core.owned_brain_evolution.detect_device_profile", return_value={}):
                    with patch("core.owned_brain_evolution.device_limits", return_value={"ppo_micro_steps": 512}):
                        apply_maturity_to_config(cfg)
    assert cfg.CONFIDENCE_THRESHOLD >= 0.60
    assert cfg.MIN_PROFIT_PROBABILITY >= 0.60
