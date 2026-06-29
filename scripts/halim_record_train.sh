#!/usr/bin/env bash
# After Colab train + Mac install — record hashes so next pack is core+delta only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi

python3 halim/scripts/record_sft_trained.py "$@"
