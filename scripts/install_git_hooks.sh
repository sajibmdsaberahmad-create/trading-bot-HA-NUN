#!/usr/bin/env bash
# Install repo git hooks (fix-journal pre-commit).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SRC="$ROOT/scripts/git-hooks/pre-commit"
HOOK_DST="$ROOT/.git/hooks/pre-commit"

if [[ ! -d "$ROOT/.git" ]]; then
  echo "Not a git repository: $ROOT"
  exit 1
fi

mkdir -p "$ROOT/.git/hooks"
cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_SRC" "$HOOK_DST"
echo "Installed pre-commit hook -> .git/hooks/pre-commit"
echo "Trading-stack commits require a new section in docs/ENGINEERING_FIX_LOG.md"
