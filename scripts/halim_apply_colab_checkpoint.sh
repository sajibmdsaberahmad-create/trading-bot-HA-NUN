#!/usr/bin/env bash
# Install latest Colab halim_toddler_vN.zip → record train → restart Halim serve.
# Drop zip in ~/Downloads or Google Drive Halim/ — HANOON start runs this with --if-new.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

STATE_FILE="$ROOT/models/halim_colab_install_state.json"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
INSTALL_LOG="$LOG_DIR/halim_colab_install.log"

IF_NEW=false
FORCE=false
NO_RESTART=false
NO_RECORD=false
ZIP_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --if-new) IF_NEW=true; shift ;;
    --force|-f) FORCE=true; shift ;;
    --no-restart) NO_RESTART=true; shift ;;
    --no-record) NO_RECORD=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--if-new] [--force] [--no-restart] [/path/to/halim_toddler_vN.zip]"
      echo "  --if-new   Skip if this zip already installed (default for HANOON boot)"
      echo "  --force    Re-install even if same zip"
      exit 0
      ;;
    -*) echo "Unknown option: $1"; exit 1 ;;
    *) ZIP_PATH="$1"; shift ;;
  esac
done

mkdir -p "$LOG_DIR" "$(dirname "$STATE_FILE")"
exec > >(tee -a "$INSTALL_LOG") 2>&1

_log() { echo "[$(date '+%H:%M:%S')] $*"; }

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

FIND_JSON=$(python3 "$ROOT/halim/scripts/find_colab_checkpoint.py" ${ZIP_PATH:+"$ZIP_PATH"} 2>/dev/null || echo '{"ok":false}')
ZIP_PATH=$(echo "$FIND_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('path','') if d.get('ok') else '')" 2>/dev/null || true)

if [[ -z "$ZIP_PATH" || ! -f "$ZIP_PATH" ]]; then
  if [[ "$IF_NEW" == "true" ]]; then
    _log "Halim Colab auto-install: no new zip in Downloads/Drive (ok)"
    exit 0
  fi
  _log "ERROR: No halim_toddler_v*.zip found. Download from Colab/Drive to ~/Downloads"
  exit 1
fi

ZIP_NAME=$(basename "$ZIP_PATH")
ZIP_SIZE=$(stat -f%z "$ZIP_PATH" 2>/dev/null || stat -c%s "$ZIP_PATH")
ZIP_MTIME=$(stat -f%m "$ZIP_PATH" 2>/dev/null || stat -c%Y "$ZIP_PATH")
ZIP_VERSION=$(echo "$ZIP_NAME" | sed -n 's/.*v\([0-9]*\).*/\1/p')
ZIP_VERSION="${ZIP_VERSION:-0}"

if [[ "$IF_NEW" == "true" && "$FORCE" != "true" && -f "$STATE_FILE" ]]; then
  ALREADY=$(python3 - <<PY
import json
from pathlib import Path
state = json.loads(Path("$STATE_FILE").read_text())
same = (
    state.get("zip_path") == "$ZIP_PATH"
    and int(state.get("zip_size", -1)) == int($ZIP_SIZE)
    and int(state.get("zip_mtime", -1)) == int($ZIP_MTIME)
)
installed_v = int(state.get("version", 0))
new_v = int("$ZIP_VERSION")
print("skip" if same or (installed_v >= new_v and Path("halim/data/checkpoints/toddler_v1/lora_adapter/adapter_model.safetensors").is_file()) else "install")
PY
)
  if [[ "$ALREADY" == "skip" ]]; then
    _log "Halim Colab: $ZIP_NAME already installed (v$ZIP_VERSION) — skip"
    exit 0
  fi
fi

_log "═══════════════════════════════════════════════════════════"
_log "Halim Colab apply: $ZIP_NAME (v$ZIP_VERSION)"
_log "═══════════════════════════════════════════════════════════"

"$ROOT/scripts/halim_install_toddler.sh" --force "$ZIP_PATH"

if [[ "$NO_RECORD" != "true" ]]; then
  _log "Recording SFT train hashes for next incremental Colab pack…"
  "$ROOT/scripts/halim_record_train.sh" || _log "WARN: halim_record_train failed (non-fatal)"
fi

chmod +x "$ROOT/scripts/halim_install_lm.sh" 2>/dev/null || true
"$ROOT/scripts/halim_install_lm.sh" 2>/dev/null || _log "WARN: halim_install_lm skipped"

if [[ -f "$ROOT/halim/scripts/eval_toddler.py" ]]; then
  _log "Quick toddler LM probe…"
  python3 "$ROOT/halim/scripts/eval_toddler.py" 2>/dev/null | tail -5 || _log "WARN: eval probe skipped/failed"
fi

python3 - <<PY
import json
from datetime import datetime, timezone
from pathlib import Path

root = Path("$ROOT")
meta = {}
for p in (
    root / "models/halim_sft_package.meta.json",
    root / "halim/data/training/sft/colab_manifest.json",
):
    if p.is_file():
        try:
            meta = json.loads(p.read_text())
            break
        except Exception:
            pass

state = {
    "installed_at": datetime.now(timezone.utc).isoformat(),
    "zip_path": "$ZIP_PATH",
    "zip_name": "$ZIP_NAME",
    "version": int("$ZIP_VERSION"),
    "zip_size": int($ZIP_SIZE),
    "zip_mtime": int($ZIP_MTIME),
    "build_id_recorded": meta.get("build_id", ""),
    "train_pairs_pack": meta.get("train_pairs"),
}
Path("$STATE_FILE").write_text(json.dumps(state, indent=2))
print(json.dumps(state, indent=2))
PY

if [[ "$NO_RESTART" != "true" ]]; then
  _log "Restarting Halim serve with v$ZIP_VERSION weights…"
  "$ROOT/scripts/ensure_halim_active.sh" --serve-only --restart || _log "WARN: Halim serve restart issue — see logs/halim_serve.log"
fi

_log "✅ Halim v$ZIP_VERSION ready — spike participation LM active"
_log "   Log: $INSTALL_LOG"
_log "   Test: curl -s http://127.0.0.1:8765/v1/status | python3 -m json.tool"
