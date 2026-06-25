#!/usr/bin/env bash
# Redirects to standalone mac-cleaner folder.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/mac-cleaner/mac-clean.sh" "$@"
