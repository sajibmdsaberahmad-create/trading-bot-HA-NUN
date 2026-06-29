#!/usr/bin/env bash
# Install Colab toddler checkpoint + start Halim serve (LoRA or merged).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

ZIP="${1:-$HOME/Downloads/halim_toddler_v1.zip}"
CKPT="$ROOT/halim/data/checkpoints/toddler_v1"

if [[ "${HALIM_TODDLER_FORCE:-false}" == "true" ]]; then
  "$ROOT/scripts/halim_install_toddler.sh" --force "${1:-$HOME/Downloads/halim_toddler_v3}"
elif [[ -n "${1:-}" ]] && { [[ -d "$1" ]] || [[ -f "$1" && "$1" == *.zip ]]; }; then
  force=()
  [[ "${2:-}" == "--force" ]] && force=(--force)
  "$ROOT/scripts/halim_install_toddler.sh" "${force[@]}" "$1"
elif [[ ! -f "$CKPT/merged/model.safetensors" ]] && [[ ! -f "$CKPT/lora_adapter/adapter_model.safetensors" ]]; then
  if [[ -f "$ZIP" ]]; then
    "$ROOT/scripts/halim_install_toddler.sh" "$ZIP"
  else
    echo "Missing checkpoint. Run: ./scripts/halim_install_toddler.sh ~/Downloads/halim_toddler_v3"
    exit 1
  fi
fi

# Halim metadata (zip may only have merged/config.json)
if [[ ! -f "$CKPT/config.json" ]]; then
  python3 - <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path("halim")))
try:
    from halim.scaffold import SCAFFOLD_HF
except ImportError:
    SCAFFOLD_HF = "Qwen/Qwen2.5-0.5B-Instruct"
p = Path("halim/data/checkpoints/toddler_v1/config.json")
merged = Path("halim/data/checkpoints/toddler_v1/merged/model.safetensors")
lora = Path("halim/data/checkpoints/toddler_v1/lora_adapter/adapter_model.safetensors")
cfg = {
    "halim_phase": "toddler",
    "model": "M. A. Halim",
    "base_model": SCAFFOLD_HF,
    "backend": "mlx" if __import__("platform").system() == "Darwin" else "hf",
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
chmod +x "$ROOT/scripts/halim_install_lm.sh"
"$ROOT/scripts/halim_install_lm.sh"

./scripts/halim_register_checkpoint.sh toddler_v1 --backend "${HALIM_LM_BACKEND:-mlx}"

echo ""
echo "🧠 Starting Halim serve (PPO distillation always on via halim_env.sh)…"
echo "   HALIM_LM_BACKEND=$HALIM_LM_BACKEND"
echo "   HALIM_MODEL_PATH=$HALIM_MODEL_PATH"
echo "   HALIM_PPO_COEVOLUTION=$HALIM_PPO_COEVOLUTION"
echo ""

exec ./scripts/halim_serve.sh
