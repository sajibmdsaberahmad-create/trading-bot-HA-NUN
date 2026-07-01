"""ScalperRunner smoke — construct hull with mocked IB without starting loop."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.config import BotConfig
from core.scalper_runner import ScalperRunner


@pytest.fixture
def mock_connector():
    conn = MagicMock()
    conn.ib = MagicMock()
    conn.register_market_data_error_handler = MagicMock()
    conn.register_tick_limit_handler = MagicMock()
    conn.register_session_reclaim_handler = MagicMock()
    conn.register_connectivity_handler = MagicMock()
    return conn


def test_scalper_runner_instantiates(mock_connector):
    cfg = BotConfig()
    notifier = MagicMock()
    with patch("core.scalper_runner.git_sync_init"):
        with patch("core.scalper_runner.StockScanner"):
            with patch("core.scalper_runner.DataManager"):
                runner = ScalperRunner(mock_connector, cfg, notifier)
    assert runner.cfg is cfg
    assert runner.conn is mock_connector
    assert runner.bot_nav == float(cfg.INITIAL_CASH)


def test_generate_guidelines_delegates(mock_connector):
    cfg = BotConfig()
    notifier = MagicMock()
    with patch("core.scalper_runner.git_sync_init"):
        with patch("core.scalper_runner.StockScanner"):
            with patch("core.scalper_runner.DataManager"):
                runner = ScalperRunner(mock_connector, cfg, notifier)
    runner.scan_results = []
    text = runner._generate_guidelines()
    assert "HANOON SELF-IMPROVEMENT GUIDELINES" in text
