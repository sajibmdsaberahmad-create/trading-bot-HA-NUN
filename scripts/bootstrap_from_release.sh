#!/usr/bin/env bash
# Download ppo_trader.zip from latest GitHub Release if missing (optional slim clone).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPO="${GITHUB_HANOON_REPO:-sajibmdsaberahmad-create/HANOON}"
MODEL="ppo_trader.zip"

if [ -f "$MODEL" ]; then
  echo "✅ $MODEL already present"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "⚠️  $MODEL missing and gh CLI not installed — full repo clone includes the model"
  exit 1
fi

echo "📥 Downloading $MODEL from latest $REPO release…"
gh release download --repo "$REPO" --pattern "$MODEL" --dir "$ROOT" || {
  echo "No release asset found — use full git clone (ppo_trader.zip is in repo)"
  exit 1
}
echo "✅ $MODEL ready"
