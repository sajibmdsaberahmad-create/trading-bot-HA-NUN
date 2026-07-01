#!/usr/bin/env python3
"""Find latest halim_toddler_vN.zip on Mac (Downloads + Google Drive Halim/)."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _version(name: str) -> int:
    m = re.search(r"v(\d+)", name, re.I)
    return int(m.group(1)) if m else 0


def search_dirs() -> List[Path]:
    dirs: List[Path] = []
    extra = os.getenv("HALIM_COLAB_SEARCH_DIRS", "")
    for part in extra.split(":"):
        part = part.strip()
        if part:
            dirs.append(Path(part).expanduser())
    downloads = Path.home() / "Downloads"
    if downloads not in dirs:
        dirs.append(downloads)
    cloud = Path.home() / "Library" / "CloudStorage"
    if cloud.is_dir():
        for gd in sorted(cloud.glob("GoogleDrive-*")):
            halim = gd / "My Drive" / "Halim"
            if halim.is_dir() and halim not in dirs:
                dirs.append(halim)
    return dirs


def find_latest_zip(explicit: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file() and p.suffix.lower() == ".zip":
            st = p.stat()
            return {
                "path": str(p.resolve()),
                "name": p.name,
                "version": _version(p.name),
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        return None

    best: Optional[Dict[str, Any]] = None
    for d in search_dirs():
        if not d.is_dir():
            continue
        for p in d.glob("halim_toddler_v*.zip"):
            if not p.is_file():
                continue
            st = p.stat()
            row = {
                "path": str(p.resolve()),
                "name": p.name,
                "version": _version(p.name),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "search_dir": str(d),
            }
            if best is None:
                best = row
                continue
            if row["version"] > best["version"]:
                best = row
            elif row["version"] == best["version"] and row["mtime"] > best["mtime"]:
                best = row
    return best


def main() -> int:
    explicit = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
    row = find_latest_zip(explicit or os.getenv("HALIM_COLAB_ZIP"))
    if not row:
        print(json.dumps({"ok": False, "reason": "no_zip"}))
        return 1
    print(json.dumps({"ok": True, **row}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
