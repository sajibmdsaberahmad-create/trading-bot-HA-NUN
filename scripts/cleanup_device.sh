#!/usr/bin/env bash
# Free disk + RAM on the trading bot Mac without stopping HANOON.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -d "$ROOT/venv" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi
python3 -c "
from core.local_cleanup import cleanup_local_workspace
from core.memory_guard import memory_status, available_ram_mb
from core.config import BotConfig
cfg = BotConfig()
before = available_ram_mb()
stats = cleanup_local_workspace(aggressive=True)
after = available_ram_mb()
mem = memory_status(cfg)
print(f'RAM: {before}MB free → {after}MB free')
print(f'Profile: low_ram={mem[\"low_ram\"]} model={cfg.OLLAMA_MODEL} ollama_budget={cfg.OLLAMA_MEMORY_BUDGET_MB}MB')
print(f'Freed ~{sum(stats.values()) / (1024*1024):.1f}MB disk')
"
