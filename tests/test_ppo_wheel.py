#!/usr/bin/env python3
"""Tests for PPO wheel: war advisory, deploy tiers, learn approval."""

import os
import unittest
from unittest.mock import patch

from core.config import BotConfig
from core.learn_approval import (
    eligible_for_ppo_training,
    filter_for_ppo_training,
    learn_approval_required,
    stamp_learn_approval,
)
from core.ppo_deploy_tiers import (
    apply_deploy_tier_to_decision,
    classify_deploy_tier,
    ppo_deploy_tiers_enabled,
)
from core.war_entry_gates import (
    apply_war_entry_veto,
    war_entry_advisory_context,
    war_entry_advisory_only,
    war_entry_veto,
)


class TestWarEntryAdvisory(unittest.TestCase):
    def setUp(self):
        self.cfg = BotConfig()
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    @patch.dict(os.environ, {"WAR_ENTRY_ADVISORY_ONLY": "true", "WAR_ACCOUNT_ENABLED": "true"})
    def test_advisory_never_vetoes(self):
        self.assertTrue(war_entry_advisory_only(self.cfg))
        reason = war_entry_veto(
            self.cfg,
            pipeline="scanner_timeout",
            confidence=0.3,
            ppo_action=0,
        )
        self.assertIsNone(reason)

    @patch.dict(os.environ, {"WAR_ENTRY_ADVISORY_ONLY": "true", "WAR_ACCOUNT_ENABLED": "true"})
    def test_apply_war_keeps_enter(self):
        dec = {"enter": True, "confidence": 0.4, "pipeline": "scanner_timeout"}
        out = apply_war_entry_veto(self.cfg, dec, ppo_action=0, ppo_conf=0.4)
        self.assertTrue(out.get("enter"))
        self.assertIn("war_advisory", out)

    @patch.dict(os.environ, {"WAR_ENTRY_ADVISORY_ONLY": "false", "WAR_ACCOUNT_ENABLED": "true",
                              "WAR_BLOCK_SCANNER_TIMEOUT": "true"})
    def test_legacy_can_veto(self):
        with patch("core.war_entry_gates.war_gates_active", return_value=True):
            reason = war_entry_veto(
                self.cfg,
                pipeline="scanner_timeout",
                confidence=0.3,
                ppo_action=0,
            )
            self.assertIsNotNone(reason)

    @patch.dict(os.environ, {"WAR_ENTRY_ADVISORY_ONLY": "true"})
    def test_advisory_context_reports_would_veto(self):
        with patch("core.war_entry_gates.war_gates_active", return_value=True), patch(
            "core.war_entry_gates.war_blocks_scanner_timeout", return_value=True
        ):
            ctx = war_entry_advisory_context(
                self.cfg, pipeline="scanner_timeout", confidence=0.3
            )
            self.assertTrue(ctx.get("war_would_veto"))


class TestPpoDeployTiers(unittest.TestCase):
    def setUp(self):
        self.cfg = BotConfig()
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    @patch.dict(os.environ, {"PPO_DEPLOY_TIERS_ENABLED": "true"})
    def test_lottery_tier_classification(self):
        self.assertTrue(ppo_deploy_tiers_enabled(self.cfg))
        tier = classify_deploy_tier(
            self.cfg,
            ppo_action=1,
            ppo_conf=0.80,
            profit_probability=0.75,
            spike_ratio=2.0,
            scan_score=60,
        )
        self.assertEqual(tier, "lottery_bullet")

    @patch.dict(os.environ, {"PPO_DEPLOY_TIERS_ENABLED": "true",
                              "PPO_LOTTERY_TIER_SIZE_MULT": "2.0"})
    def test_tier_scales_shares(self):
        dec = {"shares": 10, "deploy_usd": 100.0, "confidence": 0.8}
        out = apply_deploy_tier_to_decision(
            self.cfg, dec, 10.0,
            ppo_action=1, ppo_conf=0.8, spike_ratio=2.0, scan_score=60,
        )
        self.assertEqual(out.get("deploy_tier"), "lottery_bullet")
        self.assertGreaterEqual(int(out.get("shares", 0)), 10)


class TestLearnApproval(unittest.TestCase):
    def setUp(self):
        self.cfg = BotConfig()
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    @patch.dict(os.environ, {"LEARN_APPROVAL_REQUIRED": "true"})
    def test_unapproved_filtered(self):
        self.assertTrue(learn_approval_required(self.cfg))
        rows = [
            {"source": "live_trade", "features": [0.1], "pnl_usd": 5.0},
            stamp_learn_approval(
                {"source": "halim_ppo_coevolution", "features": [0.2]},
                approved=True,
                by="halim",
            ),
        ]
        out = filter_for_ppo_training(rows, self.cfg)
        self.assertEqual(len(out), 1)
        self.assertFalse(eligible_for_ppo_training(rows[0], self.cfg))
        self.assertTrue(eligible_for_ppo_training(rows[1], self.cfg))

    @patch.dict(os.environ, {"LEARN_APPROVAL_REQUIRED": "false"})
    def test_all_pass_when_off(self):
        rows = [{"source": "live_trade", "features": [0.1]}]
        self.assertEqual(len(filter_for_ppo_training(rows, self.cfg)), 1)


class TestPpoWheelExecution(unittest.TestCase):
    def setUp(self):
        self.cfg = BotConfig()
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    @patch.dict(os.environ, {
        "PPO_WHEEL_PROFILE_LOCK": "true",
        "PPO_ONLY_EXECUTION": "true",
    })
    def test_ppo_only_entry_blocks_quality_flash(self):
        from core.smart_stack import build_halim_local_entry
        out = build_halim_local_entry(
            self.cfg,
            halim_live={"status": "stale_context", "parsed": {}},
            quality={"profit_probability": 0.72, "enter_ok": True},
            ppo_action=0,
            ppo_conf=0.54,
            ppo_reason="neutral",
            min_conf=0.58,
            scan_score=80,
            spike_ratio=1.5,
        )
        self.assertFalse(out.get("enter"))
        self.assertEqual(out.get("pipeline"), "ppo:wheel_hold")

    @patch.dict(os.environ, {
        "PPO_WHEEL_PROFILE_LOCK": "true",
        "PPO_ONLY_EXECUTION": "true",
    })
    def test_ppo_only_entry_allows_buy(self):
        from core.smart_stack import build_halim_local_entry
        out = build_halim_local_entry(
            self.cfg,
            halim_live={"status": "stale_context", "parsed": {}},
            quality={"profit_probability": 0.72, "enter_ok": True},
            ppo_action=1,
            ppo_conf=0.62,
            ppo_reason="buy",
            min_conf=0.58,
            scan_score=80,
            spike_ratio=1.5,
        )
        self.assertTrue(out.get("enter"))
        self.assertEqual(out.get("pipeline"), "ppo:wheel_buy")

    @patch.dict(os.environ, {
        "PPO_WHEEL_PROFILE_LOCK": "true",
        "PPO_LEAD_EXITS": "true",
        "COUNCIL_EXECUTION_ADVISORY_ONLY": "true",
    })
    def test_council_cannot_exit_without_ppo(self):
        from core.live_ai_pipeline import merge_exit_decision
        out = merge_exit_decision(
            {"exit": True, "confidence": 0.85, "reason": "take profit"},
            "fresh",
            ppo_exit=False,
            ppo_conf=0.5,
            ppo_reason="hold",
            min_conf=0.58,
            cfg=self.cfg,
        )
        self.assertFalse(out.get("exit"))
        self.assertFalse(out.get("pending"))
        self.assertEqual(out.get("pipeline"), "ppo:wheel_hold")

    @patch.dict(os.environ, {
        "PPO_WHEEL_PROFILE_LOCK": "true",
        "PPO_LEAD_EXITS": "true",
    })
    def test_ppo_lead_exit(self):
        from core.live_ai_pipeline import merge_exit_decision
        out = merge_exit_decision(
            {"exit": False, "confidence": 0.5},
            "fresh",
            ppo_exit=True,
            ppo_conf=0.65,
            ppo_reason="sell signal",
            min_conf=0.58,
            cfg=self.cfg,
        )
        self.assertTrue(out.get("exit"))
        self.assertEqual(out.get("pipeline"), "ppo:wheel_exit")

    @patch.dict(os.environ, {
        "PPO_WHEEL_PROFILE_LOCK": "true",
        "PPO_LEAD_EXITS": "true",
        "COUNCIL_EXECUTION_ADVISORY_ONLY": "true",
    })
    def test_stagnation_wheel_mech_timeout(self):
        from core.live_ai_pipeline import merge_stagnation_decision
        out = merge_stagnation_decision(
            {}, "missing",
            ppo_exit=False, ppo_conf=0.5, ppo_reason="Warming up",
            min_conf=0.58, stagnant_sec=95.0, stagnation_sec=90.0,
            cfg=self.cfg,
        )
        self.assertTrue(out.get("exit"))
        self.assertEqual(out.get("pipeline"), "mech:stagnation_timeout")
        self.assertIn("mechanical", out.get("reason", ""))

    @patch.dict(os.environ, {
        "COUNCIL_NANNY_LOW_TASKS": "false",
        "PPO_WHEEL_PROFILE_LOCK": "false",
        "PPO_LEAD_EXITS": "false",
        "COUNCIL_EXECUTION_ADVISORY_ONLY": "false",
    })
    def test_stagnation_missing_label(self):
        from core.live_ai_pipeline import council_status_label, merge_stagnation_decision
        self.assertIn("skipped", council_status_label("missing"))
        out = merge_stagnation_decision(
            {}, "missing",
            ppo_exit=False, ppo_conf=0.5, ppo_reason="hold",
            min_conf=0.58, stagnant_sec=40.0, stagnation_sec=90.0,
            cfg=self.cfg,
        )
        self.assertTrue(out.get("pending"))
        self.assertIn("skipped", out.get("reason", "").lower())


if __name__ == "__main__":
    unittest.main()
