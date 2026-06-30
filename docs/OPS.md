# HANOON Operations — canonical entry & env

## Start / stop

```bash
./start.sh                    # → scripts/start_hanoon.sh → main.py --mode scalper
./scripts/stop_hanoon.sh      # graceful shutdown + one git learning push
./scripts/stop_git_sync.sh    # optional standalone git daemon
```

**Canonical live path:** `main.py --mode scalper` only.

Legacy modes (`trade`, `warmup`, `fusion-trade`) remain in `main.py` for compatibility — not used in production.

## IB connection

| Setting | Default | Notes |
|---------|---------|--------|
| `IB_PORT` | **4002** | IB Gateway paper (`BotConfig`) |
| TWS paper | 7497 | Pass `--port 7497` if using TWS |
| TWS live | 7496 | Real money |

## Accounting truth

| Layer | Role |
|-------|------|
| **IB `account_equity`** | Display P&L when `REQUIRE_IB_FILL_SYNC=true` |
| **`core/account_view.py`** | Single helper for equity / day P&L |
| **`war_account`** | Sizing bullets / lab pool |
| **`bot_nav`** | Internal bookkeeping — not shown as P&L when IB sync on |

## Git during live session

| Env | Default | Effect |
|-----|---------|--------|
| `GIT_PUSH_DURING_SESSION` | **false** | Queue learning; push on `stop_hanoon` |
| `START_GIT_SYNC_WITH_HANOON` | **false** | No background git daemon during RTH |
| `LEARNING_PUSH_ON_TRADE` | false in launcher | Queues only; no mid-session push |

## Smart stack

| Env | Default |
|-----|---------|
| `SMART_STACK` | true |
| `RAM_LIVE_ONLY` | true |
| `REQUIRE_IB_FILL_SYNC` | true |
| `IB_FILL_STRICT` | true |

See `docs/VISION_SMART_STACK.md` for pipeline rules.

## Tests

```bash
python3 -m pytest tests/ -q
```

## Repo layout

- `core/scalper_runner.py` — hull (being split: `entry_pipeline.py`, `account_view.py`)
- `core/smart_stack.py` — decision hub
- `core/git_sync.py` — learning artifact sync
- `archive/` — deprecated modules (e.g. `replay_live_runner.py`)
