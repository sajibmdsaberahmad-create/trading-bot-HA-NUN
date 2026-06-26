#!/usr/bin/env bash
# Publish a clean HANOON algo snapshot → https://github.com/sajibmdsaberahmad-create/HANOON
# Does NOT modify trading-bot-HA-NUN or other repos.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HANOON_REPO="${HANOON_REPO:-https://github.com/sajibmdsaberahmad-create/HANOON.git}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
WORKDIR="$(mktemp -d /tmp/hanoon-publish.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "═══════════════════════════════════════════════════════════════"
echo "  HANOON clean publish → $HANOON_REPO"
echo "  Workdir: $WORKDIR"
echo "═══════════════════════════════════════════════════════════════"

# Refresh encrypted env vault from local .env
if [ -f ".env" ]; then
  python3 -c "from core.env_secrets import encrypt_env_to_vault; encrypt_env_to_vault(force=True)" 2>/dev/null || true
fi

# ── Core runtime ─────────────────────────────────────────────────────────────
mkdir -p "$WORKDIR/core" "$WORKDIR/scripts" "$WORKDIR/models/daily_reports" "$WORKDIR/models/daily_ib_learning" "$WORKDIR/secrets" "$WORKDIR/runtime" "$WORKDIR/docs"

cp -R core/. "$WORKDIR/core/"
cp main.py requirements.txt start.sh stop.sh "$WORKDIR/"
cp START.command STOP.command "$WORKDIR/" 2>/dev/null || true
cp .env.example "$WORKDIR/"
cp .gitignore.hanoon "$WORKDIR/.gitignore"
cp README.md "$WORKDIR/"

# Scripts (trading only)
for f in start_hanoon.sh stop_hanoon.sh start_git_sync.sh git_auto_push.py bootstrap_from_release.sh publish_hanoon_repo.sh release_hanoon.sh; do
  [ -f "scripts/$f" ] && cp "scripts/$f" "$WORKDIR/scripts/"
done
chmod +x "$WORKDIR/scripts/"*.sh "$WORKDIR"/*.sh "$WORKDIR"/*.command 2>/dev/null || true

# PPO model (required — no retrain on new device)
if [ -f "ppo_trader.zip" ]; then
  cp ppo_trader.zip "$WORKDIR/"
else
  echo "⚠️  ppo_trader.zip missing — run bootstrap_from_release.sh after clone"
fi

# Essential AI state (learning preserved, no rolling jsonl bloat)
ESSENTIAL_MODELS=(
  models/README.md
  models/consciousness.json
  models/cognitive_state.json
  models/scalper_weights.json
  models/pilot_experience.json
  models/ai_guidelines.txt
  models/trader_directives.txt
  models/daily_guidelines.txt
  models/parameter_adjustments.json
  models/improvement_history.json
  models/training_history.json
  models/feature_manifest.json
  models/model_manifest.json
  models/ai_session_limits.json
  models/architecture_epoch.json
  models/shadow_circuit_state.json
  models/market_data_denylist.json
  models/telegram_verified.json
)
for f in "${ESSENTIAL_MODELS[@]}"; do
  [ -f "$f" ] && cp "$f" "$WORKDIR/$f"
done
touch "$WORKDIR/models/daily_reports/.gitkeep"
touch "$WORKDIR/models/daily_ib_learning/.gitkeep"
touch "$WORKDIR/runtime/.gitkeep"

# Encrypted secrets (cross-device .env)
cp secrets/.gitkeep "$WORKDIR/secrets/" 2>/dev/null || true
[ -f secrets/hanoon.env.enc ] && cp secrets/hanoon.env.enc "$WORKDIR/secrets/"
[ -f secrets/sync.key ] && cp secrets/sync.key "$WORKDIR/secrets/"

# Minimal docs
[ -f docs/LAUNCH_GUIDE.md ] && cp docs/LAUNCH_GUIDE.md "$WORKDIR/docs/"

# ── Git init & push ──────────────────────────────────────────────────────────
cd "$WORKDIR"
git init -b main
git add -A
git -c user.name="HANOON Publisher" -c user.email="hanoon@local" commit -m "$(cat <<EOF
HANOON algo snapshot $STAMP

Clean portable bundle: core, scripts, PPO model, AI learning state, encrypted env vault.
Clone → ./start.sh on any device.
EOF
)"

# Push (use gh credential or GITHUB_TOKEN from parent .env)
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

PUSH_URL="$HANOON_REPO"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  PUSH_URL="https://${GITHUB_TOKEN}@github.com/sajibmdsaberahmad-create/HANOON.git"
fi

git remote add origin "$PUSH_URL"
git push -u origin main --force

echo ""
echo "✅ Published clean HANOON repo"
echo "   Clone: git clone $HANOON_REPO"
echo "   Run:   ./start.sh"
