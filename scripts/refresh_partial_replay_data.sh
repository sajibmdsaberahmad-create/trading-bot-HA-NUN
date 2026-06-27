#!/usr/bin/env bash
# Re-download intraday CSVs that have fewer bars than the fullest ticker (IB timeouts).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ -d venv ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
export CLIENT_ID="${CLIENT_ID:-1}"
export IB_CLIENT_ID="${IB_CLIENT_ID:-$CLIENT_ID}"
export IB_PORT="${IB_PORT:-4002}"
PYTHONPATH=. python scripts/download_ib_replay_data.py --refresh-partial "$@"
