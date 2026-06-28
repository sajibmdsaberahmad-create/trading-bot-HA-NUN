#!/usr/bin/env bash
# Merge council + action gold + coevolution + dialogue → halim/data/training/sft/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi
PY="${PYTHON:-python3}"

# Refresh all gold sources (idempotent dedupe) before SFT merge
"$PY" halim/scripts/export_training_gold.py

"$PY" halim/scripts/prepare_sft.py "$@"

# Always refresh the single canonical Colab zip when SFT changes
if [[ "${HALIM_AUTO_PACKAGE_COLAB:-true}" == "true" ]]; then
  HALIM_SKIP_PREPARE=true "$ROOT/scripts/halim_package_colab.sh"
fi
