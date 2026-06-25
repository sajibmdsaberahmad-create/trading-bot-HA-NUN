#!/usr/bin/env python3
"""
core/local_cleanup.py — Free disk/RAM: caches, stale logs, duplicate model zips.
"""

from __future__ import annotations

import glob
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List

from core.notify import log

ROOT = Path(__file__).resolve().parent.parent

_JSONL_TRIM: Dict[str, int] = {
    "models/thought_journal.jsonl": 5000,
    "models/ai_decision_log.jsonl": 4000,
    "models/experience_buffer.jsonl": 8000,
    "models/flight_log.jsonl": 3000,
    "models/account_snapshots.jsonl": 2000,
    "models/account_evaluation_log.jsonl": 2000,
    "models/trained_record_hashes.jsonl": 3000,
    "models/post_mortem_audit.jsonl": 2000,
    "models/regime_atr_efficiency.jsonl": 2000,
}


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


def _trim_log(log_path: Path, max_mb: float = 2.0, keep_lines: int = 3000) -> int:
    if not log_path.exists():
        return 0
    size_mb = log_path.stat().st_size / (1024 * 1024)
    if size_mb <= max_mb:
        return 0
    before = log_path.stat().st_size
    try:
        lines = log_path.read_text(errors="replace").splitlines()
        log_path.write_text("\n".join(lines[-keep_lines:]) + "\n")
        freed = before - log_path.stat().st_size
        log.info(f"Trimmed {log_path.name} ({size_mb:.1f}MB → {log_path.stat().st_size / 1024:.0f}KB)")
        return max(0, freed)
    except Exception as exc:
        log.debug(f"Log trim skipped ({log_path.name}): {exc}")
        return 0


def _trim_jsonl(rel_path: str, max_lines: int) -> int:
    path = ROOT / rel_path
    if not path.exists():
        return 0
    try:
        lines = path.read_text(errors="replace").splitlines()
        if len(lines) <= max_lines:
            return 0
        before = path.stat().st_size
        path.write_text("\n".join(lines[-max_lines:]) + "\n")
        return max(0, before - path.stat().st_size)
    except Exception:
        return 0


def _prune_old_reports(days: int = 7) -> int:
    freed = 0
    cutoff = time.time() - days * 86400
    for sub in ("models/daily_reports", "backtest_results", "models/archive", "logs/archive"):
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


def _prune_duplicate_ppo_warmups(keep: int = 1) -> int:
    """Remove old ppo_trader_warmup_*.zip — keep newest only."""
    freed = 0
    models = ROOT / "models"
    zips = sorted(
        models.glob("ppo_trader_warmup_*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in zips[keep:]:
        try:
            freed += old.stat().st_size
            old.unlink()
            log.info(f"Removed old warmup zip {old.name}")
        except Exception:
            pass
    return freed


def _unload_ollama_ram() -> None:
    try:
        from core.memory_guard import unload_heavy_ollama_models
        unload_heavy_ollama_models()
    except Exception:
        pass


def cleanup_local_workspace(aggressive: bool = True) -> dict:
    """Clean local workspace — safe while bot is running."""
    stats: Dict[str, int] = {
        "pycache_bytes": 0,
        "reports_bytes": 0,
        "jsonl_bytes": 0,
        "logs_bytes": 0,
        "warmup_zip_bytes": 0,
        "runtime_bytes": 0,
    }

    stats["pycache_bytes"] = _rm_glob(["**/__pycache__", "**/*.pyc"])
    stats["runtime_bytes"] = _rm_glob(["runtime/*.tmp", "runtime/*.log"])
    mpl_cache = Path.home() / ".matplotlib"
    if mpl_cache.exists() and aggressive:
        try:
            for f in mpl_cache.glob("*.cache"):
                stats["runtime_bytes"] += f.stat().st_size
                f.unlink(missing_ok=True)
        except Exception:
            pass

    for log_name in ("logs/HANOON.log", "logs/ollama.log", "HANOON.log"):
        stats["logs_bytes"] += _trim_log(ROOT / log_name, max_mb=1.5 if aggressive else 3.0)

    if aggressive:
        stats["reports_bytes"] = _prune_old_reports(days=7)
        stats["warmup_zip_bytes"] = _prune_duplicate_ppo_warmups(keep=1)
        for rel, max_lines in _JSONL_TRIM.items():
            stats["jsonl_bytes"] += _trim_jsonl(rel, max_lines)

    _unload_ollama_ram()

    total_mb = sum(stats.values()) / (1024 * 1024)
    log.info(f"🧹 Local cleanup done (~{total_mb:.1f}MB freed)")
    return stats


def run_periodic_cleanup(cfg=None, *, force: bool = False) -> dict:
    """Called from main loop when market closed or RAM is tight."""
    from core.memory_guard import is_memory_pressured, available_ram_mb

    aggressive = force or is_memory_pressured(
        int(getattr(cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024)) if cfg else 1024
    )
    if not aggressive and not force:
        return {"skipped": True, "available_mb": available_ram_mb()}
    return cleanup_local_workspace(aggressive=aggressive)
