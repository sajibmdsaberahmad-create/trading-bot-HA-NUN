#!/bin/bash
# Double-click launcher (macOS) — starts Ollama + HANOON full pilot mode
cd "$(dirname "$0")"
chmod +x start.sh scripts/start_hanoon.sh scripts/stop_hanoon.sh 2>/dev/null || true
exec ./start.sh
