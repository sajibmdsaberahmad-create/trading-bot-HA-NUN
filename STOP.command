#!/bin/bash
# Double-click graceful stop (macOS) — syncs git, disconnects IB, unloads Ollama
cd "$(dirname "$0")"
chmod +x scripts/stop_hanoon.sh stop.sh 2>/dev/null || true
exec ./scripts/stop_hanoon.sh
