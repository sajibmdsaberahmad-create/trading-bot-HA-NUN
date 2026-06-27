#!/usr/bin/env bash
# Train Halim toddler LM (16GB+ Mac) or print GPU transfer instructions (8GB).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi

./scripts/halim_prepare_train.sh
python halim/scripts/train_toddler.py "$@"
