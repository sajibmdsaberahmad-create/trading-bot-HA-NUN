#!/usr/bin/env bash
# Create GitHub Release with ppo_trader.zip (optional mirror for slim clones).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPO="${GITHUB_HANOON_REPO:-sajibmdsaberahmad-create/HANOON}"
TAG="v$(date -u +%Y.%m.%d)-$(git rev-parse --short HEAD 2>/dev/null || echo manual)"

[ -f ppo_trader.zip ] || { echo "ppo_trader.zip missing"; exit 1; }

if [ -f .env ]; then set -a; source .env; set +a; fi

echo "Creating release $TAG on $REPO…"
gh release create "$TAG" ppo_trader.zip \
  --repo "$REPO" \
  --title "HANOON PPO $TAG" \
  --notes "Canonical PPO weights for HANOON algo repo. Included in git; release is optional mirror."

echo "✅ Release $TAG published"
