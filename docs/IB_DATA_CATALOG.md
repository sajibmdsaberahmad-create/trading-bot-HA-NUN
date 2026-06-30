# IB Data Catalog — A to Z

**Rule:** If IB provides it, use it. Local math is for signals (bars, spikes) and war virtual ledger tags only — never for NAV, PnL, margin, or fill prices.

Hub: `core/ib_truth.py` · Catalog: `core/ib_data_catalog.py` · AI bundle: `ib_ai_context()`

---

## A — Account & equity

| IB source | Fields | HANOON consumer |
|-----------|--------|-----------------|
| `accountValues()` | NetLiquidation, TotalCashValue, SettledCash | `ib_truth`, `account_view` |
| | RealizedPnL, UnrealizedPnL | Session PnL display (primary) |
| | BuyingPower, AvailableFunds, ExcessLiquidity | Risk, war sizing context |
| | InitMarginReq, MaintMarginReq, Cushion, Leverage | AI survival context |
| | DayTradesRemaining | PDT guard for AI replies |
| | PreviousDayEquityWithLoanValue | RTH baseline cross-check |

All tags stored in `snap.account.tags`; mapped fields in `IBAccountSnapshot`.

---

## B — Bars (historical & realtime)

| IB source | Use | Notes |
|-----------|-----|-------|
| `reqHistoricalData()` | PPO features, swing shadow, replay | **Signals only** — not accounting |
| `reqRealTimeBars(5s)` | Fallback when no tick stream | Stop monitoring |
| `reqHeadTimeStamp()` | Bar alignment | Available, not wired |

Consumer: `core/data.py`, `core/swing_shadow.py`

---

## C — Connection & clock

| IB source | Use |
|-----------|-----|
| `reqCurrentTime()` | Health ping, `snap.server_time` |
| `connectedEvent` / `errorEvent` | Reconnect, 10197 reclaim |
| `reqMarketDataType()` | Live vs delayed |

Consumer: `core/connector.py`, `ib_truth.fetch_ib_server_time()`

---

## D — Executions & fills

| IB source | Fields | Use |
|-----------|--------|-----|
| `fills()` | price, shares, side, time, execId | FIFO round trips, fill ledger |
| `commissionReport` | commission, realizedPNL | Per-fill cost — summed in `session_commissions` |
| `reqExecutions(filter)` | Historical filter | Reconciliation |

Consumer: `ib_truth`, `fill_tracker`, `war_ib_sync`

---

## E — Exchange & contract

| IB source | Use |
|-----------|-----|
| `qualifyContracts()` | conId, primary exchange |
| `reqContractDetails()` | Trading hours, min tick, margin per symbol |
| `marketRule()` | Price increment table |

Positions carry `con_id` — used by `reqPnLSingle` in `ib_extended.py`.

---

## F — Fundamentals

| IB source | Consumer |
|-----------|----------|
| `reqFundamentalData(ReportSnapshot)` | `core/ib_extended.py` — off-hours full refresh |

Halim: `ib_fundamentals` in `ib_ai_context()`.

---

## G — Greeks / options

| IB source | Status |
|-----------|--------|
| `reqSecDefOptParams()` | Not in equity scalp hull |
| `calculateImpliedVolatility()` | Not in equity scalp hull |

---

## H — Historical news

| IB source | Consumer |
|-----------|----------|
| `reqNewsBulletins()` | `ib_extended.fetch_news_bulletins` |
| `reqHistoricalNews()` | `ib_extended.fetch_historical_news` |

Halim: `ib_news_bulletins`, `ib_news_headlines`.

---

## I — Instrument portfolio

| IB source | Fields | Use |
|-----------|--------|-----|
| `positions()` | qty, avgCost | Ground truth qty |
| `portfolio()` | marketPrice, marketValue, unrealizedPNL, realizedPNL, costBasis | Marks & per-line PnL |

Consumer: `ib_truth.fetch_ib_positions()`

---

## L — Live quotes (L1)

| IB source | Use |
|-----------|-----|
| `reqTickers()` | **One-shot** SPY/QQQ/VIX — `ib_macro.py` |
| `reqMktData()` | Streaming L1 — entries, legacy macro |
| `cancelMktData()` | Release lines |

Prefer `reqTickers` for macro snapshots; stream only for held symbols.

---

## M — Margin preview

| IB source | Use |
|-----------|-----|
| `whatIfOrder()` | Pre-trade margin impact |

**Wired** — `core/broker.py` before every bracket; gate `IB_WHATIF_MARGIN_GATE`.

---

## N — News bulletins

See **H — Historical news**.

---

## O — Orders & brackets

| IB source | Fields | Use |
|-----------|--------|-----|
| `openTrades()` / `trades()` | status, filled, avgFillPrice | Bracket state |
| Order object | lmtPrice, auxPrice, parentId, tif, outsideRth | Stop/target visibility for AI |
| `reqAllOpenOrders()` | Include TWS manual orders | Reconcile |

Consumer: `ib_truth.fetch_ib_open_orders()` → `ib_open_orders_detail` in AI context.

---

## P — PnL streams

| IB source | Use |
|-----------|-----|
| `reqPnL(account)` | Streaming account PnL |
| `reqPnLSingle(account, conId)` | Per-symbol streaming PnL |

**Wired** — `core/ib_extended.py`; also `RealizedPnL` account tag in `ib_truth`.

---

## R — Risk numbers

All from `accountValues()` — see **A**. AI gets `ib_excess_liquidity`, `ib_cushion`, `ib_day_trades_remaining` via `ib_ai_context()`.

---

## S — Scanner

| IB source | Use |
|-----------|-----|
| `reqScannerSubscription()` | Top % gainers, volume movers |

Consumer: `core/scanner.py` — watchlist seed only; ranks from IB metadata, not local price DB.

---

## T — Tick-by-tick

| IB source | Use |
|-----------|-----|
| `reqTickByTickData(AllLast)` | Every print — spike detection, stop breach |

Consumer: `core/data.py` — fastest layer.

---

## W — Wall Street Horizon

| IB source | Use |
|-----------|-----|
| `reqWshMetaData()` / earnings calendar | Event risk |

**Wired** — `ib_extended.fetch_wsh_events` (off-hours; requires IB WSH subscription).

---

## What AIs receive today

Call `ib_ai_context(cfg, connector)` — includes:

- Full account tags (margin, PDT, PnL)
- Positions with IB marks and cost basis
- Open orders with stop/limit/bracket parentId
- Session FIFO per-ticker + IB per-line realized
- Commissions (from IB fill reports)
- Macro SPY/QQQ/VIX from IB (`MACRO_FROM_IB=true`)
- **Extended:** fundamentals, news, WSH, reqPnLSingle, contract hours, market rules
- Horizon gates + swing paper pool context

Wired into: `halim_companion.live_snapshot()`, expandable to council/copilot.

---

## Env vars

```
MACRO_FROM_IB=true          # SPY/QQQ/VIX from reqTickers, Yahoo fallback
IB_MACRO_TTL_SEC=120
IB_EXTENDED_ENABLED=true
IB_WHATIF_MARGIN_GATE=true
WAR_SWING_PAPER_USD=2000
SWING_PAPER_ENABLED=false
```

---

## Migration checklist (duplicate IB calls → ib_truth)

| File | Status |
|------|--------|
| `account_evaluator.py` | ✅ uses get_snapshot |
| `daily_ib_learning.py` | ✅ delegates to build_snapshot |
| `position_intel.py` | Uses ib_truth |
| `account_view.py` | Uses ib_truth |
| `market_context.py` | ✅ IB-first macro |
| `halim_companion.py` | ✅ ib_ai_context |

Remaining direct `ib.positions()` calls in broker/exit paths are intentional for order placement latency.
