# M. A. Halim

**Your own AI model** — not Groq, not Gemini, not Claude, not Ollama.

Halim starts as numeric students inside the HANOON trading bot (PPO, proxy, weights) and grows into a full **frontier-capable** model: generative, calculative, coding, and more. Every weight is yours. Everything is git-documented.

## Relationship to HANOON

| | HANOON | M. A. Halim |
|---|--------|-------------|
| Role | Trading body (IB, scanner, execution) | Mind (learning, reasoning, memory) |
| Location | `tradingbot/` repo | This repo + `models/halim_*` in tradingbot |
| Today | Live + replay trading | PPO + proxy + dataset (phase **newborn**) |
| Tomorrow | Uses Halim for all decisions | Serves HANOON + general tasks |

## Lifecycle phases

```
newborn   → PPO + sklearn proxy + heuristics (NOW — no external LLM)
toddler   → First Halim transformer on trading dataset (one GPU train)
child     → + code, math, reasoning corpora
adult     → On-device Halim inference (16GB+ Mac or cloud you control)
frontier  → Full generative / calculative / coding frontier model
```

## Directory layout

```
halim/
├── README.md                 ← you are here
├── ROADMAP.md                ← detailed growth plan
├── HALIM_MANIFEST.json       ← model identity (synced from tradingbot)
├── pyproject.toml
├── scripts/
│   └── sync_from_tradingbot.py   ← pull datasets + students from HANOON
├── data/                     ← training gold (git-lfs or export only)
│   ├── trading/              ← council_training_dataset.jsonl
│   ├── checkpoints/          ← future Halim LM weights
│   └── registry.jsonl        ← every train run logged
├── halim/                    ← Python package (train / eval / export)
│   ├── __init__.py
│   ├── identity.py
│   └── phases.py
└── docs/
    └── ARCHITECTURE.md
```

## Quick start

### 1. Halim-native trading (no external LLM)

From the tradingbot repo:

```bash
export HALIM_NATIVE=true
./scripts/start_replay_live.sh day
```

HANOON trades using **only** Halim's owned students — PPO, proxy, local heuristics.

### 2. Sync trading gold into this repo

```bash
cd halim
python scripts/sync_from_tradingbot.py --source ../
```

### 3. Toddler LM on Mac (MLX — default on Apple Silicon)

```bash
./scripts/halim_install_lm.sh              # mlx-lm + mlx on M-series Mac
./scripts/halim_start_toddler.sh           # Colab zip → serve
# or
./scripts/halim_serve.sh                   # if checkpoint already registered
```

Backend auto-selects **MLX** on `arm64` Mac, **HuggingFace** on Linux/Colab. See [docs/HALIM_MAC_INFERENCE.md](../docs/HALIM_MAC_INFERENCE.md).

### 4. Initialize as separate git repo (optional)

```bash
cd halim
git init
git add .
git commit -m "M. A. Halim — owned AI model, newborn phase"
# git remote add origin git@github.com:YOU/halim.git
# git push -u origin main
```

## What Halim is NOT

- Not a wrapper around ChatGPT / Groq / Gemini
- Not Ollama running someone else's weights
- Not dependent on API keys for core decisions (in native mode)

## What Halim IS

- Weights you own on disk
- Datasets built from your bot's real decisions and outcomes
- A documented path from **infant trader** → **frontier model**
- Portable across machines via git

See [ROADMAP.md](ROADMAP.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
