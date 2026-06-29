# Colab cells — Google Drive only (no `files.upload`)

**Notebook:** open `halim/colab/halim_toddler_train.ipynb` in Colab (upload once to Drive or open from repo).

Upload these **2 files** in your browser to `My Drive/Halim/`:

| File | Mac path |
|------|----------|
| `halim_toddler_v2.zip` | `~/Downloads/halim_toddler_v2.zip` |
| `halim_sft.zip` | `~/Downloads/tradingbot/halim_sft.zip` |

`halim_sft.zip` bundles `train_toddler_colab.py` — no separate script upload.

---

## Cell 1 — Mount Drive + env (incremental v3)

```python
from google.colab import drive
drive.mount('/content/drive')

import os
WORK = "/content/drive/MyDrive/Halim"
os.makedirs(WORK, exist_ok=True)

os.environ["HALIM_OUT_DIR"] = f"{WORK}/toddler_v1"
os.environ["HALIM_CONTINUE_LORA"] = "auto"
os.environ["HALIM_RESUME"] = "false"          # incremental: do NOT resume finished v2
os.environ["HALIM_SAVE_TOTAL_LIMIT"] = "3"

print("Drive folder:", WORK)
!ls -la "$WORK"
```

---

## Cell 2 — Unzip v2 weights from Drive (first time only)

Skip this cell if `toddler_v1/lora_adapter/adapter_model.safetensors` already exists.

```python
import zipfile
from pathlib import Path

WORK = Path("/content/drive/MyDrive/Halim")
v2 = WORK / "halim_toddler_v2.zip"
if not v2.is_file():
    raise FileNotFoundError(f"Upload halim_toddler_v2.zip to {WORK}")

with zipfile.ZipFile(v2, "r") as zf:
    zf.extractall(WORK)

adp = WORK / "toddler_v1" / "lora_adapter"
print("adapter:", (adp / "adapter_model.safetensors").is_file())
print("checkpoints:", [p.name for p in sorted(adp.glob("checkpoint-*"))][-5:])
```

---

## Cell 3 — Clear finished v2 checkpoints (incremental v3 only)

Keeps `adapter_model.safetensors`, removes stale `checkpoint-*` so trainer won't skip.

```python
import shutil
from pathlib import Path

adp = Path("/content/drive/MyDrive/Halim/toddler_v1/lora_adapter")
for p in adp.glob("checkpoint-*"):
    shutil.rmtree(p)
    print("removed", p.name)
print("adapter kept:", (adp / "adapter_model.safetensors").is_file())
```

---

## Cell 4 — pip

```python
!pip install -q transformers peft trl datasets accelerate bitsandbytes
```

---

## Cell 5 — Unzip SFT from Drive (not files.upload)

```python
import zipfile
import json
from pathlib import Path

WORK = Path("/content/drive/MyDrive/Halim")
sft = WORK / "halim_sft.zip"
if not sft.is_file():
    raise FileNotFoundError(f"Upload halim_sft.zip to {WORK}")

with zipfile.ZipFile(sft, "r") as zf:
    zf.extractall("/content")

manifest = json.loads(Path("/content/sft/colab_manifest.json").read_text())
print(json.dumps(manifest, indent=2))
print("sft_mode:", manifest.get("sft_mode", "full"))
```

---

## Cell 6 — Train (script comes from halim_sft.zip)

```python
from pathlib import Path

if not Path("/content/train_toddler_colab.py").is_file():
    raise FileNotFoundError("Re-run Cell 5 — halim_sft.zip should include train_toddler_colab.py")

%cd /content
!python train_toddler_colab.py
```

**Good output:**
- `CONTINUE_LORA: True | RESUME_CKPT: none`
- `Loading existing LoRA adapter for continued training…`
- Progress bar **~30–90 min** (NOT `train_runtime: 0.007`)

---

## Cell 7 — Zip v3 on Drive (download optional)

```python
!cd /content/drive/MyDrive/Halim && zip -r halim_toddler_v3.zip toddler_v1
print("Saved:", "/content/drive/MyDrive/Halim/halim_toddler_v3.zip")
```

Download on Mac from [drive.google.com](https://drive.google.com) → `Halim/halim_toddler_v3.zip`

---

## If Colab crashed mid-train

```python
os.environ["HALIM_RESUME"] = "true"
os.environ["HALIM_RESUME_MIDRUN"] = "true"   # only for crash recovery
# do NOT run Cell 3 (don't delete checkpoints)
# re-run Cell 5 + Cell 6
```
