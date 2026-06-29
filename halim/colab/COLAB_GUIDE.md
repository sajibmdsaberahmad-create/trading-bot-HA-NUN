# Halim Toddler — Google Colab (beginner guide)

Train Halim's first language model **for free** on Google's GPU. Your Mac prepares data; Colab trains with **Drive saves + resume**.

---

## Quick reference — which mode?

| Situation | Mac command | Colab env |
|-----------|-------------|-----------|
| **First train / big jump (v3 + commander)** | `./scripts/halim_colab_ready.sh` | `HALIM_FRESH_TRAIN=true` |
| **Weekly update / new gold** | `./scripts/halim_prepare_train_incremental.sh` | `HALIM_CONTINUE_LORA=auto` |
| **Colab crashed mid-run** | (same zip still on Drive) | `HALIM_RESUME=auto` — **do not** set `FRESH_TRAIN` |
| **After any successful train** | `./scripts/halim_record_train.sh` | (or auto on Colab if repo mounted) |

---

## Part A — On your Mac

### A1 — Full pack (v3 first time with commander gold)

```bash
cd ~/Downloads/tradingbot
./scripts/halim_colab_ready.sh
```

Creates **`halim_sft.zip`** (~11k pairs full mode). Check `models/halim_sft_package.meta.json` for `build_id`.

### A2 — Incremental pack (after v3 — faster ~45–90 min)

```bash
./scripts/halim_record_train.sh          # once, after last successful Colab train
./scripts/halim_prepare_train_incremental.sh
```

Creates a smaller zip: **core curriculum + new gold only** (~1.5–2.5k pairs).

### A3 — Record v2 if you already trained but never recorded

```bash
# Marks current train.jsonl hashes as "already trained" (use v2 build id)
HALIM_SFT_MODE=full ./scripts/halim_prepare_train.sh   # if needed
./scripts/halim_record_train.sh --build-id f952f242ea6e
```

---

## Part B — Google Colab (automatic)

Open **`halim/colab/halim_toddler_train.ipynb`** → Runtime → GPU → run all 4 cells.

### Upload to `My Drive/Halim/` (browser)

| When | File |
|------|------|
| **Every train** | `halim_sft.zip` from Mac (`halim_prepare_train_incremental.sh`) |
| **First time only** | `halim_toddler_v2.zip` if `toddler_v1/` not on Drive yet |

After the first run, weights stay on Drive in `toddler_v1/`. Next runs: **only upload new `halim_sft.zip`**.

Auto logic (`colab_drive_setup.py` inside the SFT zip):
- picks latest `halim_sft*.zip` and existing `toddler_v1/` on Drive
- incremental continue LoRA vs crash-resume vs fresh
- names output `halim_toddler_v4.zip`, `v5`, … on Drive

Copy-paste cells (legacy): **`halim/colab/COLAB_DRIVE_CELLS.md`**

### B1 — Mount Drive + env

```python
from google.colab import drive
drive.mount('/content/drive')

import os
WORK = "/content/drive/MyDrive/Halim"
os.makedirs(WORK, exist_ok=True)

os.environ["HALIM_OUT_DIR"] = f"{WORK}/toddler_v1"
os.environ["HALIM_CONTINUE_LORA"] = "auto"
os.environ["HALIM_RESUME"] = "false"          # incremental v3
os.environ["HALIM_SAVE_TOTAL_LIMIT"] = "3"

!ls -la "$WORK"
```

### B2 — Unzip v2 + SFT + script from Drive

```python
import zipfile, shutil, json
from pathlib import Path

WORK = Path("/content/drive/MyDrive/Halim")

# v2 (skip if adapter already on Drive)
if (WORK / "halim_toddler_v2.zip").is_file():
    adp = WORK / "toddler_v1" / "lora_adapter" / "adapter_model.safetensors"
    if not adp.is_file():
        with zipfile.ZipFile(WORK / "halim_toddler_v2.zip", "r") as zf:
            zf.extractall(WORK)

# remove finished v2 checkpoints (incremental v3)
adp_dir = WORK / "toddler_v1" / "lora_adapter"
for p in adp_dir.glob("checkpoint-*"):
    shutil.rmtree(p)

# SFT from Drive (includes train_toddler_colab.py inside zip)
with zipfile.ZipFile(WORK / "halim_sft.zip", "r") as zf:
    zf.extractall("/content")
print(json.dumps(json.load(open("/content/sft/colab_manifest.json")), indent=2))
```

### B3 — pip + train

```python
!pip install -q transformers peft trl datasets accelerate bitsandbytes
%cd /content
!python train_toddler_colab.py
```

Expect: `CONTINUE_LORA: True | RESUME_CKPT: none` and a **real** progress bar (~30–90 min).

### B4 — If session dies mid-train

```python
os.environ["HALIM_RESUME"] = "true"
os.environ["HALIM_RESUME_MIDRUN"] = "true"
# do NOT delete checkpoint-* folders — re-run B2 (skip v2 unzip) + B3
```

### B5 — Zip v3 on Drive

```python
!cd /content/drive/MyDrive/Halim && zip -r halim_toddler_v3.zip toddler_v1
```

Download `halim_toddler_v3.zip` from Drive on your Mac (no Colab upload needed).


---

## Part C — Back on Mac

```bash
cd ~/Downloads/tradingbot
./scripts/halim_start_toddler.sh ~/Downloads/halim_toddler_v3.zip
./scripts/halim_record_train.sh
./scripts/ensure_halim_active.sh --serve-only
```

---

## Environment variables (train script)

| Variable | Default | Meaning |
|----------|---------|---------|
| `HALIM_OUT_DIR` | `toddler_v1` | **Use Drive path** for persistence |
| `HALIM_RESUME` | `auto` | Resume `checkpoint-*` after crash |
| `HALIM_FRESH_TRAIN` | `false` | Wipe adapter; train from base Qwen |
| `HALIM_CONTINUE_LORA` | `auto` | Load existing LoRA on new SFT zip |
| `HALIM_SAVE_STEPS` | `0` | `0` = save each epoch; `200` = every 200 steps |
| `HALIM_SAVE_TOTAL_LIMIT` | `3` | Keep last N checkpoints |
| `HALIM_RESUME_CHECKPOINT` | — | Explicit path to one checkpoint folder |
| `HALIM_CORE_DELTA_EPOCHS` | `2.5` | Fewer epochs for incremental packs |

---

## Your workflow going forward

```
Mac: gold grows → incremental pack → halim_sft.zip
Colab: Drive OUT_DIR + CONTINUE_LORA → train ~45–90 min
Mac: install zip → record_train → serve
```

**v3 now:** `halim_colab_ready.sh` + Colab with `HALIM_FRESH_TRAIN=true` + Drive `OUT_DIR`  
**v4+:** `halim_prepare_train_incremental.sh` + `HALIM_CONTINUE_LORA=auto` (no FRESH_TRAIN)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Colab disconnected | Re-run with same Drive `OUT_DIR`, `HALIM_RESUME=auto` |
| Training 5+ hours | Use incremental pack, not full 11k |
| Incremental pack empty | Run `./scripts/halim_record_train.sh` after last train |
| `Can't find adapter` | First train needs `FRESH_TRAIN=true` once |
| Chat weak after v2 | v3 full with commander gold still recommended once |
