#!/usr/bin/env bash
# Graceful HANOON shutdown — use this instead of Ctrl+C
exec "$(dirname "$0")/scripts/stop_hanoon.sh" "$@"
