#!/usr/bin/env bash
exec "$(cd "$(dirname "$0")" && pwd)/scripts/full_disk_cleanup.sh" "$@"
