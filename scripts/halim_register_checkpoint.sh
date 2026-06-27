#!/usr/bin/env bash
# Point halim/data/checkpoints/latest at a trained checkpoint dir.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi
NAME="${1:-toddler_v1}"
shift || true
python halim/scripts/register_checkpoint.py "$NAME" "$@"
