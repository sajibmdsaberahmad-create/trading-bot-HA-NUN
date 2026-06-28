# Halim Toddler — Google Colab (beginner guide)

Train Halim's first language model **for free** on Google's GPU. Your Mac only prepares data and runs the finished model.

---

## Part A — On your Mac (10 minutes)

### Step A1 — Prepare data (one command)

Open **Terminal** and run:

```bash
cd ~/Downloads/tradingbot
chmod +x scripts/halim_colab_ready.sh
./scripts/halim_colab_ready.sh
```

This upgrades live PPO, exports **all** training gold, rebuilds `sft/train.jsonl`, and **overwrites** the single canonical **`halim_sft.zip`** (same path every time — never keep old copies).

### One zip rule

| Do | Don't |
|----|-------|
| Upload `tradingbot/halim_sft.zip` only | Keep `halim_sft_old.zip` or dated copies in Downloads |
| Run `./scripts/halim_colab_ready.sh` before each Colab train | Re-upload a zip from last week |
| Check `build_id` in Colab cell output | Guess which zip is newest |

After packaging, see `models/halim_sft_package.meta.json` for `build_id` and `updated_at`.

You should see `"ok": true` and **2,500+** deduped pairs (more after replay sessions).

Manual path (same result):

```bash
./scripts/halim_readiness.sh
./scripts/halim_prepare_train.sh
./scripts/halim_package_colab.sh
```

### Step A2 — Create upload zip

```bash
chmod +x scripts/halim_package_colab.sh
./scripts/halim_package_colab.sh
```

This creates **`halim_sft.zip`** in your tradingbot folder (~2–5 MB).

Keep Terminal open — you'll come back here after Colab.

---

## Part B — Google Colab (30–45 minutes)

### Step B1 — Create Colab account

1. Go to [https://colab.research.google.com](https://colab.research.google.com)
2. Sign in with your Google account (free)

### Step B2 — Enable free GPU

1. **File → Upload notebook**
2. Upload `halim/colab/Halim_Toddler_Training.ipynb` from this repo
3. **Runtime → Change runtime type**
4. Set **Hardware accelerator: T4 GPU**
5. Click **Save**

### Step B3 — Run cells top to bottom

Click each cell, press **Shift+Enter**.

| Cell | What it does |
|------|----------------|
| 1 | Checks GPU — must show `Tesla T4` |
| 2 | Installs libraries |
| 3 | **Upload `halim_sft.zip`** from your Mac |
| 4 | Unzips to `sft/train.jsonl` |
| 5 | Gets training script (or upload `train_toddler_colab.py` manually) |
| 6 | **Trains Halim** (~15–30 min) |
| 7 | Quick test generation |
| 8 | Downloads **`halim_toddler_v1.zip`** to your Mac |

**If Step 5 curl fails:** drag `halim/colab/train_toddler_colab.py` into Colab's file panel, then run Step 6.

---

## Part C — Back on your Mac (5 minutes)

### Step C1 — Install the checkpoint

```bash
cd ~/Downloads/tradingbot
mkdir -p halim/data/checkpoints/toddler_v1
unzip ~/Downloads/halim_toddler_v1.zip -d halim/data/checkpoints/
```

You should have:

```
halim/data/checkpoints/toddler_v1/
  config.json
  merged/          ← the actual model weights
  lora_adapter/    ← optional, ignore
```

### Step C2 — Register Halim's brain

```bash
pip install torch transformers   # if not already installed
./scripts/halim_register_checkpoint.sh toddler_v1 --backend hf
```

### Step C3 — Turn on Halim inference

```bash
export HALIM_LM_BACKEND=hf
export HALIM_MODEL_PATH=halim/data/checkpoints/latest
./scripts/halim_serve.sh
```

In another Terminal tab:

```bash
./scripts/halim_chat.sh "Halim, summarize today's trading mindset"
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Colab says "No GPU" | Runtime → Change runtime type → T4 GPU |
| Session disconnected | Re-run from Step 3 (upload zip again) |
| `halim_sft.zip` missing | Run `./scripts/halim_package_colab.sh` on Mac |
| Chat returns empty | Check `./scripts/halim_status.sh` — checkpoint must exist |
| Out of memory on Colab | Rare on T4; restart runtime and re-run |
| `unexpected keyword argument 'max_seq_length'` | Colab TRL is new — use latest `train_toddler_colab.py` (uses `max_length`) |

---

## What you own after this

- **`toddler_v1/merged/`** — ~1GB model weights, trained on your trades
- **No API keys** required for Halim text generation
- Phase moves from **newborn** → **adult** (checkpoint exists)
