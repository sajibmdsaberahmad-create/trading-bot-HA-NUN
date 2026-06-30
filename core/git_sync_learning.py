#!/usr/bin/env python3
"""Learning persistence — extracted from git_sync."""

from __future__ import annotations

import glob as glob_mod
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Timer
from typing import Any, Dict, List, Optional, Set

from core.config import BotConfig
from core.notify import log
from core import git_sync_defer as _defer
from core import git_sync_state as S

REPO_DIR = S.REPO_DIR

LEARNING_ARTIFACTS: Dict[str, List[str]] = {
    out: List[str] = []
    for files in LEARNING_ARTIFACTS.values():
        out.extend(files)
    return list(dict.fromkeys(out))
def _force_learning_restore() -> bool:
    return os.getenv("LEARNING_FORCE_RESTORE", "").lower() in ("1", "true", "yes")
def _local_learning_file_ok(rel_path: str, min_bytes: int = 20) -> bool:
    local = os.path.join(REPO_DIR, rel_path)
    return os.path.exists(local) and os.path.getsize(local) >= min_bytes
def _hanoon_learning_needs_fetch() -> bool:
    if _force_learning_restore():
        return True
    for rel in LEARNING_REQUIRED_CODE:
        if not _local_learning_file_ok(rel):
            return True
    return False
def _repo_patterns_need_pull(repo_key: str) -> bool:
    if _force_learning_restore():
        return True
    patterns = LEARNING_ARTIFACTS.get(repo_key, [])
    if not patterns:
        return False
    if repo_key == "logs":
        # Logs are append-only — one local file means this device already synced
        return not any(_local_learning_file_ok(p) for p in patterns)
    if repo_key == "grandmaster":
        return not (
            _local_learning_file_ok("ppo_trader.zip", min_bytes=100_000)
            or _local_learning_file_ok("models/ppo_trader.zip", min_bytes=100_000)
        )
    return any(not _local_learning_file_ok(p) for p in patterns)
def _model_needs_release_download() -> bool:
    if _force_learning_restore():
        return True
    for rel in ("ppo_trader.zip", "models/ppo_trader.zip"):
        if _local_learning_file_ok(rel, min_bytes=100_000):
            return False
    return True
def is_learning_current() -> bool:
    """True when local artifacts are present — no remote fetch/clone needed."""
    if not S._enabled and not S._repo:
        return True
    return (
        not _hanoon_learning_needs_fetch()
        and not _repo_patterns_need_pull("logs")
        and not _repo_patterns_need_pull("grandmaster")
        and not _model_needs_release_download()
    )
def _should_restore_file(local_path: str, remote_path: str) -> bool:
    force = os.getenv("LEARNING_FORCE_RESTORE", "").lower() in ("1", "true", "yes")
    if force:
        return True
    if not os.path.exists(local_path) or os.path.getsize(local_path) < 20:
        return True
    if not os.path.exists(remote_path):
        return False
    local_sz = os.path.getsize(local_path)
    remote_sz = os.path.getsize(remote_path)
    return remote_sz > local_sz * 1.05
def pull_from_secondary_repo(repo_key: str, file_patterns: Optional[List[str]] = None) -> List[str]:
    """Clone secondary repo and restore learning files into the workspace."""
    repo_url = _get_repo_url(repo_key)
    if not repo_url:
        return []

    patterns = file_patterns or LEARNING_ARTIFACTS.get(repo_key, [])
    if not patterns:
        return []

    if not _repo_patterns_need_pull(repo_key):
        return []

    restored: List[str] = []
    try:
        import tempfile
        import glob as glob_mod

        tmpdir = tempfile.mkdtemp(prefix=f"{repo_key}_pull_")
        auth_url = repo_url
        if not auth_url or not _git_clone(auth_url, tmpdir, label=repo_key, timeout=90):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return []

        for pattern in patterns:
            hits = glob_mod.glob(os.path.join(tmpdir, pattern))
            if not hits and os.path.exists(os.path.join(tmpdir, pattern)):
                hits = [os.path.join(tmpdir, pattern)]
            for src in hits:
                rel = os.path.relpath(src, tmpdir)
                dst = os.path.join(REPO_DIR, rel)
                if not _should_restore_file(dst, src):
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                restored.append(rel)

        shutil.rmtree(tmpdir, ignore_errors=True)
        if restored:
            log.info(f"📥 Restored {len(restored)} file(s) from {repo_key} repo")
    except Exception as exc:
        log.debug(f"{repo_key} pull error: {exc}")
    return restored
def restore_hanoon_learning() -> List[str]:
    """Fetch tracked learning files from origin/main (missing locals only)."""
    if not S._enabled:
        return []
    if not _hanoon_learning_needs_fetch():
        return []
    restored: List[str] = []
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=90,
        )
        for rel in LEARNING_ARTIFACTS.get("code", []):
            local = os.path.join(REPO_DIR, rel)
            if os.path.exists(local) and os.path.getsize(local) >= 20:
                if not os.getenv("LEARNING_FORCE_RESTORE", "").lower() in ("1", "true", "yes"):
                    continue
            r = subprocess.run(
                ["git", "checkout", "origin/main", "--", rel],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and os.path.exists(local) and os.path.getsize(local) >= 20:
                restored.append(rel)
        if restored:
            log.info(f"📥 Restored {len(restored)} learning file(s) from HANOON repo")
    except Exception as exc:
        log.debug(f"HANOON learning restore: {exc}")
    return restored
def restore_model_from_release() -> bool:
    """Download ppo_trader.zip from latest GitHub release if missing locally."""
    if not _gh_cli_available() or not S._repo:
        return False
    if not _model_needs_release_download():
        return False
    target = os.path.join(REPO_DIR, "ppo_trader.zip")
    try:
        if _run_gh(
            ["release", "download", "--repo", S._repo, "latest", "--pattern", "ppo_trader.zip", "--dir", REPO_DIR],
            cwd=REPO_DIR, timeout=180,
        ):
            log.info("📥 Restored ppo_trader.zip from GitHub release")
            return True
    except Exception as exc:
        log.debug(f"Model release restore: {exc}")
    return False
def restore_all_learning(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """On startup / new device: pull earned experience from all GitHub repos."""
    if cfg and not getattr(cfg, "LEARNING_RESTORE_ON_STARTUP", True):
        return {"skipped": True}
    if not S._enabled:
        log.info("Learning restore skipped (GitHub token/repo not configured)")
        return {"skipped": True, "reason": "git_disabled"}

    if is_learning_current():
        log.info("✅ Learning restore — local experience already current")
        return {
            "hanoon": [], "logs": [], "grandmaster": [],
            "model_release": False, "total": 0, "skipped": True, "reason": "current",
        }

    log.info("📥 Restoring AI learning artifacts from GitHub...")
    hanoon = restore_hanoon_learning()
    logs = pull_from_secondary_repo("logs")
    gm = pull_from_secondary_repo("grandmaster")
    model_ok = restore_model_from_release()

    total = len(set(hanoon + logs + gm))
    if total or model_ok:
        log.info(f"✅ Learning restore — {total} artifact(s)" + (" + model" if model_ok else ""))
    else:
        log.info("✅ Learning restore — local experience already current")
    return {"hanoon": hanoon, "logs": logs, "grandmaster": gm, "model_release": model_ok, "total": total}
def push_learning_checkpoint(
    reason: str = "checkpoint",
    full_sync: bool = False,
    *,
    force: bool = False,
) -> bool:
    """Push learning artifacts to HANOON + Logs + Grandmaster (never blocks trading loop if called via async)."""
    if (
        force
        and not is_replay_live()
        and not _shutdown_git_reason(reason)
        and not _git_session_push_enabled()
    ):
        _queue_batched_checkpoint(reason)
        log.debug(f"Git checkpoint deferred (force blocked): {reason[:80]}")
        return True
    if not force and _should_defer_git_push("training"):
        _queue_batched_checkpoint(reason)
        _schedule_batched_checkpoint_flush()
        return True
    if not S._enabled:
        return False
    now = time.time()
    with _checkpoint_lock:
        if now - S._last_checkpoint_ts < S._CHECKPOINT_MIN_INTERVAL_SEC and not full_sync:
            log.debug(f"Learning checkpoint skipped (throttled): {reason}")
            return False
        S._last_checkpoint_ts = now
        S._last_push_ts = 0

        tag = f"learn_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        existing = [f for f in _learning_files_flat() if os.path.exists(os.path.join(REPO_DIR, f))]

        hanoon_files = [f for f in existing if f in LEARNING_ARTIFACTS.get("code", [])]
        logs_files = [f for f in existing if f in LEARNING_ARTIFACTS.get("logs", [])]
        gm_files = [f for f in existing if f in LEARNING_ARTIFACTS.get("grandmaster", [])]

        ok = False
        if hanoon_files:
            learn_title = f"learn: {reason} | {tag}"
            if brain := _brain_snapshot_line():
                learn_title += f" | {brain}"
            ok = push_change(learn_title, files=hanoon_files, category="training") or ok
        if logs_files and _get_repo_url("logs"):
            ok = push_to_secondary_repo("logs", logs_files, f"learn: {reason}", "training") or ok
        if gm_files and _get_repo_url("grandmaster"):
            ok = push_weights_to_repo(
                gm_files, repo_url=_get_repo_url("grandmaster"),
                message=f"learn: {reason} | {tag}",
            ) or ok

        if full_sync:
            try:
                sync_all_learning_artifacts(release_tag=tag)
            except Exception as exc:
                log.debug(f"Full learning sync: {exc}")

        if ok and cfg_bot is not None:
            try:
                from core.telegram_broadcast import notify_learning_checkpoint
                from core.git_sync import _git_notify_mode
                if _git_notify_mode(cfg_bot) not in ("off", "log"):
                    notify_learning_checkpoint(cfg_bot, f"{reason} | {tag}", ok=True)
            except Exception:
                pass

        return ok
def push_learning_checkpoint_async(reason: str = "checkpoint", full_sync: bool = False) -> None:
    """Non-blocking learning checkpoint — batched (one push, many reasons)."""
    if not S._enabled:
        return

    if _batch_checkpoints_enabled() or _should_defer_git_push("training"):
        _queue_batched_checkpoint(reason)
        if is_replay_live():
            log.debug(f"Git checkpoint queued for replay end: {reason}")
            return
        _schedule_batched_checkpoint_flush()
        return

    with _checkpoint_lock:
        if reason in _checkpoint_pending:
            return
        _checkpoint_pending.add(reason)

    def _run():
        try:
            push_learning_checkpoint(reason, full_sync=full_sync)
        except Exception as exc:
            log.debug(f"Background learning push ({reason}): {exc}")
        finally:
            with _checkpoint_lock:
                _checkpoint_pending.discard(reason)

    try:
        from core.async_utils import get_background_worker
        get_background_worker()._executor.submit(_run)
    except Exception:
        try:
            push_learning_checkpoint(reason, full_sync=full_sync)
        except Exception as exc:
            log.debug(f"Learning push fallback ({reason}): {exc}")
def verify_all_repos(cfg: Optional[BotConfig] = None) -> Dict[str, bool]:
    """Check that configured GitHub repos are reachable with the token."""
    token = _resolve_github_token(cfg)
    if not token:
        return {}
    results: Dict[str, bool] = {}
    for key, attr in (
        ("code", "GITHUB_HANOON_REPO"),
        ("grandmaster", "GITHUB_GRANDMASTER_REPO"),
        ("logs", "GITHUB_LOGS_REPO"),
    ):
        slug = (getattr(cfg, attr, "") if cfg else "") or os.getenv(attr, "")
        slug = _normalize_github_slug(slug.strip())
        url = _resolve_clone_url(slug) if slug else None
        if not url:
            results[key] = False
            continue
        try:
            r = subprocess.run(
                ["git", "ls-remote", url, "HEAD"],
                capture_output=True, text=True, timeout=25,
            )
            results[key] = r.returncode == 0
        except Exception:
            results[key] = False
    reachable = [k for k, v in results.items() if v]
    pending = [k for k, v in results.items() if not v]
    if reachable:
        log.info(f"GitHub repos OK: {', '.join(reachable)}")
    if pending:
        log.info(f"GitHub repos awaiting first push: {', '.join(pending)}")
    return results
def sync_all_repos(reason: str = "manual_sync") -> Dict[str, bool]:
    """Push code → HA-NUN, journals → Logs, weights → Grandmaster."""
    if not S._enabled:
        return {}
    out: Dict[str, bool] = {}
    out["learning"] = push_learning_checkpoint(reason, full_sync=True)
    return out
