#!/usr/bin/env bash
# One-shot git push (manual command — not the background daemon)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then set -a; source .env; set +a; fi
export GIT_SYNC_STANDALONE=1
export GH_TOKEN="${GITHUB_TOKEN:-${GITHUB_PAT:-}}"
export GITHUB_TOKEN="${GITHUB_TOKEN:-${GITHUB_PAT:-}}"

if [ -d "$ROOT/venv" ]; then source "$ROOT/venv/bin/activate"; fi

REASON="${1:-manual_sync}"
export REASON
python3 - <<PY
import os, sys
sys.path.insert(0, "$ROOT")
os.environ["GIT_SYNC_STANDALONE"] = "1"
from core.config import BotConfig
from core.git_sync import init, preflight_check, push_change, set_standalone_mode, _collect_dirty_files, REPO_DIR

cfg = BotConfig()
set_standalone_mode(True)
init(cfg)
ok, lines = preflight_check(cfg)
print("═══ Git sync once ═══")
for line in lines:
    print(line)
if not ok:
    raise SystemExit(1)
dirty = _collect_dirty_files(REPO_DIR)
if not dirty:
    print("No changes to push.")
else:
    ok = push_change(f"manual: {os.environ.get('REASON', 'sync')}", files=dirty, category="manual_sync")
    print(f"Pushed {len(dirty)} file(s):", "ok" if ok else "failed")
PY
