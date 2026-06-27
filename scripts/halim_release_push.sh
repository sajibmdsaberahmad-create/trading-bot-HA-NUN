#!/usr/bin/env bash
# Prepare Halim code + toddler checkpoint for GitHub (Git LFS for weights).
#
# Usage:
#   ./scripts/halim_release_push.sh                 # setup LFS + show git status
#   ./scripts/halim_release_push.sh --commit        # stage + commit (no push)
#   ./scripts/halim_release_push.sh --push          # commit + push to origin
#   ./scripts/halim_release_push.sh --new-repo      # init standalone repo under halim-release/
#   ./scripts/halim_release_push.sh --release         # gh release upload halim_toddler_v1.zip
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DO_COMMIT=false
DO_PUSH=false
DO_NEW_REPO=false
DO_RELEASE=false
for arg in "$@"; do
  case "$arg" in
    --commit) DO_COMMIT=true ;;
    --push) DO_COMMIT=true; DO_PUSH=true ;;
    --new-repo) DO_NEW_REPO=true ;;
    --release) DO_RELEASE=true ;;
  esac
done

HALIM_DIR="$ROOT/halim"
CKPT="$HALIM_DIR/data/checkpoints/toddler_v1"
ZIP_SRC="${HALIM_TODDLER_ZIP:-$HOME/Downloads/halim_toddler_v1.zip}"
RELEASE_DIR="$ROOT/halim-release"
GITHUB_REPO="${HALIM_GITHUB_REPO:-sajibmdsaberahmad-create/M-A-Halim}"

echo "══════════════════════════════════════════════════════════════"
echo "  Halim release helper"
echo "  Checkpoint: $CKPT"
echo "  GitHub repo (optional): $GITHUB_REPO"
echo "══════════════════════════════════════════════════════════════"

if [[ ! -f "$CKPT/merged/model.safetensors" ]]; then
  if [[ -f "$ZIP_SRC" ]]; then
    echo "📦 Extracting toddler checkpoint from ${ZIP_SRC}…"
    mkdir -p "$HALIM_DIR/data/checkpoints"
    unzip -o "$ZIP_SRC" -d "$HALIM_DIR/data/checkpoints/"
    "$ROOT/scripts/halim_register_checkpoint.sh" toddler_v1 --backend hf 2>/dev/null || true
  else
    echo "❌ Missing toddler checkpoint and zip: $ZIP_SRC"
    exit 1
  fi
fi

_setup_lfs() {
  local target="$1"
  if ! command -v git-lfs >/dev/null 2>&1; then
    echo "⚠️  git-lfs not installed — brew install git-lfs && git lfs install"
    return 1
  fi
  git lfs install --local 2>/dev/null || git lfs install
  cat >"$target/.gitattributes" <<'EOF'
*.safetensors filter=lfs diff=lfs merge=lfs -text
*.bin filter=lfs diff=lfs merge=lfs -text
*.gguf filter=lfs diff=lfs merge=lfs -text
*.zip filter=lfs diff=lfs merge=lfs -text
EOF
  git lfs track "*.safetensors" "*.bin" "*.gguf" 2>/dev/null || true
}

if [[ "$DO_NEW_REPO" == "true" ]]; then
  echo "📁 Building standalone Halim-only repo at ${RELEASE_DIR}…"
  rm -rf "$RELEASE_DIR"
  mkdir -p "$RELEASE_DIR"
  rsync -a \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='data/actions/action_log.jsonl' \
    --exclude='data/coevolution/correction_log.jsonl' \
    --exclude='data/trading/experience_buffer.jsonl' \
    --exclude='data/trading/council_training_dataset.jsonl' \
    --exclude='data/learn_cache/' \
    "$HALIM_DIR/" "$RELEASE_DIR/"
  mkdir -p "$RELEASE_DIR/data/checkpoints"
  rsync -a "$CKPT/" "$RELEASE_DIR/data/checkpoints/toddler_v1/"
  ln -sfn toddler_v1 "$RELEASE_DIR/data/checkpoints/latest"
  cat >"$RELEASE_DIR/.gitignore" <<'EOF'
__pycache__/
*.py[cod]
.venv/
venv/
.DS_Store
*.log
data/actions/
data/coevolution/
data/learn_cache/
data/trading/experience_buffer.jsonl
data/trading/council_training_dataset.jsonl
EOF
  cat >"$RELEASE_DIR/STANDALONE.md" <<'EOF'
# M. A. Halim — standalone model repo

This repository is **Halim only** — no HANOON trading bot code.

## Clone + run

```bash
git clone https://github.com/sajibmdsaberahmad-create/M-A-Halim.git
cd M-A-Halim
git lfs pull
pip install -e ".[hf]"
export HALIM_REPO_ROOT="$PWD"
export HALIM_LM_BACKEND=hf
export HALIM_MODEL_PATH=data/checkpoints/latest
export HALIM_FORCE_LM=true
python halim/serve.py
```

Health: `curl http://127.0.0.1:8765/health`

## Contents

- `halim/` — Python package (serve, engine, inference)
- `data/checkpoints/toddler_v1/` — Colab-trained toddler LM (Git LFS)
- `scripts/` — train, register checkpoint, prepare SFT
- `colab/` — Google Colab training notebooks
EOF
  cd "$RELEASE_DIR"
  git init -b main
  _setup_lfs "$RELEASE_DIR"
  git add .
  git commit -m "$(cat <<'EOF'
Initial Halim standalone repo — toddler v1 model + training pipeline.

Owned personal AI (M. A. Halim): merged Qwen2.5-0.5B-Instruct checkpoint via Git LFS.
EOF
)"
  if [[ "$DO_PUSH" == "true" ]]; then
    echo "📤 Creating GitHub repo $GITHUB_REPO and pushing…"
    if gh repo view "$GITHUB_REPO" >/dev/null 2>&1; then
      git remote add origin "https://github.com/$GITHUB_REPO.git" 2>/dev/null || \
        git remote set-url origin "https://github.com/$GITHUB_REPO.git"
    else
      gh repo create "$GITHUB_REPO" --public \
        --description "M. A. Halim — owned personal AI model (toddler LM + training pipeline)" \
        --source=. --remote=origin
    fi
    git push -u origin main
    git lfs push origin main --all
    echo "✅ Standalone Halim repo: https://github.com/$GITHUB_REPO"
  else
    git status
    echo ""
    echo "Next:"
    echo "  ./scripts/halim_release_push.sh --new-repo --push"
    echo "  Or: cd halim-release && gh repo create $GITHUB_REPO --public --source=. --push"
  fi
  exit 0
fi

# In-repo: allow LFS-tracked checkpoints under halim/
cat >"$HALIM_DIR/.gitattributes" <<'EOF'
data/checkpoints/**/*.safetensors filter=lfs diff=lfs merge=lfs -text
data/checkpoints/**/*.bin filter=lfs diff=lfs merge=lfs -text
EOF

# Relax gitignore for registered checkpoint only
GITIGNORE="$HALIM_DIR/.gitignore"
if ! grep -q 'toddler_v1' "$GITIGNORE" 2>/dev/null; then
  cat >>"$GITIGNORE" <<'EOF'

# Toddler checkpoint — tracked via Git LFS (halim_release_push.sh)
!data/checkpoints/toddler_v1/
!data/checkpoints/toddler_v1/**
!data/checkpoints/latest
EOF
fi

_setup_lfs "$ROOT" || true

if [[ "$DO_RELEASE" == "true" ]]; then
  RELEASE_ZIP="$ROOT/halim_toddler_v1_release.zip"
  echo "📦 Creating release zip…"
  (cd "$HALIM_DIR/data/checkpoints" && zip -r "$RELEASE_ZIP" toddler_v1)
  if command -v gh >/dev/null 2>&1; then
    TAG="${HALIM_RELEASE_TAG:-halim-toddler-v1}"
    gh release create "$TAG" "$RELEASE_ZIP" \
      --repo "$GITHUB_REPO" \
      --title "Halim Toddler v1" \
      --notes "Colab-trained M. A. Halim toddler LM (merged Qwen2.5-0.5B-Instruct)" \
      2>/dev/null || gh release upload "$TAG" "$RELEASE_ZIP" --repo "$GITHUB_REPO"
    echo "✅ Release uploaded to $GITHUB_REPO"
  else
    echo "✅ Created $RELEASE_ZIP — install gh CLI to upload: gh release create …"
  fi
fi

git add "$HALIM_DIR/.gitattributes" "$HALIM_DIR/.gitignore" 2>/dev/null || true
git add -f "$CKPT" "$HALIM_DIR/data/checkpoints/latest" 2>/dev/null || true
git status --short halim/ 2>/dev/null || git status --short

if [[ "$DO_COMMIT" == "true" ]]; then
  git commit -m "$(cat <<'EOF'
Add Halim toddler checkpoint and release tooling.

Track merged LM weights via Git LFS so Halim can run on any clone.
EOF
)" || echo "Nothing to commit"
fi

if [[ "$DO_PUSH" == "true" ]]; then
  echo "📤 Pushing to origin (requires git-lfs and network)…"
  git push origin HEAD
  git lfs push origin HEAD 2>/dev/null || true
  echo "✅ Push complete"
fi

echo ""
echo "Done. Double-click START_HALIM.command to run Halim + Telegram chat."
