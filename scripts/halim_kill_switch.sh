#!/usr/bin/env bash
# Emergency halt for M. A. Halim — all gated actions stop immediately.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REASON="${1:-operator_halt}"
PYTHONPATH=. python -c "
from core.halim_guardrails import activate_kill_switch
activate_kill_switch('$REASON')
print('Halim kill switch ACTIVE')
"
