#!/usr/bin/env python3
"""Tests for PPO wheel env lock."""

import os
import unittest
from unittest.mock import patch

from core.commander_learning import _apply_mutation
from core.config import BotConfig
from core.halim_entry_line import halim_entry_await_sec
from core.param_bounds import is_runtime_blocked
from core.ppo_wheel_profile import is_ppo_wheel_locked_param, ppo_wheel_profile_lock


class TestPpoWheelProfileLock(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self.cfg = BotConfig()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    @patch.dict(os.environ, {"PPO_WHEEL_PROFILE_LOCK": "true"})
    def test_locked_params_blocked(self):
        self.assertTrue(ppo_wheel_profile_lock())
        self.assertTrue(is_ppo_wheel_locked_param("CONFIDENCE_THRESHOLD"))
        self.assertTrue(is_runtime_blocked("MIN_PROFIT_PROBABILITY"))

    @patch.dict(os.environ, {"PPO_WHEEL_PROFILE_LOCK": "true"})
    def test_commander_mutation_rejected(self):
        ok, msg = _apply_mutation(
            self.cfg, "CONFIDENCE_THRESHOLD", 0.71, "test", autopilot=None,
        )
        self.assertFalse(ok)
        self.assertIn("ppo_wheel_locked", msg)

    @patch.dict(os.environ, {"HALIM_ENTRY_AWAIT_SEC": "0"})
    def test_default_await_zero(self):
        self.assertEqual(halim_entry_await_sec(self.cfg), 0.0)


if __name__ == "__main__":
    unittest.main()
