#!/usr/bin/env bash
# Read-only learn fetch — Wikipedia, news, reference (strict monitoring, never edits external).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
URL="${1:-https://en.wikipedia.org/wiki/Egg}"
export HALIM_WEB_LEARN=true
export HALIM_OPERATOR_SETTINGS=true
if [[ -d venv ]]; then source venv/bin/activate; fi
PYTHONPATH=. python - "$URL" <<'PY'
import json
import sys
from core.halim_guardrails import apply_operator_frontier_settings
from core.halim_web_learn import fetch_learn_page, fetch_wikipedia_summary
from core.config import BotConfig

url = sys.argv[1]
apply_operator_frontier_settings(BotConfig())
if url.startswith("wiki:"):
    r = fetch_wikipedia_summary(url[5:])
else:
    r = fetch_learn_page(url)
print(json.dumps(r, indent=2))
PY
