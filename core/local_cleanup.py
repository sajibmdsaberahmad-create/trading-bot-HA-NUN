#!/usr/bin/env python3
"""
core/local_cleanup.py — Free disk/RAM after git push on bot shutdown.
"""

from __future__ import annotations

import glob
import os
import shutil
import time
from pathlib import Path
from typing import List

from core.notify import log

ROOT = Path(__file__).resolve().parent.parent


def _rm_glob(patterns: List[str]) -> int:
    freed = 0
    for pat in patterns:
        for p in glob.glob(str(ROOT / pat), recursive=True):
            try:
                path = Path(p)
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink(missing_ok=True)
                elif path.is_dir() and "__pycache__" in str(path):
                    freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass
    return freed


def _trim_log(log_path: Path, max_mb: float = 2.0) -> None:
    """Keep tail of huge logs after push to remote."""
    if not log_path.exists():
        return
    size_mb = log_path.stat().st_size / (1024 * 1024)
    if size_mb <= max_mb:
        return
    try:
        lines = log_path.read_text(errors="replace").splitlines()
        keep = lines[-3000:]
        log_path.write_text("\n".join(keep) + "\n")
        log.info(f"Trimmed {log_path.name} ({size_mb:.1f}MB → ~{log_path.stat().st_size / 1024:.0f}KB)")
    except Exception as exc:
        log.debug(f"Log trim skipped: {exc}")


def _prune_old_reports(days: int = 7) -> int:
    freed = 0
    cutoff = time.time() - days * 86400
    for sub in ("models/daily_reports", "backtest_results", "models/archive"):
        d = ROOT / sub
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    freed += f.stat().st_size
                    f.unlink()
                except Exception:
                    pass
    return freed


def cleanup_local_workspace(aggressive: bool = True) -> dict:
    """
    Clean local workspace after successful git push.
    Keeps models needed to restart; removes caches and stale artifacts.
    """
    stats = {"pycache_bytes": 0, "reports_bytes": 0, "runtime_bytes": 0}

    stats["pycache_bytes"] = _rm_glob(["**/__pycache__", "**/*.pyc"])
    stats["runtime_bytes"] = _rm_glob(["runtime/*.tmp", "runtime/*.log", "/tmp/mpl/*"])

    for log_name in ("logs/HANOON.log", "HANOON.log"):
        _trim_log(ROOT / log_name)

    if aggressive:
        stats["reports_bytes"] = _prune_old_reports(days=7)

    # Unload Ollama from RAM (disk models stay)
    try:
        import subprocess
        subprocess.run(["ollama", "stop", "qwen2.5:3b"], capture_output=True, timeout=10)
        subprocess.run(["ollama", "stop", "phi3:mini"], capture_output=True, timeout=10)
        subprocess.run(["ollama", "stop", "qwen2.5:0.5b"], capture_output=True, timeout=10)
    except Exception:
        pass

    total_mb = sum(stats.values()) / (1024 * 1024)
    log.info(f"🧹 Local cleanup done (~{total_mb:.1f}MB freed from caches/old reports)")
    return stats
