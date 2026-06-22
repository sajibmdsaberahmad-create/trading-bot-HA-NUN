# 🧠 Grandmaster Distillation Training on Google Colab

This guide covers training the **210M Teacher → 21M Student** distillation pipeline on Google Colab with enterprise GPUs (A100/L4).

## Why Colab?

- **Enterprise GPUs**: A100 40GB/80GB or L4 with Tensor cores
- **No local hardware limits**: Train massive 210M parameter models
- **Persistent storage**: Google Drive mounts keep trained weights
- **Free tier available**: Colab Pro for faster GPUs

---

## Prerequisites

1. **Google account** with Colab access
2. **Google Drive** with sufficient space (~5-10GB for models)
3. **GitHub repository** cloned locally
4. **Colab Pro** (recommended): For A100/L4 access and longer runtimes

---

## Step-by-Step Setup

### 1. Open the Colab Notebook

```bash
# From your local machine
colab_training.ipynb  # Opens in VS Code
# OR upload to Google Drive and open via drive.google.com
```

### 2. Mount Google Drive

```python
from google.colab import drive
drive.mount('/content/drive')
```

### 3. Clone Your Repository

```python
%cd /content/drive/MyDrive/
!git clone https://github.com/YOUR_GITHUB_USERNAME/trading-bot-HA-NUN.git
%cd trading-bot-HA-NUN
```

### 4. Install Dependencies

```bash
!pip install -r requirements.txt
```

### 5. Run Training

```bash
!python main.py --mode advanced-train --ppo-timesteps 1000000
```

### 6. Push Weights to GitHub

```bash
!git add models/*
!git commit -m "train: grandmaster distillation weights updated via Colab"
!git push origin main
```

---

## Training Configurations

### Grandmaster Distillation (210M → 21M)

```bash
!python main.py --mode advanced-train \
    --ticker SPY \
    --ppo-timesteps 1000000 \   # 1M for deep learning
    --epochs 50 \
    --device auto \
    --train-start 2020-01-01 \
    --train-end 2024-12-01 \
    --use-synthetic
```

### Multi-Timeframe Training

Train separate specialized models for each timeframe:

| Timeframe | Bar Size | PPO Timesteps | Use Case |
|-----------|----------|---------------|----------|
| 1min | `1 min` | 500K | Scalping |
| 5min | `5 mins` | 500K | Short-term swing |
| 1h | `1 hour` | 300K | Daily swing |
| 4h | `4 hours` | 300K | Multi-day |
| 1d | `1 day` | 200K | Position trading |

```python
TIMEFRAMES = [
    {"name": "1min_scalper", "timeframe": "1min", "ppo_timesteps": 500000},
    {"name": "1h_swing", "timeframe": "1h", "ppo_timesteps": 300000},
    {"name": "1d_position", "timeframe": "1d", "ppo_timesteps": 200000},
]

for tf in TIMEFRAMES:
    !python main.py --mode advanced-train \
        --timeframe {tf['timeframe']} \
        --ppo-timesteps {tf['ppo_timesteps']} \
        --use-synthetic
```

### LSE Training (London Stock Exchange)

```bash
!python main.py --mode advanced-train \
    --ticker ISF \      # iShares Core FTSE 100 ETF
    --lse \
    --timeframe 1h \
    --ppo-timesteps 300000 \
    --use-synthetic
```

---

## Model Architecture

The grandmaster distillation creates:

- **Teacher (210M)**: Large transformer with 8 layers, 768 dim, 12 heads
- **Student (21M)**: Distilled model capturing teacher's knowledge
- **PPO (1M params)**: Reinforcement learning fine-tuner
- **LSTM (500K params)**: Sequential pattern learner
- **Fusion Engine**: Combines all three model outputs

### Config Parameters

From `core/config.py`:

```python
GRANDMASTER_D_MODEL: int = 768          # Transformer dimension
GRANDMASTER_NUM_HEADS: int = 12         # Attention heads
GRANDMASTER_FFN_DIM: int = 3072         # Feed-forward dimension
GRANDMASTER_NUM_LAYERS: int = 8         # Transformer layers
DISTILLATION_TEMPERATURE: float = 3.0   # Softmax temperature
DISTILLATION_ALPHA: float = 0.4         # Distillation weight
```

---

## GPU Memory Management

Colab GPUs have limited VRAM. The notebook handles:

- **Automatic device detection**: CUDA > MPS > CPU
- **Batch size tuning**: Adjusts based on GPU memory
- **Checkpointing**: Saves best models to prevent loss
- **Garbage collection**: Frees memory between phases

### Monitoring GPU Usage

```python
import torch
print(torch.cuda.memory_summary())
```

### Reducing Memory Usage

If you hit OOM errors:

```python
# In TrainingConfig
batch_size: int = 32  # Reduce from 64
transformer_d_model: int = 128  # Reduce from 256
ppo_batch_size: int = 128  # Reduce from 256
```

---

## Output Files

After training, these files are generated:

```
models/
├── ppo_trader.zip                 # PPO agent weights
├── transformer_model.pth          # Transformer weights
├── transformer_model_best.pth     # Best checkpoint
├── lstm_model.h5                  # LSTM weights
├── fusion_state.json              # Fusion engine state
├── training_history.json          # Training metrics
├── checkpoints/                   # Periodic checkpoints
└── backups/                       # Model version backups
```

---

## Pushing to GitHub

### Method 1: Automatic (with token)

```python
import os
token = os.getenv('GITHUB_TOKEN', '')
if token:
    !git push https://{token}@github.com/USERNAME/trading-bot-HA-NUN.git main
```

### Method 2: Manual SSH

```bash
!git remote set-url origin git@github.com:USERNAME/trading-bot-HA-NUN.git
!git push origin main
```

### Method 3: Manual HTTPS

```bash
!git push origin main
# Enter credentials when prompted
```

---

## Multi-Market Deployment

After training, the same model can trade:

### US Equities (SPY, QQQ)
```bash
python main.py --mode scalper --ticker SPY
```

### LSE Equities (ISF, VOD, BP)
```bash
python main.py --mode scalper --ticker ISF --lse
```

### Different Timeframes
```bash
python main.py --mode scalper --ticker SPY --timeframe 1h    # Swing
python main.py --mode scalper --ticker SPY --timeframe 1d    # Position
```

---

## Troubleshooting

### Out of Memory (OOM)

- Reduce `batch_size` in `TrainingConfig`
- Reduce `transformer_d_model`
- Use smaller `ppo_timesteps`
- Enable gradient checkpointing

### Slow Training

- Verify GPU is being used: `!nvidia-smi`
- Use Colab Pro for A100/L4 GPUs
- Reduce `epochs` for faster iterations

### Git Push Failures

- Ensure you have push access to the repo
- Use a GitHub token: `https://<TOKEN>@github.com/...`
- Check git remote: `!git remote -v`

### Data Fetch Failures

- Use `--use-synthetic` flag for synthetic data
- IB Gateway must be running for live data
- Check IB Gateway port and credentials

---

## Advanced: Custom Training Runs

### Custom Hyperparameter Sweep

```python
for lr in [1e-4, 2e-4, 5e-4]:
    for temp in [2.0, 3.0, 5.0]:
        print(f"Training: lr={lr}, temp={temp}")
        !python main.py --mode advanced-train \
            --ppo-timesteps 100000 \
            --epochs 20 \
            --device auto \
            --train-start 2020-01-01 \
            --train-end 2023-01-01 \
            --use-synthetic
```

### Resume from Checkpoint

```python
!python main.py --mode advanced-train \
    --ppo-timesteps 500000 \
    --resume-from models/checkpoints/latest.pth
```

---

## Next Steps

1. Train on all timeframes (1min, 5min, 1h, 4h, 1d)
2. Train on LSE instruments (ISF, VOD, BP)
3. Run fusion backtest to validate
4. Push all weights to GitHub
5. Deploy live trading with `--mode scalper`

---

**Note**: Always paper trade (`--port 7497`) before live trading (`--port 7496`). The AI requires extensive validation before real money deployment.