#!/usr/bin/env bash
# Manual or cron: export gold → SFT → short MLX LoRA → register → restart serve.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"
if [[ -d venv ]]; then source venv/bin/activate; fi

FORCE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=true; shift ;;
    *) shift ;;
  esac
done

python3 - <<PY
import json
import os
from core.halim_auto_lm import run_auto_retrain_sync

os.environ.setdefault("HALIM_REPO_ROOT", "$ROOT")
force = ${FORCE/true/True}
force = force if isinstance(force, bool) else str("$FORCE").lower() == "true"
out = run_auto_retrain_sync(trigger="manual_script", force=force)
print(json.dumps(out, indent=2))
raise SystemExit(0 if out.get("ok") else 1)
PY
