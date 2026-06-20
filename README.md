# PPO Trading Bot — Risk-Managed Edition

A PPO (reinforcement learning) trading bot for Interactive Brokers,
built so the AI only ever decides *when* to be in a trade — position
size, stop-loss, take-profit, and every circuit breaker are computed
deterministically by a separate, hardcoded risk engine that the AI
cannot override.

**Start here:**
- New to this bot? → [`docs/LAUNCH_GUIDE.md`](docs/LAUNCH_GUIDE.md) — setup, IB Gateway, Telegram alerts, Mac → VPS deployment
- Want to understand the AI? → [`docs/TRAINING_GUIDE.md`](docs/TRAINING_GUIDE.md) — features, PPO, online fine-tuning, tuning
- Want to understand the code? → [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module map, the exit system, why each design choice was made

## What's in this version

- **Risk-based position sizing**: trade size is computed from a dollar
  risk budget (default $50 on a $1,000 account) and current
  volatility — not from a fixed % of cash.
- **Four-layer exit system**: hard stop-loss, ATR-based trailing
  stop-loss, predictive hard take-profit, and a trailing profit-taker
  that lets winners run further while still locking in gains.
- **Real IB bracket orders**: stops and targets are placed as live
  orders on IB's servers, so they keep working even if your machine
  loses connection.
- **Tick-level monitoring**: stop/target checks run on every market
  tick (sub-second in liquid names), not just once a minute.
- **14 engineered features** (11 original + 3 predictive: trend
  strength, mean-reversion distance, volatility-expansion ratio).
- **Telegram notifications** for every trade, stop trigger, risk halt,
  reconnect, error, and a daily summary.
- **Auto-scaling**: risk and sizing recalculate from live account
  equity, so the same config works at $1,000 or $50,000.
- **Cross-platform**: auto-detects Apple Metal / NVIDIA CUDA / CPU;
  identical code runs on macOS now and a Linux VPS later.

## Quick start

```bash
pip install -r requirements.txt
python main.py --mode warmup           # train once, ~20-40 min
python main.py --mode trade            # paper trade live
```

Full instructions, including IB Gateway and Telegram setup, are in
[`docs/LAUNCH_GUIDE.md`](docs/LAUNCH_GUIDE.md).

## Disclaimer

This is for educational and paper-trading purposes. Algorithmic
trading involves substantial risk of financial loss. Past backtest or
paper-trading performance does not guarantee future results. Never
risk capital you cannot afford to lose. The hardcoded risk engine
limits how much the bot *can* lose per trade and per day — it does not
make losses impossible.
