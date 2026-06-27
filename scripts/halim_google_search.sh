#!/usr/bin/env bash
# Test Halim Google AI search — public AI Overview only (no Gemini API, no browsing).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
QUERY="${1:-what is an egg}"
export HALIM_GOOGLE_AI_SEARCH=true
export HALIM_OPERATOR_SETTINGS=true
if [[ -d venv ]]; then source venv/bin/activate; fi
PYTHONPATH=. python -c "
from core.halim_guardrails import apply_operator_frontier_settings
from core.halim_google_ai_search import query_google_ai_answer
from core.config import BotConfig
apply_operator_frontier_settings(BotConfig())
import json
r = query_google_ai_answer('$QUERY')
print(json.dumps(r, indent=2))
"
