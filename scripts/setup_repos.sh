#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════════
# scripts/setup_repos.sh — Initialize Multi-Repo GitHub Architecture
# ════════════════════════════════════════════════════════════════════════════════
# Creates three separate GitHub repositories:
#   1. HA-NUN     — Live trading bot code + config + lightweight weights
#   2. Grandmaster — Heavy model weights (210M Teacher / 21M Student)
#   3. Logs       — Raw market data, backtest results, daily reports
#
# Usage:
#   chmod +x scripts/setup_repos.sh
#   ./scripts/setup_repos.sh
#
# Prerequisites:
#   - GitHub CLI (`gh`) installed and authenticated
#   - GITHUB_TOKEN env var set with repo creation permissions
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Config
ORG="${GITHUB_ORG:-sajibmdsaberahmad-create}"
BASE_NAME="${GITHUB_REPO_BASE:-trading-bot}"
TOKEN="${GITHUB_TOKEN:-}"

if [ -z "$TOKEN" ]; then
    echo "❌ GITHUB_TOKEN not set. Export it first:"
    echo "   export GITHUB_TOKEN=ghp_..."
    exit 1
fi

echo "🏗️  Setting up multi-repo architecture..."
echo "   Organization: $ORG"
echo ""

# ════════════════════════════════════════════════════════════════════════════════
# 1. HA-NUN Repo (Primary Live Trading Bot)
# ════════════════════════════════════════════════════════════════════════════════
REPO_HA_NUN="${ORG}/${BASE_NAME}-HA-NUN"
echo "📦 Creating HA-NUN repo: $REPO_HA_NUN"

if gh repo view "$REPO_HA_NUN" &>/dev/null; then
    echo "   ⚠️  Repo already exists, skipping creation"
else
    gh repo create "$REPO_HA_NUN" --public --description "HA-NUN Live Trading Bot | Multi-Paradigm AI Ecosystem | Code + Config + Lightweights"
    echo "   ✅ Created"
fi

# Initialize local HA-NUN repo if not already a git repo
if [ ! -d ".git" ]; then
    git init
    git remote remove origin 2>/dev/null || true
    git remote add origin "https://${TOKEN}@github.com/${REPO_HA_NUN}.git"
    git branch -M main
fi

echo ""

# ════════════════════════════════════════════════════════════════════════════════
# 2. Grandmaster Repo (Model Weights)
# ════════════════════════════════════════════════════════════════════════════════
REPO_GRANDMASTER="${ORG}/${BASE_NAME}-Grandmaster"
echo "🏛️  Creating Grandmaster repo: $REPO_GRANDMASTER"

if gh repo view "$REPO_GRANDMASTER" &>/dev/null; then
    echo "   ⚠️  Repo already exists, skipping creation"
else
    gh repo create "$REPO_GRANDMASTER" --public --description "Grandmaster Distillation | 210M Teacher → 21M Student | Model Weights Only"
    echo "   ✅ Created"
fi

echo ""

# ════════════════════════════════════════════════════════════════════════════════
# 3. Logs Repo (Raw Data — separate to avoid bloat)
# ════════════════════════════════════════════════════════════════════════════════
REPO_LOGS="${ORG}/${BASE_NAME}-Logs"
echo "📊 Creating Logs repo: $REPO_LOGS"

if gh repo view "$REPO_LOGS" &>/dev/null; then
    echo "   ⚠️  Repo already exists, skipping creation"
else
    gh repo create "$REPO_LOGS" --public --description "Trading Bot Logs | Raw Market Data | Backtest Results | Daily Reports"
    echo "   ✅ Created"
fi

echo ""

# ════════════════════════════════════════════════════════════════════════════════
# Configure .gitignore for multi-repo
# ════════════════════════════════════════════════════════════════════════════════
echo "🔧 Configuring .gitignore and directory structure..."

# Ensure .gitignore exists and bloat guard is active
if [ ! -f ".gitignore" ]; then
    echo "❌ .gitignore not found. Please create it first."
    exit 1
fi

# Create directory markers
mkdir -p data/logs backups checkpoints models/daily_reports
touch data/.gitkeep logs/.gitkeep models/daily_reports/.gitkeep

# ════════════════════════════════════════════════════════════════════════════════
# Update core/config.py with repo URLs (if not already set)
# ════════════════════════════════════════════════════════════════════════════════
echo "🔑 Updating config with repo URLs..."

python3 -c "
import re
with open('core/config.py', 'r') as f:
    content = f.read()

# Set repo URLs if they use defaults
if 'GITHUB_HA_NUN_REPO: str = os.getenv(\"GITHUB_HA_NUN_REPO\", \"\")' in content:
    content = content.replace(
        'GITHUB_HA_NUN_REPO: str = os.getenv(\"GITHUB_HA_NUN_REPO\", \"\")',
        f'GITHUB_HA_NUN_REPO: str = os.getenv(\"GITHUB_HA_NUN_REPO\", \"https://github.com/{REPO_HA_NUN}.git\")'
    )

if 'GITHUB_GRANDMASTER_REPO: str = os.getenv(\"GITHUB_GRANDMASTER_REPO\", \"\")' in content:
    content = content.replace(
        'GITHUB_GRANDMASTER_REPO: str = os.getenv(\"GITHUB_GRANDMASTER_REPO\", \"\")',
        f'GITHUB_GRANDMASTER_REPO: str = os.getenv(\"GITHUB_GRANDMASTER_REPO\", \"https://github.com/{REPO_GRANDMASTER}.git\")'
    )

if 'GITHUB_LOGS_REPO: str = os.getenv(\"GITHUB_LOGS_REPO\", \"\")' in content:
    content = content.replace(
        'GITHUB_LOGS_REPO: str = os.getenv(\"GITHUB_LOGS_REPO\", \"\")',
        f'GITHUB_LOGS_REPO: str = os.getenv(\"GITHUB_LOGS_REPO\", \"https://github.com/{REPO_LOGS}.git\")'
    )

with open('core/config.py', 'w') as f:
    f.write(content)
print('✅ Config updated with repo URLs')
"

echo ""

# ════════════════════════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════════════════════════
echo "═══════════════════════════════════════════════════════════════════════"
echo "✅ Multi-Repo Architecture Setup Complete"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""
echo "📦 HA-NUN (Live Trading Bot):"
echo "   $REPO_HA_NUN"
echo "   Contents: Code, config, scalper_weights.json, performance.csv"
echo ""
echo "🏛️  Grandmaster (Model Weights):"
echo "   $REPO_GRANDMASTER"
echo "   Contents: transformer_model.pth, lstm_model.h5, scalper_weights.json"
echo ""
echo "📊 Logs (Raw Data):"
echo "   $REPO_LOGS"
echo "   Contents: data/live_market_features.csv, backtest_results, daily_reports"
echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "Next steps:"
echo "  1. git add . && git commit -m 'chore: multi-repo architecture'"
echo "  2. git push origin main"
echo "  3. Configure .env with GITHUB_TOKEN, GITHUB_HA_NUN_REPO, etc."
echo "═══════════════════════════════════════════════════════════════════════"