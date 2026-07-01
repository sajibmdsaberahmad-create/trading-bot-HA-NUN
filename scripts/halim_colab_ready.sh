#!/usr/bin/env bash
# One-shot upgrade: PPO live model + export all gold + SFT + Colab zip + readiness.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source "$ROOT/scripts/halim_env.sh"
if [[ -d "$ROOT/venv" ]]; then source "$ROOT/venv/bin/activate"; fi
PY="${PYTHON:-python3}"

echo "== Halim Colab-ready upgrade =="

# 1. Live PPO reflex (promote from replay only if walk-forward gate passes)
if [[ -f "$ROOT/models/ppo_trader_replay.zip" ]]; then
  PROMOTE=$("$PY" -c "
from core.promotion_gate import try_promote_ppo_replay
from core.config import BotConfig
r = try_promote_ppo_replay(BotConfig())
print('yes' if r.get('promoted') else 'no')
" 2>/dev/null || echo "no")
  if [[ "$PROMOTE" == "yes" ]]; then
    echo "✓ Live PPO: models/ppo_trader.zip ← replay (gate passed)"
  elif [[ ! -f "$ROOT/models/ppo_trader.zip" ]]; then
    cp "$ROOT/models/ppo_trader_replay.zip" "$ROOT/models/ppo_trader.zip"
    echo "✓ Live PPO: models/ppo_trader.zip ← replay (no incumbent)"
  else
    echo "⏸ Live PPO unchanged — promotion gate blocked (replay metrics below threshold)"
  fi
else
  echo "⚠ No models/ppo_trader_replay.zip — run replay first for best PPO"
fi

# 2. Export all training gold (deduped) + JSON entry curriculum for v5
echo "→ Exporting training gold…"
if [[ "${HALIM_JSON_ENTRY_API:-false}" == "true" ]] || [[ "${HALIM_V5_PREP:-false}" == "true" ]]; then
  export HALIM_JSON_ENTRY_API=true
  echo "  (API teacher ON — HALIM_JSON_ENTRY_API_MAX=${HALIM_JSON_ENTRY_API_MAX:-120})"
fi
"$PY" "$ROOT/halim/scripts/export_training_gold.py"

# 3. Merge SFT train/valid (+ auto-rebuild halim_sft.zip)
echo "→ Preparing SFT dataset + canonical Colab zip…"
HALIM_AUTO_PACKAGE_COLAB=true "$ROOT/scripts/halim_prepare_train.sh" --min-pairs "${HALIM_TODDLER_MIN_PAIRS:-2500}"

# 4. Sync identity + manifest
"$PY" - <<'PY'
import json
from core.halim_identity import sync_identity_phase, write_halim_manifest
from core.config import BotConfig
cfg = BotConfig()
phase = sync_identity_phase(cfg)
manifest = write_halim_manifest(cfg)
print(json.dumps({"phase": phase, "manifest_updated": bool(manifest)}, indent=2))
PY

# 5. Verify Colab package meta (zip already built by prepare_train)
echo "→ Colab package status…"
if [[ -f "$ROOT/halim_sft.zip" ]]; then
  "$PY" - <<'PY'
import json
from pathlib import Path
meta = Path("models/halim_sft_package.meta.json")
if meta.is_file():
    print(json.dumps(json.loads(meta.read_text()), indent=2))
else:
    print('{"warning": "halim_sft_package.meta.json missing"}')
PY
else
  HALIM_SKIP_PREPARE=true "$ROOT/scripts/halim_package_colab.sh"
fi

# 6. Readiness report
echo ""
echo "== Readiness =="
"$ROOT/scripts/halim_readiness.sh" || true

echo ""
echo "Next: upload halim_sft.zip to My Drive/Halim/ (toddler_vN.zip only if toddler_v1/ missing) → halim/colab/halim_toddler_train.ipynb"
echo "Guide: halim/colab/COLAB_GUIDE.md"
