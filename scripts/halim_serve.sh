#!/usr/bin/env bash
# M. A. Halim local server — active runtime (status + learn + write). Not inference-only.
# Reflex (PPO/proxy) always stays inline in HANOON; this never replaces fast path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HALIM_REPO_ROOT="$ROOT"
export PYTHONPATH="$ROOT/halim:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ -d venv ]]; then source venv/bin/activate; fi
exec python -m halim.serve "$@"
