#!/usr/bin/env bash
# Root launcher — delegates to scripts/start_hanoon.sh
exec "$(dirname "$0")/scripts/start_hanoon.sh" "$@"
