#!/usr/bin/env bash
# Install Halim toddler checkpoint from Colab zip or extracted folder.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

FORCE=false
SRC=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force|-f) FORCE=true; shift ;;
    -*) echo "Unknown option: $1"; exit 1 ;;
    *) SRC="$1"; shift ;;
  esac
done

if [[ -z "$SRC" ]]; then
  for candidate in \
    "$HOME/Downloads/halim_toddler_v3.zip" \
    "$HOME/Downloads/halim_toddler_v3" \
    "$HOME/Downloads/halim_toddler_v2.zip" \
    "$HOME/Downloads/halim_toddler_v1.zip"; do
    if [[ -e "$candidate" ]]; then
      SRC="$candidate"
      break
    fi
  done
fi

if [[ -z "$SRC" || ! -e "$SRC" ]]; then
  echo "Usage: ./scripts/halim_install_toddler.sh [--force] [/path/to/halim_toddler_v3.zip|folder]"
  exit 1
fi

CKPT="$ROOT/halim/data/checkpoints/toddler_v1"
mkdir -p "$ROOT/halim/data/checkpoints"

if [[ "$FORCE" == "true" && -d "$CKPT" ]]; then
  echo "🗑  Removing old toddler_v1 (--force)…"
  rm -rf "$CKPT"
fi

if [[ -d "$SRC" ]]; then
  echo "📦 Installing folder $SRC → toddler_v1…"
  mkdir -p "$CKPT"
  if command -v rsync &>/dev/null; then
    rsync -a --delete "$SRC/" "$CKPT/"
  else
    rm -rf "$CKPT"
    mkdir -p "$CKPT"
    cp -R "$SRC/." "$CKPT/"
  fi
elif [[ -f "$SRC" && "$SRC" == *.zip ]]; then
  echo "📦 Extracting $SRC → halim/data/checkpoints/…"
  unzip -o "$SRC" -d "$ROOT/halim/data/checkpoints/"
else
  echo "Unsupported source: $SRC (need .zip or directory)"
  exit 1
fi

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

python3 - <<'PY'
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

ckpt = Path("halim/data/checkpoints/toddler_v1")
cfg_path = ckpt / "config.json"
cfg = {}
if cfg_path.is_file():
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:
        pass

merged = ckpt / "merged" / "model.safetensors"
lora = ckpt / "lora_adapter" / "adapter_model.safetensors"
cfg.setdefault("halim_phase", "toddler")
cfg.setdefault("model", "M. A. Halim")
cfg.setdefault("base_model", "Qwen/Qwen2.5-0.5B-Instruct")
cfg["backend"] = "mlx" if platform.system() == "Darwin" else cfg.get("backend", "hf")
if merged.is_file():
    cfg["merged_path"] = "merged"
if lora.is_file():
    cfg["adapter_path"] = "lora_adapter"
cfg["installed_at"] = datetime.now(timezone.utc).isoformat()
cfg["installed_from"] = str(cfg.get("build_id") or cfg.get("trained_at") or "colab")
cfg_path.write_text(json.dumps(cfg, indent=2))
print(json.dumps({"ok": True, "config": str(cfg_path), "merged": merged.is_file(), "lora": lora.is_file()}, indent=2))
PY

chmod +x "$ROOT/scripts/halim_register_checkpoint.sh"
"$ROOT/scripts/halim_register_checkpoint.sh" toddler_v1 --backend "${HALIM_LM_BACKEND:-mlx}"

echo ""
echo "✓ Halim toddler installed: $CKPT"
echo "  Next: ./scripts/ensure_halim_active.sh --serve-only"
echo "  Record train: ./scripts/halim_record_train.sh"
