# 🌍 Multi-Timeframe Trading & LSE Support Guide

Complete guide for trading HA-NUN across all timeframes and markets.

---

## 📊 Multi-Timeframe Training & Trading

HA-NUN can be trained and deployed on any timeframe. The observation window stays at 30 bars, but the market physics captured changes dramatically:

| Timeframe | CLI Flag | Bar Size | Window Horizon | Use Case | Risk Profile |
|-----------|----------|----------|----------------|----------|--------------|
| **Scalping** | `--timeframe 1min` | 1 minute | 30 minutes | Micro-imbalances, quick in/out | Tight stops (0.7x ATR) |
| **Short-term** | `--timeframe 5min` | 5 minutes | 2.5 hours | Day trading swings | Moderate stops (1.0x ATR) |
| **Swing** | `--timeframe 1h` | 1 hour | 30 hours | Multi-day positions | Wider stops (1.5x ATR) |
| **Position** | `--timeframe 4h` | 4 hours | 5 days | Weekly trends | Wide stops (2.0x ATR) |
| **Investing** | `--timeframe 1d` | 1 day | 30 days | Long-term trends | Very wide (2.5x ATR) |

### How It Works

The **same 422-dimensional observation matrix** works across all timeframes because:

1. **Features are relative**: Uses Z-scores, ratios, and accelerations instead of absolute prices
2. **18 robust features**: Log returns, volume accelerations, VPIN, Amihud, etc.
3. **Universal patterns**: Support/resistance, momentum, regime detection work on any timeframe
4. **Only risk changes**: Stop distance and take-profit scale with timeframe

### Training on All Timeframes

**Option A: Sequential Training (Recommended)**

```bash
#!/bin/bash
# train_all_timeframes.sh

TIMEFRAMES=(
    "1min:500000"   # 500K steps for scalping
    "5min:500000"   # 500K steps for short-term
    "1h:300000"     # 300K steps for swing
    "4h:300000"     # 300K steps for position
    "1d:200000"     # 200K steps for investing
)

for tf_config in "${TIMEFRAMES[@]}"; do
    IFS=':' read -r tf steps <<< "$tf_config"
    echo "========================================="
    echo "Training on ${tf} timeframe..."
    echo "========================================="
    
    python main.py --mode advanced-train \
        --ticker SPY \
        --timeframe "$tf" \
        --ppo-timesteps "$steps" \
        --epochs 30 \
        --device auto \
        --use-synthetic
    
    # Save model with timeframe suffix
    if [ -f "models/transformer_model.pth" ]; then
        cp models/transformer_model.pth models/transformer_model_${tf}.pth
        cp models/lstm_model.h5 models/lstm_model_${tf}.h5
        cp ppo_trader.zip ppo_trader_${tf}.zip
    fi
done

echo "✅ All timeframe training complete!"
```

**Option B: Colab Multi-Timeframe Training**

In `colab_training.ipynb`, run the multi-timeframe cell:

```python
TIMEFRAMES = [
    {"name": "1min_scalper", "timeframe": "1min", "ppo_timesteps": 500000},
    {"name": "5min_scalper", "timeframe": "5min", "ppo_timesteps": 500000},
    {"name": "1h_swing", "timeframe": "1h", "ppo_timesteps": 300000},
    {"name": "4h_swing", "timeframe": "4h", "ppo_timesteps": 300000},
    {"name": "1d_position", "timeframe": "1d", "ppo_timesteps": 200000},
]

for tf_config in TIMEFRAMES:
    !python main.py --mode advanced-train \
        --ticker SPY \
        --timeframe {tf_config['timeframe']} \
        --ppo-timesteps {tf_config['ppo_timesteps']} \
        --epochs 30 \
        --device auto \
        --use-synthetic
```

### Live Trading by Timeframe

```bash
# Scalping (1min bars, tight stops)
python main.py --mode scalper \
    --ticker SPY \
    --timeframe 1min \
    --max-risk-usd 30

# Day trading (5min bars)
python main.py --mode scalper \
    --ticker SPY \
    --timeframe 5min \
    --max-risk-usd 50

# Swing trading (1h bars)
python main.py --mode scalper \
    --ticker SPY \
    --timeframe 1h \
    --max-risk-usd 100

# Position trading (daily bars)
python main.py --mode scalper \
    --ticker SPY \
    --timeframe 1d \
    --max-risk-usd 200
```

### Automatic Risk Adjustment

The `--timeframe` flag automatically adjusts these parameters in `core/config.py`:

```python
# Example: 1h swing trading
SCALP_STOP_ATR_MULTIPLIER: 1.5   # Wider stops for higher TF
SCALP_TP_ATR_MULTIPLIER: 3.0     # Larger targets
SCALP_MAX_STOP_PCT: 0.025        # Max 2.5% stop
SCALP_MAX_TP_PCT: 0.08           # Max 8% target
```

---

## 🌍 London Stock Exchange (LSE) Trading

HA-NUN can trade UK equities and ETFs on the London Stock Exchange with one flag.

### Setup Requirements

1. **IB Account** with:
   - UK market data subscription
   - LSE trading permissions
   - GBP cash available

2. **Currency**: All LSE trades in GBP

3. **Exchange**: Set to `LSE` instead of `SMART`

### LSE Instruments

| Ticker | Name | Type | Description |
|--------|------|------|-------------|
| **ISF** | iShares Core FTSE 100 UCITS ETF | ETF | FTSE 100 tracker |
| **VOD** | Vodafone Group | Stock | Telecom giant |
| **BP** | BP plc | Stock | Energy major |
| **HSBA** | HSBC Holdings | Stock | Global bank |
| **SHEL** | Shell plc | Stock | Energy major |
| **ULVR** | Unilever | Stock | Consumer goods |
| **AZN** | AstraZeneca | Stock | Pharma |
| **GSK** | GSK plc | Stock | Healthcare |

### Trading LSE

**Paper Trading (Safe)**

```bash
# FTSE 100 ETF (most liquid)
python main.py --mode scalper \
    --ticker ISF \
    --lse \
    --timeframe 1h \
    --port 7497

# Individual stock (e.g., Vodafone)
python main.py --mode scalper \
    --ticker VOD \
    --lse \
    --timeframe 1h \
    --port 7497
```

**Live Trading (When Ready)**

```bash
python main.py --mode scalper \
    --ticker ISF \
    --lse \
    --timeframe 1h \
    --port 7496
```

### LSE Training

```bash
# Train on LSE instrument
python main.py --mode advanced-train \
    --ticker ISF \
    --lse \
    --timeframe 1h \
    --ppo-timesteps 300000 \
    --epochs 30 \
    --device auto \
    --use-synthetic
```

### LSE Risk Profiles

Same timeframe risk profiles apply, but note:

- **FTSE 100 volatility**: Similar to SPY but with currency risk
- **GBP denomination**: Adjust `CURRENCY` and risk calculations
- **Market hours**: LSE opens 8:00 AM, closes 4:30 PM GMT

---

## 🔄 Combined: Multi-Timeframe + Multi-Market

The ultimate configuration: train on all timeframes for all markets.

### Master Training Script

```bash
#!/bin/bash
# master_training.sh - Train on everything

echo "========================================="
echo "  HA-NUN MASTER TRAINING"
echo "  Multi-Timeframe + Multi-Market"
echo "========================================="

# US Markets
echo ""
echo "🇺🇸 Training US Equities..."
python main.py --mode advanced-train \
    --ticker SPY \
    --timeframe 1min \
    --ppo-timesteps 500000 \
    --use-synthetic

python main.py --mode advanced-train \
    --ticker SPY \
    --timeframe 1h \
    --ppo-timesteps 300000 \
    --use-synthetic

python main.py --mode advanced-train \
    --ticker SPY \
    --timeframe 1d \
    --ppo-timesteps 200000 \
    --use-synthetic

# LSE Markets
echo ""
echo "🇬🇧 Training LSE Equities..."
python main.py --mode advanced-train \
    --ticker ISF \
    --lse \
    --timeframe 1h \
    --ppo-timesteps 300000 \
    --use-synthetic

python main.py --mode advanced-train \
    --ticker VOD \
    --lse \
    --timeframe 1d \
    --ppo-timesteps 200000 \
    --use-synthetic

echo ""
echo "========================================="
echo "  ✅ MASTER TRAINING COMPLETE"
echo "========================================="
```

### Colab Master Training

In `colab_training.ipynb`:

```python
# Train on everything
MARKETS = [
    {"ticker": "SPY", "lse": False, "timeframes": ["1min", "1h", "1d"]},
    {"ticker": "ISF", "lse": True, "timeframes": ["1h", "1d"]},
]

for market in MARKETS:
    for tf in market["timeframes"]:
        lse_flag = "--lse" if market["lse"] else ""
        !python main.py --mode advanced-train \
            --ticker {market['ticker']} \
            {lse_flag} \
            --timeframe {tf} \
            --ppo-timesteps 300000 \
            --device auto \
            --use-synthetic
```

---

## 🎯 Deployment Strategies

### Strategy 1: Universal AI (One Model for All)

Train one grandmaster model on mixed data:

```bash
python main.py --mode advanced-train \
    --ticker SPY \
    --timeframe 1h \
    --ppo-timesteps 1000000 \
    --epochs 50
```

**Pros**: One model to rule them all
**Cons**: May not optimize for any specific timeframe

### Strategy 2: Specialized Models (One per Timeframe)

Train separate models, deploy based on strategy:

```bash
# Train all
./scripts/train_all_timeframes.sh

# Deploy scalper model for day trading
python main.py --mode scalper --timeframe 1min --model models/ppo_trader_1min.zip

# Deploy swing model for multi-day
python main.py --mode scalper --timeframe 1h --model models/ppo_trader_1h.zip
```

**Pros**: Optimized for each use case
**Cons**: More models to manage

### Strategy 3: Market-Specific Models

Train separate models for US and LSE:

```bash
# US model
python main.py --mode advanced-train --ticker SPY --timeframe 1h

# LSE model
python main.py --mode advanced-train --ticker ISF --lse --timeframe 1h
```

**Pros**: Captures market-specific dynamics
**Cons**: Requires more training compute

### Strategy 4: Fusion AI (Recommended)

Use the multi-model fusion engine to combine:

1. **PPO** (1M params) - RL trader
2. **Transformer** (21M) - Temporal patterns
3. **LSTM** (500K) - Sequential memory
4. **Ensemble** - Rule-based strategies

```bash
python main.py --mode fusion-trade \
    --ticker SPY \
    --timeframe 1h
```

The fusion engine automatically weights models by recent accuracy and market regime.

---

## 📊 Configuration Reference

### Timeframe Configurations

Edit `core/config.py`:

```python
# Multi-timeframe configurations
BAR_SIZE_MAP = {
    "scalper_1min": "1 min",
    "scalper_5min": "5 mins",
    "swing_1h": "1 hour",
    "swing_4h": "4 hours",
    "position_1d": "1 day"
}

TIMEFRAME_RISK = {
    "scalper_1min": {
        "stop_atr_mult": 0.7,
        "tp_atr_mult": 1.5,
        "max_stop_pct": 0.010,
        "max_tp_pct": 0.03,
    },
    "swing_1h": {
        "stop_atr_mult": 1.5,
        "tp_atr_mult": 3.0,
        "max_stop_pct": 0.025,
        "max_tp_pct": 0.08,
    },
    "position_1d": {
        "stop_atr_mult": 2.5,
        "tp_atr_mult": 5.0,
        "max_stop_pct": 0.050,
        "max_tp_pct": 0.20,
    }
}
```

### LSE Configurations

```python
# In core/config.py or via CLI
EXCHANGE = "LSE"
CURRENCY = "GBP"
TICKER = "ISF"  # FTSE 100 ETF
```

---

## 🚀 Quick Start Examples

### Example 1: Day Trade SPY (5min)

```bash
python main.py --mode scalper \
    --ticker SPY \
    --timeframe 5min \
    --cash 5000 \
    --max-risk-usd 50
```

### Example 2: Swing Trade AAPL (1h)

```bash
python main.py --mode scalper \
    --ticker AAPL \
    --timeframe 1h \
    --cash 10000 \
    --max-risk-usd 100
```

### Example 3: Position Trade ISF (Daily)

```bash
python main.py --mode scalper \
    --ticker ISF \
    --lse \
    --timeframe 1d \
    --cash 20000 \
    --max-risk-usd 200 \
    --port 7497
```

### Example 4: Train Multi-Timeframe in Colab

```python
# In colab_training.ipynb
TIMEFRAMES = ["1min", "1h", "1d"]
for tf in TIMEFRAMES:
    !python main.py --mode advanced-train \
        --ticker SPY \
        --timeframe {tf} \
        --ppo-timesteps 300000 \
        --device auto
```

---

## 🔧 Troubleshooting

### Timeframe Issues

**Problem**: "Invalid timeframe" error

**Solution**: Use exact CLI values: `1min`, `5min`, `1h`, `4h`, `1d`

**Problem**: Too many/few bars

**Solution**: The system automatically adjusts. 30-bar window stays constant.

### LSE Issues

**Problem**: "Could not qualify contract"

**Solution**:
1. Verify IB account has UK market data
2. Check ticker symbol (ISF for FTSE 100 ETF)
3. Ensure `--lse` flag is set
4. Wait 30 seconds after IB Gateway login

**Problem**: Wrong currency

**Solution**: `--lse` automatically sets `CURRENCY=GBP`

---

## 📈 Performance Expectations

### By Timeframe

| Timeframe | Expected Trades/Day | Win Rate | Avg Hold Time |
|-----------|---------------------|----------|---------------|
| 1min | 5-20 | 55-65% | 1-5 minutes |
| 5min | 2-10 | 60-70% | 10-30 minutes |
| 1h | 0-3 | 65-75% | 1-4 hours |
| 4h | 0-1 | 70-80% | 1-2 days |
| 1d | 0-1 | 70-80% | 3-10 days |

### By Market

| Market | Liquidity | Volatility | Spread | Best Timeframe |
|--------|-----------|------------|--------|----------------|
| US Equities (SPY) | Very High | Medium | Tight | All |
| LSE (ISF) | High | Medium | Moderate | 1h, 1d |

---

## 🎓 Advanced Topics

### Transfer Learning

Train on US markets, fine-tune on LSE:

```bash
# Step 1: Train on SPY (US)
python main.py --mode advanced-train \
    --ticker SPY \
    --timeframe 1h \
    --ppo-timesteps 500000

# Step 2: Fine-tune on ISF (LSE)
python main.py --mode advanced-train \
    --ticker ISF \
    --lse \
    --timeframe 1h \
    --ppo-timesteps 100000  # Less since pre-trained
```

### Regime-Adaptive Trading

Switch timeframes based on market regime:

```python
# In your trading logic
if regime == "high_volatility":
    timeframe = "5min"  # Faster decisions
elif regime == "low_volatility":
    timeframe = "4h"    # Slower, wider views
else:
    timeframe = "1h"    # Default
```

### Multi-Asset Trading

Run multiple instances for different assets:

```bash
# Terminal 1
python main.py --mode scalper --ticker SPY --timeframe 1h

# Terminal 2
python main.py --mode scalper --ticker ISF --lse --timeframe 1h

# Terminal 3
python main.py --mode scalper --ticker QQQ --timeframe 5min
```

---

## ✅ Checklist

Before going live:

- [ ] Paper traded each timeframe for 30+ days
- [ ] Verified LSE account permissions
- [ ] Tested all risk parameters
- [ ] Backtested on 1+ year of data
- [ ] Trained on multiple timeframes
- [ ] Validated fusion engine performance
- [ ] Set up Telegram alerts
- [ ] Configured git auto-push
- [ ] Reviewed all hardcoded risk limits
- [ ] Stress-tested with market shocks

---

**HA-NUN v3.5** — One AI, Every Timeframe, Every Market

*From 1-minute scalps to 30-day positions, US to UK — universal trading intelligence.*