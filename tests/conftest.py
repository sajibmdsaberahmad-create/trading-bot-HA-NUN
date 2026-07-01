"""Shared pytest fixtures — isolate PPO wheel / capital-phase env from unit tests."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

import pytest


@contextmanager
def ppo_wheel_off() -> Iterator[None]:
    """Disable PPO-only short-circuit so Halim / ai-sure paths are testable."""
    env = {
        "PPO_WHEEL_PROFILE_LOCK": "false",
        "PPO_ONLY_EXECUTION": "false",
        "SMART_STACK_AI_SURE_ENTRY": "true",
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        with patch("core.ppo_wheel_profile.ppo_wheel_profile_lock", return_value=False):
            with patch("core.ppo_wheel_profile.ppo_only_execution_enabled", return_value=False):
                yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def war_ledger_on() -> Iterator[None]:
    """Force war ledger apply (tests run outside RTH war phase)."""
    with patch("core.war_account.war_ledger_applies", return_value=True):
        yield


@pytest.fixture
def ppo_wheel_off_env():
    with ppo_wheel_off():
        yield


@pytest.fixture
def war_ledger_env():
    with war_ledger_on():
        yield
