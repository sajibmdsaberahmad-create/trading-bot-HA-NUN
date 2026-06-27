#!/usr/bin/env bash
# Zip SFT data for Google Colab upload.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

./scripts/halim_prepare_train.sh 2>/dev/null || true

SFT="halim/data/training/sft"
OUT="halim_sft.zip"

if [[ ! -f "$SFT/train.jsonl" ]]; then
  echo "Missing $SFT/train.jsonl — run ./scripts/halim_prepare_train.sh first"
  exit 1
fi

rm -f "$OUT"
TMP=$(mktemp -d)
cp -R "$SFT" "$TMP/sft"
cp halim/colab/train_toddler_colab.py "$TMP/"
(cd "$TMP" && zip -r "$ROOT/$OUT" sft train_toddler_colab.py)
rm -rf "$TMP"
SIZE=$(du -h "$OUT" | cut -f1)
echo "Created: $ROOT/$OUT ($SIZE)"
echo "Upload this zip to Google Colab (Step 4 in halim/colab/COLAB_GUIDE.md)"
