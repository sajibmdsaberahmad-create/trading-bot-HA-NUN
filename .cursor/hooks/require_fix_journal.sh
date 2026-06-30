#!/usr/bin/env bash
# Cursor afterFileEdit: remind agents to journal trading-stack changes before commit.
set -euo pipefail

input=$(cat)
file_path=$(echo "$input" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('file_path',''))" 2>/dev/null || true)

if [[ -z "$file_path" ]]; then
  exit 0
fi

case "$file_path" in
  core/*|halim/halim/*|scripts/*.sh|scripts/*.command|.cursor/rules/*)
    ;;
  *)
    exit 0
    ;;
esac

cat <<'EOF'
{"additional_context": "MANDATORY: You edited a trading-stack path. Before finishing or committing, append a full entry to docs/ENGINEERING_FIX_LOG.md (new ## YYYY-MM-DD section: problem, root cause, files, env vars, verify). Git pre-commit will block commits without it. Optional one-liner in docs/BRAIN_DEVELOPMENT_LOG.md."}
EOF
