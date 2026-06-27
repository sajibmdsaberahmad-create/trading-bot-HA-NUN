#!/usr/bin/env bash
# Merge council + action gold + coevolution → halim/data/training/sft/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi

# Refresh action gold from journal first
if [[ "${HALIM_SKIP_EXPORT:-false}" != "true" ]]; then
  ./scripts/halim_export_actions.sh >/dev/null 2>&1 || true
fi

python halim/scripts/prepare_sft.py "$@"
