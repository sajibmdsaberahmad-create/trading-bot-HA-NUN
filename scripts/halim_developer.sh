#!/usr/bin/env bash
# M. A. Halim developer cycle — mutate, self-improve, sync halim repo, git push now.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export TZ="America/New_York"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export HALIM_AUTO_PUSH=true
export GIT_PUSH_DURING_SESSION=true
export OWNED_BRAIN_GIT_PUSH=true

if [[ -d venv ]]; then source venv/bin/activate; fi
if [[ -f .env ]]; then set -a; source .env; set +a; fi

echo "══════════════════════════════════════════════════════════════"
echo "  M. A. Halim DEVELOPER — mutate · improve · git push"
echo "══════════════════════════════════════════════════════════════"

PYTHONPATH=. python -c "
from core.config import BotConfig
from core.halim_developer import run_halim_developer_cycle
import json
r = run_halim_developer_cycle(BotConfig(), trigger='manual', push_git=True)
print(json.dumps(r, indent=2))
"
