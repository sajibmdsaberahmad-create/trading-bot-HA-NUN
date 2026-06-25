#!/usr/bin/env bash
# Mac Storage Cleaner CLI — run from this folder or anywhere.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/clean.py" "$@"
