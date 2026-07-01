#!/usr/bin/env bash
# Halim v5 training pack — read-only web browse + API enrichment → halim_sft.zip
#
# Runs off-hours (pauses if HANOON scalper is live unless you override).
# Usage:
#   ./scripts/halim_v5_ready.sh                 # full pack (~12 learn cycles + API)
#   ./scripts/halim_v5_ready.sh --skip-learn    # gold/API/SFT only (fast)
#   HALIM_V5_LEARN_CYCLES=20 ./scripts/halim_v5_ready.sh
#   HALIM_LEARN_DURING_TRADING=true ./scripts/halim_v5_ready.sh  # beside live bot
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

export HALIM_V5_PREP=true
export HALIM_WEB_LEARN=true
export HALIM_OPERATOR_SETTINGS=true
export HALIM_GOOGLE_AI_SEARCH=true
export HALIM_LEARN_GOOGLE_SNIPPETS=true
export HALIM_JSON_ENTRY_API=true
export HALIM_LEARN_UNCAPPED_DATE="$(date -u +%Y-%m-%d)"

# Raised read-only caps for v5 pack (override halim_env defaults; still no external writes)
export HALIM_V5_LEARN_CYCLES=12
export HALIM_V5_MAX_FETCHES=2500
export HALIM_V5_API_DAILY_CAP=2000
export HALIM_JSON_ENTRY_API_MAX=500
export HALIM_V5_WEB_DRILL_MAX=80
export HALIM_LEARN_BATCH_MAX=12
export HALIM_LEARN_GOOGLE_MAX=8
export HALIM_GOOGLE_AI_DAILY_CAP=400
export HALIM_LEARN_UNCAPPED_MAX_FETCHES=2500
export HALIM_LEARN_UNCAPPED_MAX_GOLD=300

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

SKIP_LEARN=""
FORCE_FLAG=""
for arg in "$@"; do
  case "$arg" in
    --skip-learn) SKIP_LEARN="--skip-learn" ;;
    --no-force-learn) FORCE_FLAG="--no-force-learn" ;;
  esac
done

if pgrep -f "main.py --mode (scalper|replay-live)" >/dev/null 2>&1 \
   && [[ "${HALIM_LEARN_DURING_TRADING:-false}" != "true" ]]; then
  echo "⚠️  HANOON is trading — v5 prep will skip web browse (use --skip-learn or stop bot)."
  echo "   Or: HALIM_LEARN_DURING_TRADING=true $0"
  SKIP_LEARN="--skip-learn"
fi

echo "══════════════════════════════════════════════════════════════"
echo "  M. A. HALIM — v5 training pack (read-only web + API → Colab)"
echo "  Learn cycles: ${HALIM_V5_LEARN_CYCLES} | JSON API max: ${HALIM_JSON_ENTRY_API_MAX}"
echo "  Web drills API max: ${HALIM_V5_WEB_DRILL_MAX} | Fetch cap: ${HALIM_V5_MAX_FETCHES}"
echo "══════════════════════════════════════════════════════════════"

PYTHONPATH="$ROOT/halim:$ROOT" python3 -m core.halim_v5_prep \
  ${SKIP_LEARN} \
  --learn-cycles "${HALIM_V5_LEARN_CYCLES}" \
  ${FORCE_FLAG}

echo ""
echo "Next: upload halim_sft.zip → My Drive/Halim/ → halim/colab/halim_toddler_train.ipynb"
echo "Guide: halim/colab/COLAB_GUIDE.md"

if [[ -t 0 ]]; then
  read -r -p "Press Enter to close…" _ || true
fi
