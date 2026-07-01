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
  # Prefer highest halim_toddler_vN.zip in Downloads
  best_v=0
  for candidate in "$HOME/Downloads"/halim_toddler_v*.zip; do
    [[ -f "$candidate" ]] || continue
    v=$(basename "$candidate" | sed -n 's/.*v\([0-9]*\).*/\1/p')
    [[ -n "$v" && "$v" -gt "$best_v" ]] && best_v="$v" && SRC="$candidate"
  done
  if [[ -z "$SRC" ]]; then
    for candidate in \
      "$HOME/Downloads/halim_toddler_v3" \
      "$HOME/Downloads/halim_toddler_v2" \
      "$HOME/Downloads/halim_toddler_v1"; do
      if [[ -e "$candidate" ]]; then
        SRC="$candidate"
        break
      fi
    done
  fi
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

python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(".").resolve()))
from core.local_cleanup import prune_halim_colab_artifacts
prune_halim_colab_artifacts()
PY

chmod +x "$ROOT/scripts/halim_register_checkpoint.sh"
"$ROOT/scripts/halim_register_checkpoint.sh" toddler_v1 --backend "${HALIM_LM_BACKEND:-mlx}"

echo ""
echo "✓ Halim toddler installed: $CKPT"
echo "  Disk: ~1GB in repo (merged + adapter). Downloads source is NOT removed by this script."
echo "  If disk dropped GB after install, a sweep may have deleted ~/Downloads copies — see ./scripts/disk_audit.sh"
echo "  After quitting Cursor: ./scripts/untrack_halim_weights.sh  (shrinks .git/lfs, keeps weights)"
echo "  Auto pipeline: ./scripts/halim_apply_colab_checkpoint.sh"
echo "  Or restart HANOON (HALIM_AUTO_INSTALL_COLAB=true)"
