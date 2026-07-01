"""IB async-loop guard and adopt grace after recent exit."""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

from core.ib_sync import ib_blocking_calls_safe, safe_qualify_contracts
from core.position_sync import adopt_ib_positions_into_slots


def test_ib_blocking_calls_safe_without_loop():
    ib = MagicMock()
    ib.isConnected.return_value = True
    assert ib_blocking_calls_safe(ib) is True


def test_ib_blocking_calls_safe_off_main_thread():
    ib = MagicMock()
    ib.isConnected.return_value = True
    result = {"ok": False}

    def _worker():
        result["ok"] = ib_blocking_calls_safe(ib)

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert result["ok"] is False


def test_ib_blocking_calls_safe_inside_async_loop():
    ib = MagicMock()
    ib.isConnected.return_value = True

    async def _run():
        return ib_blocking_calls_safe(ib)

    assert asyncio.run(_run()) is False


def test_safe_qualify_skips_coroutine_result():
    ib = MagicMock()
    ib.isConnected.return_value = True

    async def _fake():
        return []

    ib.qualifyContracts.return_value = _fake()
    assert safe_qualify_contracts(ib, MagicMock()) == []


def test_adopt_skips_recently_exited_ticker():
    ib = MagicMock()
    slots: dict = {}
    recently = {"INLF": time.time()}
    with patch(
        "core.position_sync.ib_long_position_map",
        return_value={"INLF": 1200.0},
    ):
        adopted = adopt_ib_positions_into_slots(
            ib,
            slots,
            recently_exited=recently,
            recently_exited_grace_sec=600.0,
        )
    assert adopted == []
    assert "INLF" not in slots


def test_adopt_recovers_when_grace_expired():
    ib = MagicMock()
    slots: dict = {}
    recently = {"INLF": time.time() - 900.0}
    with patch(
        "core.position_sync.ib_long_position_map",
        return_value={"INLF": 1200.0},
    ), patch(
        "core.position_sync.position_entry_price",
        return_value=0.03,
    ):
        adopted = adopt_ib_positions_into_slots(
            ib,
            slots,
            recently_exited=recently,
            recently_exited_grace_sec=300.0,
        )
    assert adopted == ["INLF"]
    assert slots["INLF"]["shares"] == 1200.0
