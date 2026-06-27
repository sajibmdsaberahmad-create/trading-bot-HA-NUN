#!/usr/bin/env bash
# Export Halim action journal → instruction-tuning gold (off-hours / manual).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi
python - <<'PY'
import json
from core.halim_action_learn import export_action_gold, all_capabilities_status
from core.halim_identity import compute_halim_phase

phase = compute_halim_phase()
result = export_action_gold()
caps = all_capabilities_status(phase)
print(json.dumps({"phase": phase, "export": result, "capabilities": caps.get("capabilities", {})}, indent=2))
PY
