# HANOON Horizon Roadmap

**One hull** (`scalper_runner` = Life Engine) · **One IB Truth** · **Horizon dimension**

IB Gateway is the economic source of truth for positions, cash, PnL, fills, and open orders. Local ledgers (war $1k pool, verdict logs) tag `horizon` for learning and sizing only.

## Horizons

| Horizon | Live orders | Purpose now |
|---------|-------------|-------------|
| `scalp` | Yes (only path) | RTH spikes, PPO reflex, Halim entries |
| `swing` | No (shadow) | 1h scan, verdict labels, teacher curriculum |
| `position` | No (future) | Multi-day holds when adult + scalp gate passes |

## Maturity gates (`core/trade_horizon.py`)

| Stage | Swing shadow | Swing paper | Position |
|-------|--------------|-------------|----------|
| newborn | — | — | — |
| child | log-only 1h scan | — | — |
| teen | yes | env `SWING_PAPER_ENABLED=true` + scalp gate | — |
| adult | yes | yes | env `POSITION_HORIZON_ENABLED=true` |

**Scalp profit gate:** IB `RealizedPnL` tag (not local FIFO) must show edge before swing paper / position live. Override: `SCALP_PROFIT_GATE_FORCE=pass|fail`.

## IB Truth (`core/ib_truth.py`)

Fetched every balance refresh — consumers read `get_snapshot()`:

- `accountValues` → NetLiq, RealizedPnL, UnrealizedPnL, BuyingPower, MaintMarginReq
- `positions()` + `portfolio()` → qty, avg cost, market price, unrealized/realized per line
- `openTrades()` → live order state (brackets)
- `fills()` → FIFO round trips for per-ticker audit (secondary to IB tags for session PnL)

Session PnL display order: **IB RealizedPnL** → FIFO fills → NetLiq delta since RTH open.

## Files

| Module | Role |
|--------|------|
| `core/ib_truth.py` | Central IB snapshot + `ib_truth_context()` |
| `core/trade_horizon.py` | Horizon enum, maturity gates, scalp gate |
| `core/swing_shadow.py` | Off-hours 1h shadow scan → `models/swing_shadow_verdicts.jsonl` |
| `core/war_ib_sync.py` | War $1k virtual ledger synced from IB positions |
| `core/account_view.py` | Telegram/Halim equity + session PnL |
| `scripts/reconcile_ib_truth.py` | CLI reconcile war vs IB |

## Env vars

```
IB_TRUTH_RTH_SESSION=true
IB_TRUTH_RTH_FILLS_ONLY=true
WAR_CAPITAL_USD=1000
WAR_IB_SYNC=true
WAR_IB_SYNC_INTERVAL_SEC=90
SWING_SHADOW_ENABLED=true
SWING_SHADOW_INTERVAL_SEC=900
SWING_PAPER_ENABLED=false
POSITION_HORIZON_ENABLED=false
```

## Live today (2026-07-01)

- `core/ib_extended.py` — reqPnL, reqPnLSingle, fundamentals, news, WSH, whatIfOrder
- `core/swing_paper.py` + `WAR_SWING_PAPER_USD` virtual pool
- `core/ppo_swing_train.py` → `models/ppo_swing_1h.json`
- War ledger `horizon` tags
- Bracket what-if margin gate in `broker.py`

## Remaining

- `POSITION_HORIZON_ENABLED` live multi-day IB orders
- Options `reqSecDefOptParams`
