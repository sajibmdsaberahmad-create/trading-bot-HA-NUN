# Architecture

How the codebase is organized and why. **Production live trading:** `main.py --mode scalper`
→ `core/scalper_runner.py` (HANOON hull). See `docs/OPS.md` and `docs/VISION_SMART_STACK.md`.

Legacy PPO single-ticker mode (`trader.py`, `--mode trade`) remains for compatibility.

---

## File Map

```
main.py                  CLI — scalper (canonical), replay-live, legacy trade/warmup

core/
  scalper_runner.py      HANOON hull — scanner, entry, exit, council (split in progress)
  smart_stack.py         Life Engine hub — sensors → PPO → Halim → blend → war
  ai_commander.py        Council prompts, spike decisions, learning export
  account_view.py        IB-grounded equity / day P&L display
  entry_pipeline.py      IB-confirmed entry fill detection
  config.py              BotConfig — tunable parameters
  broker.py              IB bracket orders
  git_sync.py            Learning artifact sync (delegates defer to git_sync_defer)
  war_account.py         Virtual sizing ledger ($1k war / lab)
  trader.py              LEGACY — single-ticker PPO live trader

docs/
  OPS.md                 Start/stop, env defaults, accounting truth
  VISION_SMART_STACK.md  Smart stack rules (mandatory for loop changes)
  ENGINEERING_FIX_LOG.md Fix journal
```

The scalper hull orchestrates modules; prefer extending `smart_stack.py` and
`entry_pipeline.py` over growing `scalper_runner.py` further.

---

## The Core Design Decision: AI Decides *When*, Risk Engine Decides *How Much*

The PPO agent's action space is `Discrete(3)`: HOLD, BUY, SELL. That's
it. It never outputs a position size, a stop price, or a target price.
Those are computed by `core/risk.py`, deterministically, from current
volatility (ATR) and your configured risk tolerance — every single
time, regardless of what the model "wants."

This means a bug in training, an overfit model, or a fine-tune session
that temporarily destabilizes the policy **cannot** make the bot risk
more than `MAX_RISK_PER_TRADE_USD` on a trade or stay in a position
past its stop. The blast radius of "the AI is wrong" is capped by
construction, not by hoping the AI behaves.

---

## The Exit System: Four Mechanisms, One State Object

Every open position is represented by a single `TradePlan`
(`core/risk.py`), which both the tick loop and the decision loop in
`core/trader.py` read and update. There are four ways a position closes
automatically, in addition to the AI's own SELL signal:

```
                         entry_price
                              │
    ┌─────────────────────────┼─────────────────────────┐
    │                          │                          │
hard stop              entry placed              hard take-profit
(ATR × 1.5,             with both as                (ATR × 2.5,
 clamped                IB bracket                   reward:risk
 0.3%–2%)               child orders                  >= 1.5x)
    │                                                     │
    │         once price moves favorably:                │
    │                                                     │
trailing stop                                    trailing profit
arms at +0.5%,                                   arms at +1%,
trails by ATR×1.2                                allows 40% giveback
(tightens the                                    from peak, locks in
 stop as price                                   the rest as price
 rises — can only                                runs further than
 move up, never down)                            the fixed target
```

**Why four and not just one stop and one target:** a fixed stop/target
pair is either too tight (you get stopped out by normal noise, or you
cap a winner that had more room to run) or too loose (you give back
more than necessary on a reversal). The trailing versions solve both
problems for their respective side — the trailing stop protects gains
once you have them without needing to predict in advance how far the
move will go, and the trailing profit-taker lets a strong trend keep
running past where a static take-profit would have closed it, while
still guaranteeing you keep a configurable fraction of the peak gain.

**Tick-level vs decision-bar-level:** `evaluate_tick()` runs on every
single tick (sub-second in liquid names), so a stop breach is acted on
immediately rather than waiting for the next 1-minute decision bar to
close. New entries and the AI's HOLD/BUY/SELL signal are still only
evaluated once per decision bar — that's the agent's actual reaction
speed, and trying to ask it to "decide" on every tick would mean
feeding it 1-minute-trained features on far-too-fresh data, which is a
different (harder) problem than what it was trained for.

---

## Why Real IB Bracket Orders, Not Just Python Logic

Every entry calls `BrokerExecutor.place_bracket_buy()`
(`core/broker.py`), which submits **three linked orders to IB**: the
entry, a STOP child, and a LIMIT take-profit child, with
One-Cancels-All linkage. Once IB acknowledges them, all three live on
IB's matching engine — not in this Python process's memory.

This is the direct answer to "protect against connection cut": if your
Mac sleeps, your wifi drops, the VPS reboots, or this script crashes,
the stop and target **continue to work**, because they're not waiting
on this code to evaluate anything. The Python-side `evaluate_tick()`
logic still runs in parallel and is what allows the *trailing*
stop/profit logic (which needs to react to new peaks) — every time it
moves the stop, `update_stop_price()` re-submits the new stop price to
IB too, so the exchange-side protection stays in sync with, not behind,
the in-memory trailing state.

---

## Why Risk-Based Position Sizing

"Max $50 loss on a $1,000 account" is a statement about **dollars**,
not about percent of cash deployed. `core/risk.py`'s
`compute_trade_plan()` works backward from that constraint:

```
risk_usd       = min(equity * RISK_PER_TRADE_PCT, MAX_RISK_PER_TRADE_USD)
stop_distance  = ATR * STOP_ATR_MULTIPLIER   (clamped to 0.3%–2% of price)
shares         = risk_usd / stop_distance
```

then applies secondary caps (available cash, `MAX_POSITION_PCT`,
`MAX_SHARES_PER_TRADE`) and takes the smallest result. In practice, on
a $1,000 account, the cash/position-size caps are usually the binding
constraint before the dollar-risk budget is even reached — meaning the
bot tends to risk noticeably *less* than the $50 ceiling on most
trades, not right up to it. That's intentional conservatism for small
accounts; as `INITIAL_CASH`/account equity grows, the risk-based sizer
increasingly becomes the binding constraint instead.

---

## Why ATR-Based Stops, Not Fixed Percent

A fixed 1% stop is too tight in a volatile session (noise stops you
out) and too loose in a calm one (you give back more than needed). ATR
(Average True Range) measures how much the stock is *actually* moving
right now, so `STOP_ATR_MULTIPLIER * ATR` adapts automatically.
`MIN_STOP_DISTANCE_PCT` / `MAX_STOP_DISTANCE_PCT` then clamp it so a
single freak volatility spike (or a moment of near-zero volatility)
can't produce an unreasonable stop in either direction.

---

## Data Flow: Three Speeds

| Layer | Source | Used for |
|---|---|---|
| Tick stream | `reqTickByTickData` (every trade print) or 5-sec bar fallback | Tick-level stop/target evaluation |
| Fast bars (5s) | Aggregated from ticks | ATR / volatility for stop sizing on entry |
| Decision bars (1min) | Aggregated from fast bars | PPO agent's 30-bar observation window |

All three live inside `core/data.py`'s `DataManager`, and all three are
fed by the same underlying IB event loop (`ib.sleep(1)` in
`trader.py`'s `run()` pumps every pending IB event — ticks, bar closes,
order status — through their respective callbacks each iteration).
There's no separate thread to manage; `ib_insync`'s event-driven model
handles concurrency internally.

---

## State Persistence Across Restarts

If the bot process restarts while a position is open, the position
itself and its protective bracket orders are unaffected (they live on
IB, see above) — but the bot's local `RiskManager.plan` (which tracks
the trailing-stop/trailing-profit ratchet state) is currently rebuilt
fresh on `setup()`, not restored from `STATE_PATH`. In practice this
means: after a restart, IB's resting stop order continues protecting
you at wherever the trailing logic last left it, but the *Python-side*
trailing ratchet effectively restarts its tracking from the current
price rather than from the pre-restart peak. For most users running
under `systemd`/`nohup` with infrequent restarts this is a minor edge
case; if you need bullet-proof state recovery across restarts, persist
`TradePlan` to `STATE_PATH` on every update and reload it in
`LiveTrader.setup()` — the hook points are already there
(`cfg.STATE_PATH` is reserved for this).

---

## Extending the Bot

- **New feature:** add it to `FeatureEngineer.compute()` in
  `core/features.py`, bump `N_FEATURES` in `core/config.py`, retrain
  from scratch (the observation dimension changes).
- **New exit rule:** add it inside `RiskManager.evaluate_tick()` —
  it's the single choke point all tick-level exits flow through.
- **Multiple symbols:** the bot is currently single-symbol
  (`MAX_CONCURRENT_POSITIONS = 1`, enforced in `validate_action()`).
  Multi-symbol would mean one `DataManager`/`RiskManager`/`TradePlan`
  per symbol, coordinated by `trader.py` — a meaningful rewrite, not a
  config flag.
- **New notification channel:** add a method to `Notifier`
  (`core/notify.py`) following the existing Telegram/email pattern.
