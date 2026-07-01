# Halim Colab — one path, no confusion

Train Halim on **free Colab T4**. Mac builds data; Colab trains; Mac installs.

---

## Mac → Colab → Mac

```
Mac:  ./scripts/halim_colab_ready.sh
        ↓ halim_sft.zip (~800 KB, 13k+ pairs)
Colab: halim/colab/halim_toddler_train.ipynb (4 cells)
        ↓ halim_toddler_vN.zip on Drive
Mac:  ./scripts/halim_apply_colab_checkpoint.sh
        ↓
      ./scripts/halim_record_train.sh   (after successful install)
```

---

## Step 1 — Mac: build zip

```bash
cd ~/Downloads/tradingbot
./scripts/halim_colab_ready.sh
```

- **Safe:** exports gold (append/dedupe only), rebuilds `halim_sft.zip` — **does not delete** gold or mark anything trained.
- Check: `models/halim_sft_package.meta.json` → `build_id`, `train_pairs`.

---

## Step 2 — Colab: open notebook fresh

1. Upload **`halim/colab/halim_toddler_train.ipynb`** to Colab  
   (or File → Upload notebook — replace any old copy)
2. **Runtime → Change runtime type → T4 GPU**
3. Run cells **1 → 2 → 3 → 4**

| Cell | What |
|------|------|
| **1** | Mount Drive (`My Drive/Halim/`) — version storage only |
| **2** | Upload `halim_sft.zip` to Colab (widget) → setup |
| **3** | Fresh fast train on `/content/toddler_v1` (~2–3h) |
| **4** | Zip to Drive as `halim_toddler_vN.zip` |

### Cell 3 — confirm fast path

Log should show:

```
Knobs: batch=8 grad_accum=1 ... fp16=False bf16=False profile=colab_t4_fast
```

and `s/it` ~2–3 (faster than ~5.6 slow path; no fp16 AMP on T4 QLoRA).

### Cell 2 — confirm setup

```
fresh train: no prior toddler weights
out_dir: /content/toddler_v1
```

---

## Step 3 — Mac: install

```bash
./scripts/halim_apply_colab_checkpoint.sh
# or: ./scripts/halim_watch_colab_zip.sh  while downloading
./scripts/halim_record_train.sh
```

---

## Where files go

| File | Where |
|------|--------|
| `halim_sft.zip` | **Colab upload** (Cell 2) — every train |
| Training weights | `/content/toddler_v1` during train |
| `halim_toddler_vN.zip` | **Drive** `My Drive/Halim/` |
| Gold on Mac | `halim/data/training/*.jsonl` — never deleted by rebuild |

---

## Later: incremental trains (after v4 installed + recorded)

```bash
./scripts/halim_record_train.sh
./scripts/halim_prepare_train_incremental.sh
```

Same notebook — in Cell 3 change env:

```python
os.environ.pop('HALIM_FRESH_TRAIN', None)
os.environ['HALIM_CONTINUE_LORA'] = 'auto'
```

Expect ~45–90 min on T4.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `s/it` ~5.6 | Old zip — rerun `halim_colab_ready.sh`, re-upload |
| `merged/` missing in Cell 4 | Cell 3 still running or failed |
| OOM on T4 | `HALIM_BATCH_SIZE=4` `HALIM_GRAD_ACCUM=2` |
| Colab disconnect | Re-run Cell 2+3; set `HALIM_RESUME_MIDRUN=true` only if mid-epoch crash |

Copy-paste cells: `halim/colab/COLAB_DRIVE_CELLS.md` (same as notebook).
