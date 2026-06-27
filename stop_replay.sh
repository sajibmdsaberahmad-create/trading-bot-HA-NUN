#!/usr/bin/env bash
# Graceful replay shutdown — evolution + Halim gold + git sync
exec "$(dirname "$0")/scripts/stop_replay.sh" "$@"
