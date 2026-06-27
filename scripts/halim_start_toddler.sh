#!/usr/bin/env bash
# Install Colab toddler checkpoint + start Halim serve (LoRA or merged).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

ZIP="${1:-$HOME/Downloads/halim_toddler_v1.zip}"
CKPT="$ROOT/halim/data/checkpoints/toddler_v1"

if [[ ! -d "$CKPT/merged" ]] || [[ ! -f "$CKPT/merged/model.safetensors" ]]; then
  if [[ ! -f "$ZIP" ]]; then
    echo "Missing checkpoint and zip: $ZIP"
    echo "Usage: ./scripts/halim_start_toddler.sh [/path/to/halim_toddler_v1.zip]"
    exit 1
  fi
  echo "📦 Extracting $ZIP → halim/data/checkpoints/ (~1 GB, one time)…"
  mkdir -p "$ROOT/halim/data/checkpoints"
  unzip -o "$ZIP" -d "$ROOT/halim/data/checkpoints/"
fi

# Halim metadata (zip may only have merged/config.json)
if [[ ! -f "$CKPT/config.json" ]]; then
  python3 - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
p = Path("halim/data/checkpoints/toddler_v1/config.json")
merged = Path("halim/data/checkpoints/toddler_v1/merged/model.safetensors")
lora = Path("halim/data/checkpoints/toddler_v1/lora_adapter/adapter_model.safetensors")
cfg = {
    "halim_phase": "toddler",
    "model": "M. A. Halim",
    "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
    "backend": "hf",
    "merged_path": "merged" if merged.is_file() else None,
    "adapter_path": "lora_adapter" if lora.is_file() and not merged.is_file() else None,
    "registered_at": datetime.now(timezone.utc).isoformat(),
    "trained_on": "google_colab",
}
cfg = {k: v for k, v in cfg.items() if v is not None}
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(cfg, indent=2))
print("Wrote", p)
PY
fi

if [[ -d "$ROOT/venv" ]]; then source "$ROOT/venv/bin/activate"; fi
pip install -q torch transformers peft 2>/dev/null || pip install torch transformers peft
pip uninstall -y torchao 2>/dev/null || true

./scripts/halim_register_checkpoint.sh toddler_v1 --backend hf

echo ""
echo "🧠 Starting Halim serve (PPO distillation always on via halim_env.sh)…"
echo "   HALIM_LM_BACKEND=$HALIM_LM_BACKEND"
echo "   HALIM_MODEL_PATH=$HALIM_MODEL_PATH"
echo "   HALIM_PPO_COEVOLUTION=$HALIM_PPO_COEVOLUTION"
echo ""

exec ./scripts/halim_serve.sh
