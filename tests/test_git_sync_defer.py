#!/usr/bin/env python3
"""Unit tests for git sync session deferral."""

from __future__ import annotations

import os
from unittest.mock import patch

from core import git_sync


def test_session_push_disabled_by_default():
    with patch.dict(os.environ, {"GIT_PUSH_DURING_SESSION": "false"}, clear=False):
        git_sync.set_global_config(None)
        assert git_sync._git_session_push_enabled() is False
        assert git_sync._batch_checkpoints_enabled() is False


def test_flush_deferred_when_session_push_off():
    with patch.dict(os.environ, {"GIT_PUSH_DURING_SESSION": "false"}, clear=False):
        git_sync.set_global_config(None)
        git_sync._queue_batched_checkpoint("trade_closed_TEST")
        ok = git_sync.flush_batched_git_sync("session_batch", force=True)
        assert ok is False
        assert len(git_sync._checkpoint_batched_reasons) >= 1


def test_shutdown_flush_allowed():
    with patch.dict(os.environ, {"GIT_PUSH_DURING_SESSION": "false"}, clear=False):
        git_sync.set_global_config(None)
        git_sync._queue_batched_checkpoint("trade_closed_TEST")
        with patch.object(git_sync, "push_learning_checkpoint", return_value=True) as mock:
            ok = git_sync.flush_batched_git_sync("pre_shutdown", force=True)
        assert ok is True
        mock.assert_called_once()
