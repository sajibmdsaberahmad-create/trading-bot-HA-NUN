"""AI-sure entry — no blind spike fast paths; dynamic Halim+PPO+quality alignment."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.config import BotConfig
from core.entry_quality import apply_ai_sure_veto
from core.smart_stack import (
    ai_sure_entry_enabled,
    build_halim_local_entry,
    dynamic_entry_surety,
    fast_entry_pipeline_blocked,
)


def test_ai_sure_enabled_with_smart_stack():
    cfg = BotConfig()
    with patch("core.smart_stack.smart_stack_enabled", return_value=True):
        assert ai_sure_entry_enabled(cfg)


def test_fast_pipeline_blocked():
    assert fast_entry_pipeline_blocked("ppo:micro_fast")
    assert fast_entry_pipeline_blocked("halim:quality_flash")
    assert not fast_entry_pipeline_blocked("halim:ai_sure_lead")


def test_build_halim_ai_sure_requires_halim_enter():
    cfg = BotConfig()
    quality = {"profit_probability": 0.91, "enter_ok": True, "reason": "ok"}
    halim = {
        "status": "fresh",
        "parsed": {"enter": False, "confidence": 0.50, "reason": "wait"},
    }
    with patch("core.smart_stack.smart_stack_enabled", return_value=True):
        with patch("core.smart_stack.ai_sure_entry_enabled", return_value=True):
            out = build_halim_local_entry(
                cfg,
                halim_live=halim,
                quality=quality,
                ppo_action=1,
                ppo_conf=0.58,
                ppo_reason="",
                min_conf=0.55,
                scan_score=90,
                spike_ratio=1.5,
            )
    assert not out["enter"]
    assert "ai_sure" in out["pipeline"]


def test_build_halim_ai_sure_lead_when_aligned():
    cfg = BotConfig()
    quality = {"profit_probability": 0.91, "enter_ok": True, "reason": "ok"}
    halim = {
        "status": "fresh",
        "parsed": {"enter": True, "confidence": 0.72, "reason": "momentum"},
    }
    with patch("core.smart_stack.smart_stack_enabled", return_value=True):
        with patch("core.smart_stack.ai_sure_entry_enabled", return_value=True):
            with patch("core.smart_stack.dynamic_entry_surety", return_value={
                "min_conf": 0.65, "min_prob": 0.62, "min_halim_conf": 0.58,
            }):
                out = build_halim_local_entry(
                    cfg,
                    halim_live=halim,
                    quality=quality,
                    ppo_action=1,
                    ppo_conf=0.68,
                    ppo_reason="",
                    min_conf=0.55,
                    scan_score=90,
                    spike_ratio=1.5,
                )
    assert out["enter"]
    assert out["pipeline"] == "halim:ai_sure_lead"


def test_apply_ai_sure_veto_blocks_micro_fast():
    cfg = BotConfig()
    quality = {"profit_probability": 0.91, "enter_ok": True}
    decision = {"enter": True, "pipeline": "ppo:micro_fast", "confidence": 0.7}
    with patch("core.smart_stack.ai_sure_entry_enabled", return_value=True):
        out = apply_ai_sure_veto(
            cfg, decision, quality,
            ppo_action=1, ppo_conf=0.7, scan_score=90, spike_ratio=1.5,
        )
    assert not out["enter"]
    assert "ai_sure" in out["pipeline"]


def test_dynamic_entry_surety_includes_war_bump():
    cfg = BotConfig()
    with patch("core.smart_stack.war_posture_adjustments", return_value={
        "conf_bump": 0.04, "prob_bump": 0.05, "note": "war_trips=3",
    }):
        sure = dynamic_entry_surety(cfg, scan_score=80, spike_ratio=1.3)
    assert sure["min_prob"] >= 0.62
    assert sure["min_conf"] >= 0.55
