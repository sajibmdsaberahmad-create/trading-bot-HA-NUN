"""ReplayConnector parity with live Connector surface."""

from __future__ import annotations

from core.config import BotConfig
from core.replay_connector import ReplayConnector


def test_replay_connector_connectivity_outage_always_false():
    conn = ReplayConnector(BotConfig())
    conn.connect()
    assert conn.is_connected()
    assert conn.in_connectivity_outage() is False


def test_replay_connector_session_reclaim_noops():
    conn = ReplayConnector(BotConfig())
    conn.request_session_reclaim()
    conn.clear_pending_session_reclaim()
    assert conn.run_pending_session_reclaim() is None
