#!/usr/bin/env python3
"""
core/local_cleanup.py — Free disk/RAM: caches, stale logs, duplicate model zips.
"""

from __future__ import annotations

import glob
import os
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

from core.notify import log

ROOT = Path(__file__).resolve().parent.parent

_JSONL_TRIM: Dict[str, int] = {
    "audit_trail.jsonl": 5000,
    "models/thought_journal.jsonl": 5000,
    "models/ai_decision_log.jsonl": 4000,
    "models/experience_buffer.jsonl": 5000,
    "models/flight_log.jsonl": 3000,
    "models/account_snapshots.jsonl": 1500,
    "models/account_evaluation_log.jsonl": 1500,
    "models/ppo_entry_ledger.jsonl": 3000,
    "models/trained_record_hashes.jsonl": 3000,
    "models/post_mortem_audit.jsonl": 2000,
    "models/regime_atr_efficiency.jsonl": 2000,
    "models/smart_stack_verdicts.jsonl": 20000,
    "halim/data/trading/experience_buffer.jsonl": 5000,
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
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=keep_lines)
        if len(tail) < keep_lines and before <= max_mb * 1024 * 1024:
            return 0
        log_path.write_text("".join(tail))
        freed = before - log_path.stat().st_size
        log.info(f"Trimmed {log_path.name} ({size_mb:.1f}MB → {log_path.stat().st_size / 1024:.0f}KB)")
        return max(0, freed)
    except Exception as exc:
        log.debug(f"Log trim skipped ({log_path.name}): {exc}")
        return 0


def _trim_jsonl(rel_path: str, max_lines: int) -> int:
    """Tail-only trim — O(file) read but O(max_lines) RAM, never load whole file."""
    path = ROOT / rel_path
    if not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=max_lines + 1)
        if len(tail) <= max_lines:
            return 0
        before = path.stat().st_size
        keep = list(tail)[-max_lines:]
        path.write_text("".join(keep))
        return max(0, before - path.stat().st_size)
    except Exception:
        return 0


def _prune_learn_cache(max_files: int = 400) -> int:
    """Cap halim learn_cache JSON count — oldest removed first."""
    cache = ROOT / "halim" / "data" / "learn_cache"
    if not cache.is_dir():
        return 0
    try:
        files = sorted(cache.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return 0
    if len(files) <= max_files:
        return 0
    freed = 0
    for old in files[: len(files) - max_files]:
        try:
            freed += old.stat().st_size
            old.unlink(missing_ok=True)
        except OSError:
            pass
    if freed:
        log.info(f"Pruned learn_cache → kept {max_files} newest files")
    return freed


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


def _purge_replay_csv_farm() -> int:
    try:
        from core.replay_data_housekeeping import purge_replay_farm
        result = purge_replay_farm(verbose=False)
        return int(result.get("bytes_freed", 0) or 0)
    except Exception as exc:
        log.debug(f"Replay purge skipped: {exc}")
        return 0


def _remove_ide_worktrees() -> int:
    """Drop stale Kilo/Cursor worktree copies (duplicate models/zips)."""
    freed = 0
    worktrees = ROOT / ".kilo" / "worktrees"
    if not worktrees.is_dir():
        return 0
    for child in worktrees.iterdir():
        if not child.is_dir():
            continue
        try:
            freed += sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            shutil.rmtree(child, ignore_errors=True)
            log.info(f"Removed IDE worktree {child.name}")
        except Exception:
            pass
    return freed


def _remove_root_artifacts() -> int:
    freed = 0
    patterns = [
        "backtest_journal_*.jsonl",
        "backtest_results_*.json",
        "runtime/*.tmp",
        "runtime/*.log",
    ]
    for pat in patterns:
        for p in glob.glob(str(ROOT / pat)):
            try:
                path = Path(p)
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink(missing_ok=True)
            except Exception:
                pass
    root_zip = ROOT / "ppo_trader.zip"
    models_zip = ROOT / "models" / "ppo_trader_replay.zip"
    if root_zip.is_file() and models_zip.is_file():
        try:
            freed += root_zip.stat().st_size
            root_zip.unlink(missing_ok=True)
            log.info("Removed duplicate root ppo_trader.zip (models/ copy kept)")
        except Exception:
            pass
    return freed


def _trim_all_logs(max_mb: float = 2.0, keep_lines: int = 2500) -> int:
    freed = 0
    for rel in ("HANOON.log", "logs/HANOON.log", "logs/ollama.log", "logs/REPLAY_SCALPER.log"):
        freed += _trim_log(ROOT / rel, max_mb=max_mb, keep_lines=keep_lines)
    logs_dir = ROOT / "logs"
    if logs_dir.is_dir():
        for log_path in logs_dir.glob("*.log"):
            freed += _trim_log(log_path, max_mb=max_mb, keep_lines=keep_lines)
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


def _prune_cursor_logs(days: int = 7) -> int:
    """Trim old Cursor IDE log files (safe — not settings or extensions)."""
    freed = 0
    cursor_logs = Path.home() / "Library" / "Application Support" / "Cursor" / "logs"
    if not cursor_logs.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    try:
        for f in cursor_logs.rglob("*"):
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime >= cutoff:
                    continue
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass
    return freed


def _remove_download_installers() -> int:
    """Delete .dmg installers in ~/Downloads (re-downloadable)."""
    freed = 0
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return 0
    for dmg in downloads.glob("*.dmg"):
        try:
            freed += dmg.stat().st_size
            dmg.unlink(missing_ok=True)
        except OSError:
            pass
    return freed


def scan_downloads_clones() -> List[Dict[str, object]]:
    """Large non-tradingbot folders in ~/Downloads that look like old project copies."""
    downloads = Path.home() / "Downloads"
    keep = {ROOT.resolve()}
    rows: List[Dict[str, object]] = []
    hints = (
        "trading", "trade", "trader", "ibkr", "pivot", "pivoit", "hanoon",
        "untitled folder", "restart", "new ", "nee", "trd", "pid",
    )
    if not downloads.is_dir():
        return rows
    for child in downloads.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.resolve() in keep:
                continue
        except OSError:
            continue
        name_l = child.name.lower()
        if not any(h in name_l for h in hints):
            continue
        try:
            size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        except OSError:
            size = 0
        if size < 50 * 1024 * 1024:
            continue
        rows.append({"path": str(child), "name": child.name, "bytes": size})
    rows.sort(key=lambda r: int(r["bytes"]), reverse=True)
    return rows


def _prune_downloads_halim_extras() -> int:
    """Remove duplicate Halim checkpoint copies and failed Chrome zip downloads."""
    freed = 0
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return 0
    names = (
        "toddler_v1",
        "toddler_v1-2",
        "halim_toddler_v1.zip",
        "halim_toddler_v2.zip",
        "halim_toddler_v3.zip",
        "halim_toddler_v4.zip",
        "halim_toddler_v5.zip",
    )
    for name in names:
        p = downloads / name
        if p.exists():
            try:
                if p.is_dir():
                    freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    freed += p.stat().st_size
                    p.unlink(missing_ok=True)
                log.info(f"Removed Downloads duplicate {name}")
            except OSError:
                pass
    for d in downloads.glob("halim_toddler_v*.zip.download"):
        if d.is_dir():
            try:
                freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
                log.info(f"Removed incomplete download {d.name}")
            except OSError:
                pass
    for d in downloads.glob("*.download"):
        if not d.is_dir():
            continue
        try:
            total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            if total < 1024 * 1024:
                freed += total
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
    return freed


def prune_halim_colab_artifacts(ckpt: Optional[Path] = None) -> int:
    """Drop Colab training intermediates — keep merged/ + top-level lora adapter for MLX."""
    freed = 0
    root = ckpt or (ROOT / "halim/data/checkpoints/toddler_v1")
    la = root / "lora_adapter"
    if not la.is_dir():
        return 0
    for name in ("training_args.bin",):
        p = la / name
        if p.is_file():
            try:
                freed += p.stat().st_size
                p.unlink(missing_ok=True)
            except OSError:
                pass
    adapter_top = la / "adapter_model.safetensors"
    merged = root / "merged" / "model.safetensors"
    for sub in list(la.glob("checkpoint-*")):
        if not sub.is_dir():
            continue
        for junk in ("optimizer.pt", "scheduler.pt", "rng_state.pth"):
            p = sub / junk
            if p.is_file():
                try:
                    freed += p.stat().st_size
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        if adapter_top.is_file() or merged.is_file():
            try:
                freed += sum(f.stat().st_size for f in sub.rglob("*") if f.is_file())
                shutil.rmtree(sub, ignore_errors=True)
                log.info(f"Pruned Colab checkpoint dir {sub.name}")
            except OSError:
                pass
    if freed:
        log.info(f"Pruned Halim Colab artifacts (~{freed / (1024 * 1024):.1f}MB)")
    return freed


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def prune_git_lfs_halim_blobs() -> int:
    """Untrack Halim weight blobs from git/LFS index — keeps files on disk for MLX."""
    import subprocess

    freed = 0
    git_dir = ROOT / ".git"
    if not git_dir.is_dir():
        return 0
    before = _path_size(git_dir / "lfs") if (git_dir / "lfs").is_dir() else 0

    paths = [
        "halim/data/checkpoints/toddler_v1/merged",
        "halim/data/checkpoints/toddler_v1_mlx",
    ]
    for pat in (
        "halim/data/checkpoints/toddler_v1/lora_adapter/checkpoint-*",
        "halim/data/checkpoints/**/optimizer.pt",
        "halim/data/checkpoints/**/scheduler.pt",
        "halim/data/checkpoints/**/rng_state.pth",
        "halim/data/checkpoints/**/training_args.bin",
        "halim/data/checkpoints/**/*.safetensors",
        "halim/data/checkpoints/**/tokenizer.json",
    ):
        for p in glob.glob(str(ROOT / pat), recursive=True):
            rel = str(Path(p).relative_to(ROOT))
            try:
                subprocess.run(
                    ["git", "-C", str(ROOT), "rm", "--cached", "-q", "-r", rel],
                    capture_output=True, timeout=30, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass
    for rel in paths:
        try:
            subprocess.run(
                ["git", "-C", str(ROOT), "rm", "--cached", "-q", "-r", rel],
                capture_output=True, timeout=30, check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
    try:
        subprocess.run(
            ["git", "-C", str(ROOT), "lfs", "prune", "--force"],
            capture_output=True, timeout=180, check=False,
        )
        subprocess.run(
            ["git", "-C", str(ROOT), "gc", "--prune=now"],
            capture_output=True, timeout=120, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass
    after = _path_size(git_dir / "lfs") if (git_dir / "lfs").is_dir() else 0
    freed = max(0, before - after)
    if freed:
        log.info(f"Git LFS prune freed ~{freed / (1024 * 1024):.1f}MB")
    return freed


def cleanup_device_extras(*, remove_download_dmgs: bool = True) -> Dict[str, int]:
    """System-level safe cleanup outside the repo (caches, installers, IDE logs).

    Never runs mac-cleaner ``all`` (homebrew prune / full user caches) — that is
    opt-in via ``DEEP_SWEEP_AGGRESSIVE=true ./scripts/deep_sweep.sh`` only.
    """
    stats: Dict[str, int] = {
        "cursor_log_bytes": 0,
        "dmg_bytes": 0,
        "mac_cleaner_bytes": 0,
        "git_lfs_bytes": 0,
        "downloads_halim_bytes": 0,
    }
    if os.getenv("HALIM_GIT_LFS_PRUNE", "false").lower() in ("1", "true", "yes"):
        stats["git_lfs_bytes"] = prune_git_lfs_halim_blobs()
    if os.getenv("DEEP_SWEEP_PRUNE_DOWNLOADS", "false").lower() in ("1", "true", "yes"):
        stats["downloads_halim_bytes"] = _prune_downloads_halim_extras()
    stats["cursor_log_bytes"] = _prune_cursor_logs(days=7)
    if remove_download_dmgs and os.getenv("CLEANUP_DOWNLOAD_DMGS", "false").lower() in ("1", "true", "yes"):
        stats["dmg_bytes"] = _remove_download_installers()
    try:
        import subprocess
        # Safe subsets only — not ``hanoon`` (includes Downloads zip deletion + git gc)
        proc = subprocess.run(
            [
                "python3",
                str(ROOT / "mac-cleaner" / "clean.py"),
                "--clean", "--yes", "hanoon_cruft", "pip", "cursor_shipit",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            cwd=str(ROOT),
        )
        if proc.stdout:
            for line in proc.stdout.splitlines():
                if "freed ~" in line.lower() and line.strip().startswith("✓"):
                    log.info(line.strip())
        if proc.returncode == 0 and "Done —" in (proc.stdout or ""):
            for line in (proc.stdout or "").splitlines():
                if line.strip().startswith("Done —"):
                    log.info(line.strip())
    except Exception as exc:
        log.debug(f"mac-cleaner skipped: {exc}")
    return stats


def cleanup_local_workspace(aggressive: bool = True, *, skip_jsonl_trim: bool = False) -> dict:
    """Clean local workspace — safe while bot is running."""
    stats: Dict[str, int] = {
        "pycache_bytes": 0,
        "reports_bytes": 0,
        "jsonl_bytes": 0,
        "logs_bytes": 0,
        "warmup_zip_bytes": 0,
        "runtime_bytes": 0,
        "replay_bytes": 0,
        "worktree_bytes": 0,
        "artifacts_bytes": 0,
    }

    stats["pycache_bytes"] = _rm_glob(["**/__pycache__", "**/*.pyc"])
    stats["runtime_bytes"] += _rm_glob(["runtime/*.tmp", "runtime/*.log"])
    mpl_cache = Path.home() / ".matplotlib"
    if mpl_cache.exists() and aggressive:
        try:
            for f in mpl_cache.glob("*.cache"):
                stats["runtime_bytes"] += f.stat().st_size
                f.unlink(missing_ok=True)
        except Exception:
            pass

    stats["logs_bytes"] = _trim_all_logs(
        max_mb=1.5 if aggressive else 3.0,
        keep_lines=2500 if aggressive else 4000,
    )

    if aggressive:
        # Replay CSV purge only during replay sessions — not on live stop (preserves pre-downloaded farm)
        if os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes"):
            stats["replay_bytes"] = _purge_replay_csv_farm()
        stats["worktree_bytes"] = _remove_ide_worktrees()
        stats["artifacts_bytes"] = _remove_root_artifacts()
        stats["reports_bytes"] = _prune_old_reports(days=7)
        stats["warmup_zip_bytes"] = _prune_duplicate_ppo_warmups(keep=1)
        stats["runtime_bytes"] += _prune_learn_cache(
            max_files=int(os.getenv("HALIM_LEARN_CACHE_MAX_FILES", "400")),
        )
        stats["runtime_bytes"] += prune_halim_colab_artifacts()
        if not skip_jsonl_trim:
            protect_buffer = os.getenv("LEARNING_PROTECT_BUFFER", "true").lower() in (
                "1", "true", "yes",
            )
            for rel, max_lines in _JSONL_TRIM.items():
                if protect_buffer and "experience_buffer.jsonl" in rel:
                    continue
                stats["jsonl_bytes"] += _trim_jsonl(rel, max_lines)

    _unload_ollama_ram()

    total_mb = sum(stats.values()) / (1024 * 1024)
    log.info(f"🧹 Local cleanup done (~{total_mb:.1f}MB freed)")
    return stats


def run_periodic_cleanup(cfg=None, *, force: bool = False) -> dict:
    """Called from main loop when market closed or RAM is tight."""
    from core.memory_guard import is_memory_pressured, available_ram_mb

    iv = float(os.getenv("PERIODIC_CLEANUP_SEC", getattr(cfg, "PERIODIC_CLEANUP_SEC", 0) if cfg else 0) or 0)
    if iv <= 0 and not force:
        return {"skipped": True, "reason": "PERIODIC_CLEANUP_SEC=0", "available_mb": available_ram_mb()}
    if os.getenv("AUTO_DISK_CLEANUP", "false").lower() not in ("1", "true", "yes") and not force:
        return {"skipped": True, "reason": "AUTO_DISK_CLEANUP=false", "available_mb": available_ram_mb()}

    ram_tight = is_memory_pressured(
        int(getattr(cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024)) if cfg else 1024
    )
    aggressive = force or ram_tight
    if not aggressive and not force:
        return {"skipped": True, "available_mb": available_ram_mb()}
    # Under RAM pressure skip jsonl sweeps — tail-trim still reads whole files and spikes RAM
    return cleanup_local_workspace(aggressive=aggressive, skip_jsonl_trim=ram_tight)
