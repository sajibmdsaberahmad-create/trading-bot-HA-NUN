#!/usr/bin/env bash
# Halim engine status — inline students + optional server health.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi
python - <<'PY'
import json
import os
import sys
sys.path.insert(0, "halim")
from halim.engine import collect_status
from halim.client import health, status as remote_status

st = collect_status()
print(json.dumps(st, indent=2))
url = os.getenv("HALIM_SERVER_URL", "http://127.0.0.1:8765")
if health(url, timeout=0.5):
    print("\n--- server ---")
    print(json.dumps(remote_status(url), indent=2))
else:
    print(f"\n(server not running at {url} — optional; start: ./scripts/halim_serve.sh)")
PY
