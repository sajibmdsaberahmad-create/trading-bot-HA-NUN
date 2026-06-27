#!/usr/bin/env bash
# Manual or cron: export gold → SFT → short MLX LoRA → register → restart serve.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi

FORCE=false
for arg in "$@"; do
  [[ "$arg" == "--force" ]] && FORCE=true
done
export HALIM_AUTO_FORCE="$([[ "$FORCE" == true ]] && echo 1 || echo 0)"

python3 - <<'PY'
import json
import os
from core.halim_auto_lm import run_auto_retrain_sync

force = os.getenv("HALIM_AUTO_FORCE", "0") == "1"
out = run_auto_retrain_sync(trigger="manual_script", force=force)
print(json.dumps(out, indent=2))
raise SystemExit(0 if out.get("ok") else 1)
PY
