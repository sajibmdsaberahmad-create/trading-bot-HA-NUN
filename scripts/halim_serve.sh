#!/usr/bin/env bash
# M. A. Halim local server — active runtime (status + learn + write). Not inference-only.
# Reflex (PPO/proxy) always stays inline in HANOON; this never replaces fast path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"
if [[ -d venv ]]; then source venv/bin/activate; fi
exec python -m halim.serve "$@"
