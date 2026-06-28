#!/usr/bin/env python3
"""
Canonical Colab package — always ONE zip at repo root: halim_sft.zip

- Overwrites in place (never timestamped sibling zips in the repo)
- Removes stale halim_sft*.zip variants in repo root to avoid confusion
- Embeds build_id (content hash) so Colab can verify freshness
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "halim") not in sys.path:
    sys.path.insert(0, str(ROOT / "halim"))

CANONICAL_ZIP = "halim_sft.zip"
META_PATH = "models/halim_sft_package.meta.json"


def repo_root() -> Path:
    import os
    env = os.getenv("HALIM_REPO_ROOT", "").strip()
    return Path(env) if env else ROOT


def _file_sha256(path: Path, *, max_bytes: int = 50_000_000) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            if fh.tell() >= max_bytes:
                break
    return h.hexdigest()[:16]


def _build_id(root: Path, sft_dir: Path) -> str:
    train = sft_dir / "train.jsonl"
    manifest = sft_dir / "manifest.json"
    parts = []
    if train.is_file():
        parts.append(_file_sha256(train))
    if manifest.is_file():
        parts.append(_file_sha256(manifest))
    if not parts:
        return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _remove_stale_zips(root: Path) -> List[str]:
    """Delete old halim_sft* zips in repo root — keep only the canonical name."""
    removed: List[str] = []
    for path in sorted(root.glob("halim_sft*.zip")):
        if path.name == CANONICAL_ZIP:
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


def package_colab_sft(*, root: Path | None = None) -> Dict[str, Any]:
    root = root or repo_root()
    sft_dir = root / "halim/data/training/sft"
    train_path = sft_dir / "train.jsonl"
    valid_path = sft_dir / "valid.jsonl"

    if not train_path.is_file():
        return {
            "ok": False,
            "reason": "missing_train_jsonl",
            "message": "Run ./scripts/halim_prepare_train.sh first",
        }

    manifest: Dict[str, Any] = {}
    manifest_path = sft_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    raw: Dict[str, Any] = {}
    try:
        from halim.dataset import count_raw_sources
        raw = count_raw_sources(root)
    except Exception:
        pass

    build_id = _build_id(root, sft_dir)
    created_at = datetime.now(timezone.utc).isoformat()

    colab_manifest = {
        "package": "halim_sft",
        "version": 3,
        "canonical_file": CANONICAL_ZIP,
        "build_id": build_id,
        "created_at": created_at,
        "train_pairs": manifest.get("train_pairs"),
        "valid_pairs": manifest.get("valid_pairs"),
        "pairs_total": manifest.get("pairs_total"),
        "by_source": manifest.get("by_source"),
        "raw_sources": raw,
        "upload_rule": (
            "Always upload THIS halim_sft.zip from your tradingbot folder. "
            "Do not keep old copies in Downloads — delete them after upload. "
            "Re-run ./scripts/halim_colab_ready.sh before each Colab train."
        ),
    }
    colab_manifest_path = sft_dir / "colab_manifest.json"
    colab_manifest_path.write_text(json.dumps(colab_manifest, indent=2), encoding="utf-8")

    removed = _remove_stale_zips(root)
    zip_path = root / CANONICAL_ZIP
    tmp_zip = root / f".{CANONICAL_ZIP}.tmp"
    train_script = root / "halim/colab/train_toddler_colab.py"

    if tmp_zip.is_file():
        tmp_zip.unlink()

    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in ("train.jsonl", "valid.jsonl", "manifest.json", "colab_manifest.json"):
            fp = sft_dir / rel
            if fp.is_file():
                zf.write(fp, arcname=f"sft/{rel}")
        if train_script.is_file():
            zf.write(train_script, arcname="train_toddler_colab.py")

    tmp_zip.replace(zip_path)
    size_kb = round(zip_path.stat().st_size / 1024, 1)

    meta = {
        "file": CANONICAL_ZIP,
        "path": str(zip_path.resolve()),
        "build_id": build_id,
        "updated_at": created_at,
        "pairs_total": manifest.get("pairs_total"),
        "train_pairs": manifest.get("train_pairs"),
        "by_source": manifest.get("by_source"),
        "removed_stale_zips": removed,
        "upload_rule": colab_manifest["upload_rule"],
    }
    meta_file = root / META_PATH
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "zip": str(zip_path.resolve()),
        "build_id": build_id,
        "size_kb": size_kb,
        "pairs_total": manifest.get("pairs_total"),
        "removed_stale_zips": removed,
        "meta_path": str(meta_file),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical halim_sft.zip for Colab")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    result = package_colab_sft(root=args.root)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
