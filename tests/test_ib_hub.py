"""IB hub orchestrator."""
from core.ib_hub import audit_ib_coverage, ib_hub_enabled


def test_hub_enabled_default():
    import os
    from unittest.mock import patch
    with patch.dict("os.environ", {"IB_HUB_ENABLED": "true"}):
        assert ib_hub_enabled() is True


def test_audit_coverage():
    r = audit_ib_coverage()
    assert r["api_endpoints_used"] >= 20
    assert r["api_endpoints_total"] >= r["api_endpoints_used"]
