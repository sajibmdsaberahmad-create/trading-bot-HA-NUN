#!/usr/bin/env bash
# Incremental Colab pack: core curriculum + gold since last completed train (~1.5–2.5k pairs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export HALIM_SFT_MODE=core_delta
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi

echo "== Halim incremental SFT (core + new delta) =="
"$ROOT/scripts/halim_prepare_train.sh" --mode core_delta --min-pairs "${HALIM_CORE_DELTA_MIN_PAIRS:-400}"
echo ""
echo "Upload halim_sft.zip to Colab. Use HALIM_CONTINUE_LORA=auto and Drive OUT_DIR (see COLAB_GUIDE.md)."
