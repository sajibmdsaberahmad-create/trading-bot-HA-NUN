#!/usr/bin/env bash
# Push HANOON workspace to all 4-repo architecture:
#   trading-bot-HA-NUN      → code + light AI state
#   trading-bot-Logs        → journals, experience, metrics
#   trading-bot-Grandmaster → model weights & proxies
#   Algo                    → optional private experiments (manual)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -f .env ]; then set -a; source .env; set +a; fi
export GH_TOKEN="${GITHUB_TOKEN:-}"
export GITHUB_TOKEN="${GITHUB_TOKEN:-}"
echo "═══ HANOON multi-repo sync ═══"
python3 - <<'PY'
from core.config import BotConfig
from core.git_sync import init, verify_all_repos, sync_all_repos, push_learning_checkpoint

cfg = BotConfig()
init(cfg)
verify_all_repos(cfg)
ok = push_learning_checkpoint("script_sync_all_repos")
print("learning_checkpoint:", ok)
PY
echo "✅ Done — check logs above for per-repo status"
