#!/usr/bin/env bash
# Clear shadow circuit breaker — restores real IB bracket orders (paper or live)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

python3 -c "
from core.config import BotConfig
from core.shadow_mode import ShadowCircuitBreaker

cfg = BotConfig()
sc = ShadowCircuitBreaker(cfg)
if sc.force_resume_live('resume_ib_trading.sh'):
    print('✅ Shadow cleared — next entries will place real IB orders')
else:
    print('✅ Already routing to IB (shadow was not blocking)')
"
