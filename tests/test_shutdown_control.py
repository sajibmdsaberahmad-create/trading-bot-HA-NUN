"""Graceful shutdown helpers."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from core.shutdown_control import (
    clear_shutdown_request,
    interruptible_wait,
    request_shutdown,
    shutdown_requested,
)


def test_interruptible_wait_returns_on_shutdown_file(tmp_path, monkeypatch):
    shutdown = tmp_path / "shutdown.request"
    monkeypatch.setenv("HANOON_SHUTDOWN_FILE", str(shutdown))

    clear_shutdown_request()

    def _request_after_delay():
        time.sleep(0.05)
        request_shutdown("test")

    import threading
    threading.Thread(target=_request_after_delay, daemon=True).start()

    t0 = time.time()
    aborted = interruptible_wait(5.0, check_interval=0.05)
    elapsed = time.time() - t0

    assert aborted is True
    assert elapsed < 1.0
    assert shutdown_requested() is True


def test_interruptible_wait_honors_extra_check():
    ib = MagicMock()
    calls = {"n": 0}

    def extra():
        calls["n"] += 1
        return calls["n"] >= 2

    with patch("core.shutdown_control.shutdown_requested", return_value=False):
        aborted = interruptible_wait(2.0, ib=ib, check_interval=0.01, extra_check=extra)

    assert aborted is True
    assert ib.sleep.call_count >= 1
