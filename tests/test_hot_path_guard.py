"""Tests for core.hot_path_guard."""

from core.hot_path_guard import log_hot_path_warning, _last_warn


def test_log_hot_path_no_exc(caplog):
    _last_warn.clear()
    log_hot_path_warning("ib_sync")
    assert any("Hot-path ib_sync" in r.message for r in caplog.records)
