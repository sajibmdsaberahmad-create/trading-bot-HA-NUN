#!/usr/bin/env bash
# Pre-flight for MacBook Air M2 8 GB — run before RTH or after Halim install.
# Exit 0 = ready to launch; non-zero = fix items printed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"
RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}' || echo 8192)

echo "══════════════════════════════════════════════════════════════"
echo "  HANOON M2 preflight (RAM=${RAM_MB}MB)"
echo "══════════════════════════════════════════════════════════════"

FAIL=0

# Profile snippet
if [[ "$RAM_MB" -le 12288 && -f "$ROOT/scripts/m2_8gb_live_profile.sh" ]]; then
  export HANOON_DEVICE_PROFILE_ROOT="$ROOT"
  # shellcheck disable=SC1091
  source "$ROOT/scripts/m2_8gb_live_profile.sh"
  echo "✓ Profile: ${HANOON_DEVICE_PROFILE:-m2_8gb_live}"
else
  echo "ℹ Profile: full-RAM Mac (m2_8gb_live_profile skipped)"
fi

# venv
if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
  echo "✓ venv active"
else
  echo "✗ venv missing — run: python3 -m venv venv && pip install -r requirements.txt"
  FAIL=1
fi

# Halim checkpoint
if [[ -f "$ROOT/halim/data/checkpoints/toddler_v1/merged/model.safetensors" ]] \
   || [[ -f "$ROOT/halim/data/checkpoints/toddler_v1/lora_adapter/adapter_model.safetensors" ]]; then
  echo "✓ Halim checkpoint on disk"
else
  echo "✗ Halim checkpoint missing — ./scripts/halim_apply_colab_checkpoint.sh"
  FAIL=1
fi

# Halim serve
if curl -sf --max-time 3 http://127.0.0.1:8765/health >/dev/null 2>&1; then
  echo "✓ Halim serve :8765"
else
  echo "⚠ Halim serve down — ./scripts/ensure_halim_active.sh --serve-only --restart"
fi

# IB port (paper default)
IB_PORT="${IB_PORT:-4002}"
if [[ "$IB_PORT" == "4001" ]] && [[ "${HANOON_LIVE_MONEY_ACK:-false}" != "true" ]]; then
  echo "✗ IB_PORT=4001 without HANOON_LIVE_MONEY_ACK=true"
  FAIL=1
else
  echo "✓ IB_PORT=$IB_PORT (paper ok)"
fi

# Python gates smoke
python3 - <<'PY' || FAIL=1
import os
from core.config import BotConfig
from core.green_trade_doctrine import green_entry_mandatory
from core.smart_stack import live_ram_only
from core.capital_discipline import effective_min_profit_probability

cfg = BotConfig()
assert live_ram_only(cfg) or os.getenv("RAM_LIVE_ONLY", "").lower() in ("1", "true", "yes", "")
eff = effective_min_profit_probability(cfg)
print("✓ RAM_LIVE_ONLY / smart_stack ok")
print(f"  green_mandatory={green_entry_mandatory(cfg)}")
print(f"  await_sec={os.getenv('HALIM_ENTRY_AWAIT_SEC', '?')}")
print(f"  strict_prob={os.getenv('SMART_STACK_STRICT_PROFIT_PROB', '?')}")
print(f"  min_profit_env={os.getenv('MIN_PROFIT_PROBABILITY', '?')}")
print(f"  commander_runtime={os.getenv('COMMANDER_RUNTIME_ENABLED', '?')}")
print(f"  effective_min_profit={eff:.2f}")
if os.getenv("HANOON_M2_CANONICAL_LIVE", "").lower() in ("1", "true", "yes"):
    if eff > 0.60:
        print(f"✗ effective_min_profit {eff:.2f} > 0.60 on M2 canonical")
        raise SystemExit(1)
PY

# Disk (read-only)
if [[ -x "$ROOT/scripts/disk_audit.sh" ]]; then
  echo ""
  "$ROOT/scripts/disk_audit.sh" 2>/dev/null | head -20 || true
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "✅ Preflight passed — ./scripts/start_hanoon.sh"
else
  echo "❌ Preflight failed — fix items above"
  exit 1
fi
