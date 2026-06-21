# Model Versioning & Preservation Runbook

## Overview

This project uses a **code-only Git workflow** for source control and **binary-safe external registries** for model weights. This keeps the repo fast, clean, and within GitHub's limits while still preserving every trained artifact.

## What Lives Where

| Artifact | Location | Why |
|---|---|---|
| Source code, configs, training scripts | Git repo (`trading-bot-HA-NUN`) | Text diffs, collaboration, CI |
| Model binaries (`.zip`, `.pth`, `.h5`) | GitHub Releases + Hugging Face Hub | Large binary storage, versioned assets |
| Training logs / manifests | Git repo (`models/model_manifest.json`, `training_history_*.json`) | Lightweight metadata for reproducibility |
| Experience buffer / large data | `.gitignore`d | Too large for Git; can be archived to Logs repo if needed |

## The Golden Rule

**Never commit raw model weights to Git.** Every model binary must be preserved via:
1. **GitHub Releases** — tag-based asset uploads (`grandmaster-latest`, `grandmaster-v1`, etc.)
2. **Hugging Face Hub** — primary model registry, supports `git-xet` for large files

## How Preserve Works

### Automatic (preferred)

After every `AdvancedTrainingPipeline.run_all()` completion, the pipeline automatically calls `core.model_preservation.preserve_all(...)`. It will:
- Create `models/model_manifest.json` with SHA-256 checksums for every artifact
- Upload to **GitHub Releases** if `GITHUB_TOKEN` + `GITHUB_REPO` are set
- Upload to **Hugging Face Hub** if `HF_TOKEN` + `HF_REPO_ID` are set

### Manual

```bash
python run_preserve.py \
  --tag grandmaster-v1 \
  --repo sajibmdsaberahmad-create/trading-bot-HA-NUN \
  --github-token $GITHUB_TOKEN \
  --hf-repo-id sajibmdsaberahmad-create/trading-bot-HA-NUN \
  --hf-token $HF_TOKEN
```

## Environment Variables

Set these in your shell or `.env` (`.env` is gitignored):

```bash
# GitHub
export GITHUB_TOKEN="ghp_..."
export GITHUB_REPO="sajibmdsaberahmad-create/trading-bot-HA-NUN"

# Hugging Face
export HF_TOKEN="hf_..."
export HF_REPO_ID="sajibmdsaberahmad-create/trading-bot-HA-NUN"
```

## Required Packages

```bash
pip install huggingface_hub requests
```

For Hugging Face `git-xet` workflow (optional but recommended for >GB models):

```bash
git xet install
```

## Step-by-Step Preservation

### 1. Train Models

```python
from core.advanced_training import AdvancedTrainingPipeline, TrainingConfig

cfg = TrainingConfig(
    ticker='SPY',
    train_start='2020-01-01',
    train_end='2024-06-01',
    val_start='2024-06-01',
    val_end='2024-12-01',
    test_start='2025-01-01',
    test_end='2025-06-01',
    ppo_timesteps=500_000,
    epochs=20,
    run_backtest=True,
    device='auto',
)

pipeline = AdvancedTrainingPipeline(cfg)
results = pipeline.run_all()
```

### 2. Verify Manifest

```bash
cat models/model_manifest.json
```

### 3. Check GitHub Releases

Visit: `https://github.com/sajibmdsaberahmad-create/trading-bot-HA-NUN/releases`

You should see a `grandmaster-latest` release with assets:
- `ppo_trader.zip`
- `models/transformer_model.pth`
- `models/lstm_model.h5`
- `models/fusion_state.json`

### 4. Check Hugging Face Hub

```bash
huggingface-cli repo-info sajibmdsaberahmad-create/trading-bot-HA-NUN --repo-type model
```

## Model Naming Conventions

| Model | Path | Description |
|---|---|---|
| PPO warm-start | `ppo_trader.zip` | Stable-Baselines3 PPO weights |
| Transformer best | `models/transformer_model.pth` | PyTorch state dict |
| LSTM final | `models/lstm_model.h5` | Keras H5 weights |
| Fusion state | `models/fusion_state.json` | Accuracy tracker + weights |

## Rollback / Promotion

To promote a previous release:

```bash
# GitHub
gh release create grandmaster-v1.0 ppo_trader.zip models/transformer_model.pth --title "v1.0 Stable" --notes "Promoted from grandmaster-latest"

# Hugging Face
huggingface-cli upload sajibmdsaberahmad-create/trading-bot-HA-NUN ppo_trader.zip --repo-type model
```

## Troubleshooting

**GitHub release fails with 401/403**: verify `GITHUB_TOKEN` has `repo` scope.

**HuggingFace upload fails**: verify `HF_TOKEN` has `write` permissions; for large files use `git-xet`.

**"Model preservation skipped"**: check logs for network/auth errors; the pipeline will continue even if preservation fails.

**macOS TensorFlow segfault**: training on Apple Silicon may crash during LSTM phase; PPO and Transformer are unaffected. Use CUDA VPS for full grandmaster training.