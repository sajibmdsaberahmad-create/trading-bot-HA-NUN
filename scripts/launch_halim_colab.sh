#!/usr/bin/env bash
# Open Halim toddler Colab notebook (Drive-only: v2 zip + halim_sft.zip on My Drive/Halim/)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NB="$ROOT/halim/colab/halim_toddler_train.ipynb"

if [[ ! -f "$NB" ]]; then
  echo "ERROR: $NB not found"
  exit 1
fi

echo "Halim Toddler Colab — Drive-only workflow"
echo ""
echo "1. Upload to My Drive/Halim/ (browser):"
echo "   - halim_toddler_v2.zip"
echo "   - halim_sft.zip  (from $ROOT/halim_sft.zip)"
echo ""
echo "2. Upload $NB to Colab (or open from GitHub)"
echo "3. Runtime → GPU → Run all cells"
echo ""
echo "Guide: halim/colab/COLAB_GUIDE.md"

if command -v open &>/dev/null; then
  open "$NB"
elif command -v xdg-open &>/dev/null; then
  xdg-open "$NB"
else
  echo "Notebook: $NB"
fi
