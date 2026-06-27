#!/usr/bin/env bash
# Run owned-brain evolution manually (export, proxy train, PPO teacher, manifest, git push).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export TZ="America/New_York"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

export OWNED_BRAIN_DEVICE="${OWNED_BRAIN_DEVICE:-m2_8gb}"
export OWNED_BRAIN_GIT_PUSH="${OWNED_BRAIN_GIT_PUSH:-true}"

if [[ -d venv ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

echo "══════════════════════════════════════════════════════════════"
echo "  OWNED BRAIN — post-session evolution"
echo "  Device: $OWNED_BRAIN_DEVICE | Git push: $OWNED_BRAIN_GIT_PUSH"
echo "══════════════════════════════════════════════════════════════"

PYTHONPATH=. python -c "
from core.config import BotConfig
from core.owned_brain_evolution import run_post_session_evolution
import json
r = run_post_session_evolution(BotConfig(), trigger='manual', push_git=True)
print(json.dumps(r, indent=2))
"
