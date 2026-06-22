# HA-NUN Trading Bot

**HA-NUN** (High-frequency Algorithmic Neural Network for Ultra-Short-term trading) is an institutional-grade AI trading system that combines:
- PPO reinforcement learning
- Transformer temporal fusion
- LSTM sequential patterns
- Multi-model fusion engine
- Real-time market regime detection
- 210M → 21M Grandmaster Distillation

## 🚀 Key Features

### Multi-Timeframe Training
Train one AI that understands every timeframe:
- **1min/5min**: Scalping micro-imbalances
- **1h/4h**: Multi-day swing trading
- **1d**: Long-term position investing

### Multi-Market Support
- **US Equities**: SPY, QQQ, penny stock universe
- **LSE**: FTSE 100 (ISF), UK equities (VOD, BP, HSBA)
- **Automatic currency/exchange switching**

### Grandmaster Distillation (Colab)
- Train a **210M parameter teacher** on Google Colab GPUs (A100/L4)
- Distill knowledge into a **21M student** for live trading
- Persistent weights via Google Drive
- Auto-push to GitHub

### Self-Evolving AI
- **Off-hours training**: Learns from daily trades
- **Dynamic risk adjustment**: Adapts stop/take-profit to market conditions
- **Self-improvement guidelines**: Auto-tunes strategy weights
- **Consciousness module**: Reflects on performance

## 📁 Project Structure

```
trading-bot-HA-NUN/
├── main.py                        # CLI entry point
├── colab_training.ipynb           # Google Colab training notebook
├── core/
│   ├── config.py                  # All parameters (single source of truth)
│   ├── connector.py               # IB Gateway connection
│   ├── scalper_runner.py          # HA-NUN live trading loop
│   ├── advanced_training.py       # Multi-model training pipeline
│   ├── transformer_model.py       # Transformer architecture
│   ├── lstm_model.py              # LSTM architecture
│   ├── multi_model_fusion.py      # Fusion engine
│   ├── features_enhanced.py       # 18-feature engineering
│   ├── risk.py                    # Hard risk guardrails
│   └── git_sync.py                # Auto-commit/push
├── models/                        # Trained weights
│   ├── ppo_trader.zip
│   ├── transformer_model.pth
│   ├── lstm_model.h5
│   └── training_history.json
└── docs/
    ├── COLAB_TRAINING.md          # Colab training guide
    ├── LAUNCH_GUIDE.md            # Full setup instructions
    └── ARCHITECTURE.md            # System design
```

## 🎯 Quick Start

### Option A: Paper Trading (Local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run HA-NUN scalper
python main.py --mode scalper

# 3. Trade specific ticker
python main.py --mode scalper --ticker SPY

# 4. Use custom timeframe
python main.py --mode scalper --ticker SPY --timeframe 1h
```

### Option B: Grandmaster Training (Colab)

```bash
# 1. Open colab_training.ipynb in Google Colab
# 2. Mount Google Drive
# 3. Clone repo and install dependencies
# 4. Run training cell

!python main.py --mode advanced-train \
    --ticker SPY \
    --ppo-timesteps 1000000 \
    --epochs 50 \
    --device auto
```

## 📊 CLI Commands

### Trading Modes

```bash
# HA-NUN institutional scalper (recommended)
python main.py --mode scalper

# Advanced multi-model training
python main.py --mode advanced-train --ppo-timesteps 500000

# Fusion backtest
python main.py --mode fusion-backtest --bt-bars 1000

# Fusion live trading
python main.py --mode fusion-trade
```

### Multi-Timeframe Trading

```bash
# Scalping (1min bars)
python main.py --mode scalper --ticker SPY --timeframe 1min

# Swing trading (1h bars)
python main.py --mode scalper --ticker SPY --timeframe 1h

# Position trading (daily bars)
python main.py --mode scalper --ticker SPY --timeframe 1d
```

### LSE Trading

```bash
# Trade FTSE 100 ETF on London Stock Exchange
python main.py --mode scalper --ticker ISF --lse
```

### Training Options

```bash
# Basic training
python main.py --mode advanced-train --ticker SPY

# Custom timesteps and epochs
python main.py --mode advanced-train \
    --ticker SPY \
    --ppo-timesteps 1000000 \
    --epochs 50 \
    --train-start 2020-01-01 \
    --train-end 2024-12-01

# Use synthetic data (no IB Gateway needed)
python main.py --mode advanced-train --use-synthetic

# Specify device
python main.py --mode advanced-train --device cuda  # or cpu, mps, auto
```

## 🧠 AI Architecture

### Three-Model System

1. **PPO (1M params)**: Reinforcement learning trader
   - Trained on historical environment
   - Outputs: Buy/Sell/Hold + position size

2. **Transformer (210M teacher → 21M student)**: Temporal pattern recognition
   - Attention over 30-bar history
   - Distilled from grandmaster teacher
   - Outputs: Action probabilities + value estimates

3. **LSTM (500K params)**: Sequential memory
   - Bidirectional with attention
   - Learns long-range dependencies
   - Outputs: Directional bias

### Fusion Engine

Combines all three models with:
- **Regime classifier**: Bull/Bear/Chop/Shock detection
- **Confidence scorer**: Weights models by recent accuracy
- **Ensemble voting**: Democratic decision making
- **Dynamic trailing**: AI-adjusted profit protection

## 🛡️ Risk Management

### Hard Guardrails (AI cannot override)

```python
MAX_RISK_PER_TRADE_USD: float = 50.0      # Hard $50 limit
MAX_DAILY_LOSS_PCT: float = 0.03           # 3% daily loss halt
MAX_CONSECUTIVE_LOSSES: int = 4            # Cool-off after 4 losses
MAX_STOP_DISTANCE_PCT: float = 0.02        # Max 2% stop
MIN_REWARD_RISK_RATIO: float = 1.5         # Min 1.5:1 RR
```

### Dynamic Adjustments (AI-controlled)

```python
SCALP_STOP_ATR_MULTIPLIER: float = 0.7    # Base stop distance
TRAILING_STOP_ENABLED: bool = True
DYNAMIC_PROFIT_GIVEBACK_MIN: float = 0.20  # Tight
DYNAMIC_PROFIT_GIVEBACK_MAX: float = 0.50  # Loose
```

## 🔧 Configuration

Edit `core/config.py` for permanent changes:

```python
# Instrument
TICKER: str = "SPY"
EXCHANGE: str = "SMART"  # or "LSE"
CURRENCY: str = "USD"    # or "GBP"

# Timeframe
TRADING_TIMEFRAME: str = "1min"  # 1min, 5min, 1h, 4h, 1d

# Risk
RISK_PER_TRADE_PCT: float = 0.05  # 5% of equity
MAX_RISK_PER_TRADE_USD: float = 50.0

# Training
PPO_TIMESTEPS: int = 500_000
WARMUP_TIMESTEPS: int = 1_000_000
```

Or use CLI flags for one-off runs:

```bash
python main.py --mode scalper --risk-pct 0.03 --max-risk-usd 30
```

## 🌍 LSE Setup

### Prerequisites

1. IB account with UK market data subscription
2. Sufficient GBP cash in account
3. LSE trading permissions enabled

### Trading LSE

```bash
# Paper trade FTSE 100 ETF
python main.py --mode scalper --ticker ISF --lse --port 7497

# Live trade (when ready)
python main.py --mode scalper --ticker ISF --lse --port 7496
```

### LSE Risk Profiles

The `--timeframe` flag auto-adjusts risk for LSE:

| Timeframe | Stop ATR | TP ATR | Use Case |
|-----------|----------|--------|----------|
| 1min | 0.7x | 1.5x | Micro-scalp |
| 1h | 1.5x | 3.0x | Swing |
| 1d | 2.5x | 5.0x | Position |

## 📈 Training Workflows

### Full Grandmaster Distillation (Colab)

1. **Mount Drive**: Save weights persistently
2. **Clone Repo**: Sync with GitHub
3. **Install Dependencies**: PyTorch, Stable-Baselines3, TensorFlow
4. **Train 210M Teacher**: Large transformer on GPU
5. **Distill to 21M Student**: Knowledge transfer
6. **Train PPO + LSTM**: Separate models
7. **Fusion Calibration**: Weight models
8. **Push to GitHub**: All weights committed

See `colab_training.ipynb` and `docs/COLAB_TRAINING.md`.

### Multi-Timeframe Training

Train specialized models for each timeframe:

```bash
# 1min scalper
python main.py --mode advanced-train --timeframe 1min --ppo-timesteps 500000

# 1h swing
python main.py --mode advanced-train --timeframe 1h --ppo-timesteps 300000

# Daily position
python main.py --mode advanced-train --timeframe 1d --ppo-timesteps 200000
```

### LSE Training

```bash
python main.py --mode advanced-train \
    --ticker ISF \
    --lse \
    --timeframe 1h \
    --ppo-timesteps 300000
```

## 🔔 Notifications

Configure in `core/config.py`:

```python
TELEGRAM_ENABLED: bool = True
TELEGRAM_BOT_TOKEN: str = os.getenv("TRADING_BOT_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID: str = os.getenv("TRADING_BOT_TELEGRAM_CHAT_ID")
```

Alerts for:
- Trade opens/closes
- Stop/target hits
- Risk halts
- Daily summaries
- Training completions

## 📊 Monitoring

### Live Metrics

`live_metrics.json` updates every 2 seconds:

```json
{
  "mode": "HA-NUN",
  "account_equity": 1050.00,
  "nav": 1050.00,
  "position": "50 SPY",
  "win_rate": 65.0,
  "trades_today": 3,
  "top_pick": "AAPL",
  "scan_results": [...]
}
```

### Trade Journal

`models/trade_journal.jsonl` records every trade:

```json
{
  "ticker": "SPY",
  "entry": 450.00,
  "exit": 455.00,
  "shares": 100,
  "pnl_usd": 500.00,
  "result": "win"
}
```

## 🧬 Self-Improvement

The AI evolves via:

1. **Daily self-training**: Adjusts weights based on win/loss history
2. **Guideline generation**: Suggests parameter tweaks
3. **Consciousness reflection**: Meta-learning on performance
4. **Experience buffer**: Unified learning from all trades

Files auto-generated:
- `models/scalper_weights.json` - Learned scoring weights
- `models/daily_guidelines.txt` - Daily strategy adjustments
- `models/training_history.json` - Model metrics

## 🔄 Git Integration

Auto-commits:
- Model weights after each training run
- Daily summaries at market close
- Trade journals and experience buffers
- Init/shutdown reports

Configure in `core/config.py`:

```python
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "")
MAX_GIT_PUSH_RETRIES: int = 3
```

## 📚 Documentation

- `docs/LAUNCH_GUIDE.md` — Full setup tutorial
- `docs/ARCHITECTURE.md` — System design deep-dive
- `docs/COLAB_TRAINING.md` — Colab grandmaster training
- `docs/TRAINING_GUIDE.md` — Model training reference

## ⚠️ Disclaimer

This software is for **educational purposes only**. Trading real money involves substantial risk. Always:

1. Paper trade for 30+ days before live
2. Start with small capital
3. Never risk more than you can afford to lose
4. Understand the code before running

## 📄 License

Proprietary. All rights reserved.

---

**HA-NUN v3.5** — Institutional AI Trading System

*Evolving intelligence for universal market domination.*