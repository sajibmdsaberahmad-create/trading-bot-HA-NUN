"""IB data catalog and AI context."""
from core.ib_data_catalog import ACCOUNT_TAG_FIELD_MAP, catalog_summary
from core.ib_truth import ib_ai_context, IBAccountSnapshot, IBTruthSnapshot


def test_account_tag_map_covers_core_fields():
    assert "NetLiquidation" in ACCOUNT_TAG_FIELD_MAP
    assert "RealizedPnL" in ACCOUNT_TAG_FIELD_MAP
    assert "DayTradesRemaining" in ACCOUNT_TAG_FIELD_MAP


def test_catalog_summary_structure():
    s = catalog_summary()
    assert s["account_tag_count"] > 10
    assert s["api_endpoints_used"] >= 5


def test_ib_ai_context_offline():
    ctx = ib_ai_context()
    assert "catalog" in ctx


def test_extended_account_snapshot_fields():
    acct = IBAccountSnapshot(
        net_liquidation=1000,
        excess_liquidity=500,
        day_trades_remaining=3,
    )
    assert acct.excess_liquidity == 500
    assert acct.day_trades_remaining == 3
