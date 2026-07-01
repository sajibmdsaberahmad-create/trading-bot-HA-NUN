"""Halim v5 prep helpers."""
from __future__ import annotations

import os
from unittest.mock import patch

from core.halim_guardrails import effective_learn_fetch_daily_cap, v5_prep_active


def test_v5_prep_active_env():
    with patch.dict(os.environ, {"HALIM_V5_PREP": "true"}, clear=False):
        assert v5_prep_active() is True
        assert effective_learn_fetch_daily_cap() >= 2000


def test_v5_prep_inactive_by_default():
    env = {k: v for k, v in os.environ.items() if k != "HALIM_V5_PREP"}
    with patch.dict(os.environ, env, clear=True):
        assert v5_prep_active() is False
