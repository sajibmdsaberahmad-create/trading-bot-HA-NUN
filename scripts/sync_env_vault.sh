#!/usr/bin/env bash
# Encrypt .env → secrets/hanoon.env.enc for safe git sync (private repo only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f ".env" ]; then
  echo "❌ No .env — create from .env.example first"
  exit 1
fi

python3 -c "
from core.env_secrets import encrypt_env_to_vault, vault_paths_for_git
ok = encrypt_env_to_vault(force=True)
paths = vault_paths_for_git()
print('✅ Vault updated:' if ok else '⚠️ Vault skip:', ', '.join(paths) or 'none')
"

echo ""
echo "Files safe to commit (encrypted — never commit plain .env):"
echo "  secrets/hanoon.env.enc"
echo "  secrets/sync.key   (private repo only)"
echo ""
echo "New device: git pull → ./scripts/start_hanoon.sh auto-decrypts .env"
