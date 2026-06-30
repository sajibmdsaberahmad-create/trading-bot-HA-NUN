#!/usr/bin/env python3
"""
core/ib_data_catalog.py — A–Z inventory of IB Gateway / TWS API data.

Reference for what IB can supply vs what HANOON consumes. Accounting and
risk numbers must come from IB tags/fills — never re-derived locally when
IB provides them.

See also: docs/IB_DATA_CATALOG.md
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# ── Account value tags (accountValues / reqAccountUpdates) ─────────────────
# Full tag list from IB API — we map the trading-critical ones in ib_truth.

ACCOUNT_TAGS: Dict[str, str] = {
  # Equity & cash
  "NetLiquidation": "Total account value (NAV)",
  "TotalCashValue": "Total cash including FX",
  "SettledCash": "Settled cash (T+1 aware)",
  "AccruedCash": "Accrued dividends/interest",
  "EquityWithLoanValue": "Equity including loan value",
  "PreviousDayEquityWithLoanValue": "Prior day equity — session baseline",
  "GrossPositionValue": "Absolute market value of positions",
  "CashBalance": "Cash per currency",
  # P&L (broker-computed — prefer over local FIFO for display)
  "RealizedPnL": "Session/day realized P&L",
  "UnrealizedPnL": "Open position unrealized P&L",
  # Buying power & margin
  "BuyingPower": "Max purchasable notional",
  "AvailableFunds": "Funds available for trading",
  "ExcessLiquidity": "Cushion above margin requirement",
  "InitMarginReq": "Initial margin required",
  "MaintMarginReq": "Maintenance margin required",
  "FullInitMarginReq": "Portfolio margin init",
  "FullMaintMarginReq": "Portfolio margin maint",
  "FullAvailableFunds": "PM available funds",
  "FullExcessLiquidity": "PM excess liquidity",
  "RegTEquity": "Reg T equity",
  "RegTMargin": "Reg T margin",
  "SMA": "Special memorandum account",
  "Cushion": "Excess liquidity / net liquidation %",
  "Leverage": "Account leverage ratio",
  # Day trade / restrictions
  "DayTradesRemaining": "PDT day trades left (-1 = unlimited)",
  "DayTradesRemainingT+1": "DT count after T+1",
  "LookAheadNextChange": "Next margin look-ahead change time",
  "LookAheadInitMarginReq": "Look-ahead init margin",
  "LookAheadMaintMarginReq": "Look-ahead maint margin",
  "LookAheadAvailableFunds": "Look-ahead available funds",
  "LookAheadExcessLiquidity": "Look-ahead excess liquidity",
  "HighestSeverity": "Margin call severity",
}

# Tags wired into IBAccountSnapshot fields (ib_truth.fetch_ib_account_snapshot)
ACCOUNT_TAG_FIELD_MAP: Dict[str, str] = {
  "NetLiquidation": "net_liquidation",
  "TotalCashValue": "total_cash",
  "SettledCash": "settled_cash",
  "AccruedCash": "accrued_cash",
  "RealizedPnL": "realized_pnl",
  "UnrealizedPnL": "unrealized_pnl",
  "GrossPositionValue": "gross_position_value",
  "BuyingPower": "buying_power",
  "AvailableFunds": "available_funds",
  "ExcessLiquidity": "excess_liquidity",
  "InitMarginReq": "init_margin_req",
  "MaintMarginReq": "maint_margin_req",
  "EquityWithLoanValue": "equity_with_loan",
  "PreviousDayEquityWithLoanValue": "prev_day_equity",
  "DayTradesRemaining": "day_trades_remaining",
  "Leverage": "leverage",
  "Cushion": "cushion",
  "SMA": "sma",
  "RegTEquity": "regt_equity",
}

# ── API surface categories (ib_insync / TWS) ───────────────────────────────

IB_API_CATEGORIES: List[Tuple[str, str, str, str]] = [
  # (category, ib_call, used_by_hanoon, notes)
  ("Account", "accountValues()", "ib_truth", "All tags in ACCOUNT_TAGS"),
  ("Account", "reqAccountUpdates()", "—", "Streaming account — use tags above"),
  ("Account", "reqAccountSummary()", "—", "Group summary — redundant with values"),
  ("Positions", "positions()", "ib_truth, position_sync", "Qty + avgCost per contract"),
  ("Positions", "portfolio()", "ib_truth", "Mark, unrealized, realized, marketValue"),
  ("Positions", "reqPositions()", "broker, exit", "Force refresh before positions()"),
  ("PnL", "reqPnL(account)", "ib_extended", "Streaming account PnL snapshot"),
  ("PnL", "reqPnLSingle(conId)", "ib_extended", "Per-symbol IB PnL"),
  ("Orders", "openTrades()", "ib_truth, broker", "Live orders + status"),
  ("Orders", "trades()", "ib_truth, daily_ib_learning", "Session order history"),
  ("Orders", "reqAllOpenOrders()", "ib_truth", "Include TWS manual orders"),
  ("Orders", "whatIfOrder()", "ib_extended, broker", "Margin preview before bracket entry"),
  ("Fills", "fills()", "ib_truth, fill_tracker", "Executions + commissionReport"),
  ("Fills", "reqExecutions()", "fill_tracker", "Historical execution filter"),
  ("Market", "reqMktData()", "scalper_runner, market_context", "L1 quote stream"),
  ("Market", "reqTickers()", "trader, ib_macro", "One-shot snapshot — preferred"),
  ("Market", "reqTickByTickData()", "data.py", "Every trade print — spike/stops"),
  ("Market", "cancelMktData()", "connector", "Release quote lines"),
  ("Bars", "reqHistoricalData()", "data.py, swing_shadow", "OHLCV — signals only"),
  ("Bars", "reqRealTimeBars()", "data.py fallback", "5s bars when no tick stream"),
  ("Scanner", "reqScannerSubscription()", "scanner.py", "Top % gainers, volume"),
  ("Contract", "qualifyContracts()", "connector", "conId, exchange, min tick"),
  ("Contract", "reqContractDetails()", "ib_extended", "Hours, margin, listings"),
  ("Contract", "reqSecDefOptParams()", "—", "Options chain params"),
  ("Time", "reqCurrentTime()", "connector", "Gateway clock + health ping"),
  ("Time", "reqHeadTimeStamp()", "ib_extended", "First bar timestamp for symbol"),
  ("News", "reqNewsBulletins()", "ib_extended", "IB news headlines"),
  ("News", "reqHistoricalNews()", "ib_extended", "Per-symbol headlines"),
  ("Fundamentals", "reqFundamentalData()", "ib_extended", "ReportSnapshot ratios"),
  ("Risk", "marketRule()", "ib_extended", "Min price increment table"),
  ("Calendar", "reqWshMetaData()", "ib_extended", "Wall Street Horizon events"),
  ("Connection", "connectedEvent / errorEvent", "connector", "Health + 10197 reclaim"),
]

# Consumption status for AI context builder
USED_NOW = frozenset({
  "ib_truth", "ib_extended", "position_sync", "broker", "fill_tracker", "scalper_runner",
  "market_context", "data.py", "scanner.py", "connector", "trader",
  "ib_macro", "daily_ib_learning", "account_view", "war_ib_sync",
  "swing_paper", "ppo_swing_train",
})


def catalog_summary() -> Dict[str, Any]:
    """Machine-readable catalog for /status and Halim."""
    used = [r for r in IB_API_CATEGORIES if r[2] != "—" and r[2] != "planned"]
    planned = [r for r in IB_API_CATEGORIES if r[2] == "planned"]
    unused = [r for r in IB_API_CATEGORIES if r[2] == "—"]
    return {
      "account_tag_count": len(ACCOUNT_TAGS),
      "account_tags_mapped": len(ACCOUNT_TAG_FIELD_MAP),
      "api_endpoints_total": len(IB_API_CATEGORIES),
      "api_endpoints_used": len(used),
      "api_endpoints_planned": len(planned),
      "api_endpoints_available": len(unused),
      "used_calls": [{"category": c, "call": call, "consumer": cons} for c, call, cons, _ in used],
      "planned_calls": [{"category": c, "call": call, "notes": n} for c, call, _, n in planned],
    }
