"""IB client backend shim tests."""
from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


def test_ib_backend_prefers_env(monkeypatch):
    monkeypatch.setenv("IB_CLIENT_BACKEND", "ib_insync")
    import core.ib_client as ic
    importlib.reload(ic)
    fake = MagicMock()
    fake.IB = object
    with patch.dict("sys.modules", {"ib_insync": fake, "ib_async": MagicMock()}):
        importlib.reload(ic)
        ic._MODULE = None
        ic._LOADED_NAME = ""
        assert ic.ib_backend_name() == "ib_insync"


def test_ib_client_exports_ib_class(monkeypatch):
    monkeypatch.setenv("IB_CLIENT_BACKEND", "ib_insync")
    import core.ib_client as ic
    importlib.reload(ic)
    fake_mod = MagicMock()
    fake_mod.IB = type("IB", (), {})
    fake_mod.MarketOrder = type("MarketOrder", (), {})
    with patch.dict("sys.modules", {"ib_insync": fake_mod, "ib_async": MagicMock()}):
        importlib.reload(ic)
        ic._MODULE = None
        ic._LOADED_NAME = ""
        assert ic.IB is fake_mod.IB
