#!/usr/bin/env bash
# Permanently remove IDE RAM hogs (Amazon Q, Gemini Code Assist, Cloud Code CLI).
# Safe to run repeatedly — idempotent. Never touches IB Gateway or trading stack.
set -euo pipefail

CURSOR_EXT="${HOME}/.cursor/extensions"
CLOUD_CODE="${HOME}/Library/Application Support/cloud-code"
MARKER="${HOME}/.cursor/tradingbot-ide-hogs-removed"

removed=0
note() { echo "  🗑  $1"; removed=$((removed + 1)); }

echo "🧹 Removing IDE RAM hogs (permanent)…"

# Kill running sidecars first
for pattern in \
  "Amazon Q Helper" \
  "cloudcode_cli duet" \
  "geminicodeassist.*/agent/a2a-server" \
  "codewhisperer" \
  ; do
  pkill -TERM -f "$pattern" 2>/dev/null || true
done
sleep 1
for pattern in \
  "Amazon Q Helper" \
  "cloudcode_cli duet" \
  "geminicodeassist.*/agent/a2a-server" \
  ; do
  pkill -KILL -f "$pattern" 2>/dev/null || true
done

# Cursor extensions — Amazon Q + Gemini Code Assist + CodeWhisperer companion
if [[ -d "$CURSOR_EXT" ]]; then
  for prefix in \
    "amazonwebservices.amazon-q-vscode-" \
    "amazonwebservices.codewhisperer-for-command-line-companion-" \
    "google.geminicodeassist-" \
    ; do
    for dir in "$CURSOR_EXT"/${prefix}*; do
      [[ -d "$dir" ]] || continue
      rm -rf "$dir"
      note "Removed extension $(basename "$dir")"
    done
  done
fi

# Google Cloud Code CLI cache (duet respawns from here)
if [[ -d "$CLOUD_CODE" ]]; then
  rm -rf "$CLOUD_CODE"
  note "Removed cloud-code Application Support"
fi

# Block re-install prompts in this workspace
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/.vscode"
if [[ ! -f "$ROOT/.vscode/extensions.json" ]] || ! grep -q "amazon-q-vscode" "$ROOT/.vscode/extensions.json" 2>/dev/null; then
  cat >"$ROOT/.vscode/extensions.json" <<'EOF'
{
  "unwantedRecommendations": [
    "amazonwebservices.amazon-q-vscode",
    "amazonwebservices.codewhisperer-for-command-line-companion",
    "google.geminicodeassist"
  ]
}
EOF
  note "Wrote .vscode/extensions.json blocklist"
fi

date -u +"%Y-%m-%dT%H:%M:%SZ" >"$MARKER"

# Strip Amazon Q / Gemini settings so Cursor stops respawning sidecars
SETTINGS="${HOME}/Library/Application Support/Cursor/User/settings.json"
if [[ -f "$SETTINGS" ]]; then
  python3 - "$SETTINGS" <<'PY' && note "Cleaned Cursor settings (Amazon Q / Gemini keys)" || true
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
data = json.loads(p.read_text())
strip_prefix = ("amazonQ.", "geminicodeassist.", "codewhisperer.")
keys = [k for k in list(data) if k.startswith(strip_prefix)]
for k in keys:
    del data[k]
term = data.get("terminal.integrated.env.osx")
if isinstance(term, dict) and "Q_NEW_SESSION" in term:
    del term["Q_NEW_SESSION"]
    if not term:
        del data["terminal.integrated.env.osx"]
if keys or term is not None:
    p.write_text(json.dumps(data, indent=2) + "\n")
PY
fi

if [[ "$removed" -eq 0 ]]; then
  echo "✅ IDE RAM hogs already removed"
else
  echo "✅ Removed $removed item(s) — ~500–900MB freed, sidecars will not respawn"
fi
echo "   ↳ Reload Cursor once (Cmd+Shift+P → Developer: Reload Window) to drop ghost extension hosts."
