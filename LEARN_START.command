#!/bin/bash
# Double-click — Halim continuously reads the web (maintenance / off-hours) and earns action gold.
# Stops automatically when you start HANOON/replay. Ctrl+C to stop manually.
cd "$(dirname "$0")"
chmod +x scripts/halim_learn_browse.sh LEARN_START.command 2>/dev/null || true
export HALIM_LEARN_LOOP=true
exec ./scripts/halim_learn_browse.sh
