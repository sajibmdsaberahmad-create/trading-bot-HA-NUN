# Colab cells (mirror of `halim_toddler_train.ipynb`)

**Use the notebook** — `halim/colab/halim_toddler_train.ipynb` — upload it to Colab fresh.

These cells are identical to the notebook. Full guide: **`COLAB_GUIDE.md`**.

---

## Cell 1 — Mount Drive

```python
from google.colab import drive
drive.mount('/content/drive')

import os
WORK = '/content/drive/MyDrive/Halim'
os.makedirs(WORK, exist_ok=True)
os.environ['HALIM_WORK'] = WORK
print('Drive folder:', WORK)
!ls -la "$WORK"
```

---

## Cell 2 — Upload SFT + setup

```python
!pip install -q transformers peft trl datasets accelerate bitsandbytes

from google.colab import files
import os, shutil, zipfile
from pathlib import Path

for stale in ('sft', 'toddler_v1', 'train_toddler_colab.py', 'colab_drive_setup.py'):
    p = Path('/content') / stale
    if p.is_dir():
        shutil.rmtree(p)
    elif p.is_file():
        p.unlink()

print('Pick halim_sft.zip from Mac')
uploaded = files.upload()
sft_zip = None
for name, data in uploaded.items():
    dest = Path('/content') / name
    dest.write_bytes(data)
    if name.endswith('.zip'):
        sft_zip = dest

if sft_zip is None:
    sft_zip = next(Path('/content').glob('halim_sft*.zip'), None)
if sft_zip is None:
    raise FileNotFoundError('Upload halim_sft.zip')

with zipfile.ZipFile(sft_zip, 'r') as zf:
    zf.extractall('/content')

WORK = Path(os.environ['HALIM_WORK'])
%cd /content
!python colab_drive_setup.py
```

---

## Cell 3 — Fresh fast train

```python
%cd /content
import os, shutil
from pathlib import Path

adapter = Path('toddler_v1/lora_adapter')
if adapter.exists():
    shutil.rmtree(adapter)

os.environ['HALIM_OUT_DIR'] = '/content/toddler_v1'
os.environ['HALIM_FRESH_TRAIN'] = 'true'
os.environ['HALIM_CONTINUE_LORA'] = 'false'
os.environ['HALIM_FAST_PATH'] = 'auto'

!python train_toddler_colab.py
```

---

## Cell 4 — Zip to Drive

```python
import json, shutil, subprocess
from pathlib import Path

WORK = Path('/content/drive/MyDrive/Halim')
state_path = WORK / 'halim_colab_state.json'
state = json.loads(state_path.read_text()) if state_path.is_file() else {}
out_name = state.get('next_output_zip', 'halim_toddler_v4.zip')

src = Path('/content/toddler_v1')
if not (src / 'merged').is_dir():
    raise FileNotFoundError('Wait for Cell 3 to finish')

shutil.copytree(src, WORK / 'toddler_v1', dirs_exist_ok=True)
subprocess.run(['zip', '-r', out_name, 'toddler_v1'], cwd=str(WORK), check=True)
print('Saved:', WORK / out_name)
print('Mac: ./scripts/halim_apply_colab_checkpoint.sh')
```
