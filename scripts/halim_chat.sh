#!/usr/bin/env bash
# Halim chat CLI — phased unlock (collecting → teacher → native).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi

if [[ "${1:-}" == "--unlock" ]]; then
  python - <<'PY'
import json
from core.halim_unlock import unlock_ladder
print(json.dumps(unlock_ladder(), indent=2, default=str))
PY
  exit 0
fi

if [[ $# -eq 0 ]]; then
  echo "Usage: ./scripts/halim_chat.sh \"your message\""
  echo "       ./scripts/halim_chat.sh --unlock"
  exit 1
fi

python - "$@" <<'PY'
import json
import sys
from core.halim_chat import halim_chat

msg = " ".join(sys.argv[1:])
r = halim_chat(msg, purpose="chat")
print(r.get("text") or json.dumps(r, indent=2))
print(f"\n[mode={r.get('mode')} source={r.get('source')} cap={r.get('capability')}]", file=sys.stderr)
PY
