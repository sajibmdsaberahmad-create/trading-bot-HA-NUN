#!/usr/bin/env bash
# Full safe disk cleanup: tradingbot workspace + macOS caches + Downloads installers.
# Does NOT delete ~/Downloads project folders unless --prune-clones is passed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PRUNE_CLONES=0
SKIP_DEVICE=0
for arg in "$@"; do
  case "$arg" in
    --prune-clones) PRUNE_CLONES=1 ;;
    --workspace-only) SKIP_DEVICE=1 ;;
  esac
done

if [ -d "$ROOT/venv" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

export PYTHONPATH="$ROOT"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  HANOON / tradingbot — full disk cleanup"
echo "══════════════════════════════════════════════════════════"
echo ""

python3 - <<'PY'
import os
from pathlib import Path
from core.local_cleanup import (
    cleanup_device_extras,
    cleanup_local_workspace,
    scan_downloads_clones,
)

def mb(n):
    return n / (1024 * 1024)

ws = cleanup_local_workspace(aggressive=True)
ws_mb = sum(ws.values()) / (1024 * 1024)
print(f"\n📦 Workspace freed: ~{ws_mb:.1f} MB")
for k, v in sorted(ws.items(), key=lambda x: -x[1]):
    if v:
        print(f"   {k}: {mb(v):.1f} MB")

skip_device = os.environ.get("SKIP_DEVICE") == "1"
if not skip_device:
    extras = cleanup_device_extras(remove_download_dmgs=True)
    ex_mb = sum(extras.values()) / (1024 * 1024)
    print(f"\n💻 Device extras freed: ~{ex_mb:.1f} MB")
    for k, v in sorted(extras.items(), key=lambda x: -x[1]):
        if v:
            print(f"   {k}: {mb(v):.1f} MB")

clones = scan_downloads_clones()
if clones:
    total = sum(int(r["bytes"]) for r in clones)
    print(f"\n📁 Old project clones in ~/Downloads (~{mb(total):.0f} MB reclaimable):")
    for row in clones[:15]:
        print(f"   {mb(int(row['bytes'])):6.0f} MB  {row['name']}")
    print("   → Delete manually, or re-run with --prune-clones (moves to Trash)")
PY

if [ "$SKIP_DEVICE" = "1" ]; then
  SKIP_DEVICE=1 python3 -c "pass" 2>/dev/null || true
fi

if [ "$PRUNE_CLONES" = "1" ]; then
  echo ""
  echo "Moving old Downloads clones to Trash…"
  python3 - <<'PY'
import shutil
from pathlib import Path
from core.local_cleanup import ROOT, scan_downloads_clones

trash = Path.home() / ".Trash"
freed = 0
for row in scan_downloads_clones():
    src = Path(row["path"])
    if not src.is_dir():
        continue
    dest = trash / src.name
    if dest.exists():
        dest = trash / f"{src.name}_cleanup"
    try:
        size = int(row["bytes"])
        shutil.move(str(src), str(dest))
        freed += size
        print(f"  🗑  {src.name} → Trash ({size / (1024**2):.0f} MB)")
    except Exception as exc:
        print(f"  ⚠ skip {src.name}: {exc}")
print(f"Moved ~{freed / (1024**2):.0f} MB to Trash")
PY
fi

echo ""
echo "Done. Empty Trash (Finder) to reclaim space from moved folders."
echo ""
