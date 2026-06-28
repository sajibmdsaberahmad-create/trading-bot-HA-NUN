#!/usr/bin/env bash
# Build the ONE canonical Colab zip: halim_sft.zip (always overwritten, never duplicated).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi
PY="${PYTHON:-python3}"

# Optional full refresh before packaging (skip when caller already ran prepare)
if [[ "${HALIM_SKIP_PREPARE:-false}" != "true" ]]; then
  "$ROOT/scripts/halim_prepare_train.sh" 2>/dev/null || true
fi

result=$("$PY" "$ROOT/halim/scripts/package_colab_sft.py")
echo "$result"
echo "$result" | "$PY" -c "import json,sys; r=json.load(sys.stdin); sys.exit(0 if r.get('ok') else 1)"

build_id=$(echo "$result" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('build_id','?'))")
pairs=$(echo "$result" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('pairs_total','?'))")
removed=$(echo "$result" | "$PY" -c "import json,sys; print(','.join(json.load(sys.stdin).get('removed_stale_zips') or []) or 'none')")

echo ""
echo "✓ Canonical Colab package: $ROOT/halim_sft.zip"
echo "  build_id=$build_id · pairs=$pairs"
if [[ "$removed" != "none" ]]; then
  echo "  removed stale zips: $removed"
fi
echo "  Upload ONLY this file to Colab — delete any older halim_sft*.zip in Downloads."
echo "  Meta: models/halim_sft_package.meta.json"
