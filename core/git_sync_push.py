#!/usr/bin/env python3
"""Push queue and executor — extracted from git_sync."""

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

_pending_lock = Lock()
def _is_pushable_path(path: str) -> bool:
    """Never auto-push plaintext secrets; encrypted vault is OK."""
    norm = path.replace("\\", "/").strip()
    base = os.path.basename(norm)
    if base == ".env" or base.startswith(".env."):
        return False
    if norm in ("secrets/hanoon.env.enc", "secrets/sync.key"):
        return True
    if base in _NEVER_PUSH_FILES:
        return False
    low = norm.lower()
    if any(x in low for x in ("secret", "credential", "private_key", ".pem")):
        return False
    if norm.startswith(".git/"):
        return False
    return True
def filter_git_addable(files: Optional[List[str]], repo_root: str) -> List[str]:
    """Drop missing paths, secrets, and .gitignore entries before staging."""
    if not files:
        return []
    out: List[str] = []
    for raw in files:
        norm = raw.replace("\\", "/").strip()
        if not norm or not _is_pushable_path(norm):
            continue
        if norm in _GITIGNORED_ARTIFACTS:
            continue
        full = os.path.join(repo_root, norm)
        if not os.path.exists(full):
            continue
        try:
            chk = subprocess.run(
                ["git", "check-ignore", "-q", "--", norm],
                cwd=repo_root,
                capture_output=True,
                timeout=10,
            )
            if chk.returncode == 0:
                continue
        except Exception:
            pass
        out.append(norm)
    return out
def _git_porcelain_files(repo_root: str) -> List[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return []
        files: List[str] = []
        for line in result.stdout.strip().splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1].strip()
            if path and _is_pushable_path(path):
                files.append(path)
        return files
    except Exception:
        return []
def _collect_dirty_files(repo_root: str) -> List[str]:
    """All pushable dirty files in the working tree."""
    cfg = cfg_bot
    push_all_changes = cfg is None or getattr(cfg, "GIT_PUSH_ALL_CHANGES", True)
    max_files = int(getattr(cfg, "GIT_AUTO_PUSH_MAX_FILES", 80)) if cfg else 80

    porcelain = _git_porcelain_files(repo_root)
    if not porcelain:
        return []

    if push_all_changes:
        return porcelain[:max_files]

    tracked: List[str] = []
    for f in porcelain:
        if f in TRACKED_FILES:
            tracked.append(f)
        elif f.startswith("core/") and f.endswith(".py"):
            tracked.append(f)
        elif f.startswith("scripts/") or f.startswith("models/"):
            tracked.append(f)
    return tracked[:max_files]
def _queue_push(message: str, files: Optional[List[str]], category: str) -> bool:
    """Queue a push for the next batch window (non-blocking)."""
    global _flush_timer
    with _pending_lock:
        _pending_pushes.append({
            "message": message,
            "files": files,
            "category": category,
            "queued_at": time.time(),
        })
        if len(_pending_pushes) == 1:
            if _flush_timer is not None:
                try:
                    _flush_timer.cancel()
                except Exception:
                    pass
            _flush_timer = Timer(BATCH_WINDOW_SEC, _flush_pending)
            _flush_timer.daemon = True
            _flush_timer.start()
    return True
def _flush_pending():
    """Flush all pending pushes in one commit."""
    with _pending_lock:
        if not _pending_pushes:
            return
        
        # Combine all messages
        messages = [p["message"] for p in _pending_pushes]
        categories = list(set(p["category"] for p in _pending_pushes))
        
        combined_msg = _build_combined_message(messages, categories)
        
        # Collect all unique files
        all_files: Set[str] = set()
        for p in _pending_pushes:
            if p["files"]:
                all_files.update(p["files"])
        
        _pending_pushes.clear()
    
    _do_push(combined_msg, list(all_files) if all_files else None, "batch")
def _build_combined_message(messages: List[str], categories: List[str]) -> str:
    """Build a combined commit message for batched pushes."""
    ts = datetime.utcnow().strftime("%H:%M:%S")
    cat_str = "/".join(categories[:3])  # Max 3 categories
    msg_preview = messages[0][:50]
    
    if len(messages) == 1:
        return f"{categories[0]}: {messages[0]}"
    
    return f"batch [{cat_str}] @ {ts} | {len(messages)} changes | first: {msg_preview}"
def _do_push(message: str, files: Optional[List[str]], category: str, repo_url: Optional[str] = None) -> bool:
    """Execute the actual git commit and push."""
    with _push_lock:
        , S._push_count, S._failed_pushes
        target_repo = repo_url or _remote_url()
        if not target_repo:
            log.warning("No target repo for push")
            return False
        
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        try:
            # Auto-detect changed files if not specified
            if files is None:
                files = _detect_changed_files(repo_root)
                # Filter raw data files to only recent ones
                files = _apply_bloat_guard(files, repo_root)
            
            if not files:
                S._last_push_ts = time.time()
                return True

            files = filter_git_addable(files, repo_root)
            if not files:
                S._last_push_ts = time.time()
                return True
            
            # Stage files
            stage_cmds = []
            for f in files:
                stage_cmds.append(["git", "add", f])
            
            if not stage_cmds:
                S._last_push_ts = time.time()
                return True
            
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            body = _enrich_commit_message(message, category, files)
            full_message = f"{body}\n\nTimestamp: {timestamp}\nAuto-pushed by git_sync.py"
            commit_env = {**os.environ, "GIT_SYNC_AUTO_COMMIT": "1"}
            commit_cmd = ["git", "commit", "-m", full_message, "--allow-empty"]
            push_cmd = ["git", "push", target_repo, "HEAD:main"]
            
            all_success = True
            for cmd in stage_cmds:
                result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    log.debug(f"Git stage failed: {' '.join(cmd)}: {result.stderr.strip()}")
                    all_success = False
            
            if all_success:
                result = subprocess.run(
                    commit_cmd, cwd=repo_root, capture_output=True, text=True,
                    timeout=30, env=commit_env,
                )
                if result.returncode != 0:
                    log.debug(f"Git commit failed: {result.stderr.strip()}")
                    all_success = False
                else:
                    log.debug(f"Git commit: {message[:60]}")
                    _record_auto_commit_in_brain_log(body, category)
            
            if all_success:
                result = subprocess.run(push_cmd, cwd=repo_root, capture_output=True, text=True, timeout=60)
                combined = (result.stderr or "") + (result.stdout or "")
                if result.returncode != 0 and "rejected" in combined.lower():
                    log.info("Primary repo push rejected — pull --rebase origin main then retry")
                    _git_pull_rebase_origin(repo_root, timeout=90)
                    result = subprocess.run(
                        ["git", "push", "origin", "HEAD:main"],
                        cwd=repo_root, capture_output=True, text=True, timeout=60,
                    )
                if result.returncode != 0:
                    log.debug(f"Git push failed: {result.stderr.strip()}")
                    all_success = False
                else:
                    S._push_count += 1
                    S._last_push_ts = time.time()
                    log.debug(f"GitHub: pushed #{S._push_count} to {repo_url or 'default'} — {category}: {message[:60]}")
                    _notify_git_push_result(
                        cfg_bot, message, category, ok=True, repo=repo_url or "code"
                    )
            else:
                S._failed_pushes += 1
                _notify_git_push_result(
                    cfg_bot, message, category, ok=False, repo=repo_url or "code"
                )
            
            return all_success
            
        except subprocess.TimeoutExpired:
            log.warning(f"Git push timed out [{category}]")
            S._failed_pushes += 1
            return False
        except Exception as exc:
            log.debug(f"Git push failed [{category}]: {exc}")
            S._failed_pushes += 1
            return False
def _apply_bloat_guard(files: List[str], repo_root: str) -> List[str]:
    """
    Prune raw data files to prevent .git bloat.
    
    Keeps only the most recent 30 days of raw CSV/JSONL data in commits.
    Model weights, config, and code are never pruned.
    """
    pruned = []
    for f in files:
        basename = os.path.basename(f)
        if basename in RAW_DATA_FILES or any(rf in f for rf in RAW_DATA_FILES):
            # Check file age
            full = os.path.join(repo_root, f)
            if os.path.exists(full):
                mtime = os.path.getmtime(full)
                age_days = (time.time() - mtime) / 86400
                if age_days > MAX_RAW_DATA_DAYS:
                    log.debug(f"Bloat guard: skipping old data file {f} ({age_days:.0f} days)")
                    continue
        pruned.append(f)
    return pruned
def _detect_changed_files(repo_root: str) -> List[str]:
    """Detect which files should be committed."""
    dirty = _collect_dirty_files(repo_root)
    if dirty:
        return dirty
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            files = [f for f in result.stdout.strip().splitlines() if _is_pushable_path(f)]
            if files:
                return files[:80]
    except Exception:
        pass
    return [f for f in TRACKED_FILES if os.path.exists(os.path.join(repo_root, f))][:40]
def _normalize_github_slug(repo: str) -> str:
    """
    Normalize any GitHub repo reference to 'owner/repo'.
    Handles owner/repo, full https URLs, git@github.com: URLs, and
    malformed URLs missing github.com (e.g. https://owner/repo.git).
    """
    if not repo:
        return ""
    s = repo.strip().rstrip("/")
    if "@" in s:
        s = s.split("@", 1)[-1]
    for prefix in (
        "https://", "http://", "git@github.com:", "ssh://git@github.com/",
    ):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    if s.lower().startswith("github.com/"):
        s = s[len("github.com/"):]
    elif s.lower().startswith("www.github.com/"):
        s = s[len("www.github.com/"):]
    if s.endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return s
def _github_clone_url(slug: str, token: Optional[str] = None) -> str:
    """Build a valid https://github.com/owner/repo.git clone URL."""
    slug = _normalize_github_slug(slug)
    if not slug or "/" not in slug:
        return ""
    host_path = f"github.com/{slug}.git"
    if token:
        return f"https://{token}@{host_path}"
    return f"https://{host_path}"
def _resolve_clone_url(raw: str) -> Optional[str]:
    """Normalize repo config and return an authenticated clone URL."""
    if not raw:
        return None
    slug = _normalize_github_slug(raw)
    if not slug or "/" not in slug:
        log.warning(f"Invalid GitHub repo slug (expected owner/repo): {raw!r}")
        return None
    if "github.com" not in raw.lower() and (raw.startswith("http") or raw != slug):
        log.info(f"GitHub repo URL auto-corrected: {raw!r} → github.com/{slug}")
    return _github_clone_url(slug, S._token)
def _git_clone(auth_url: str, dest: str, label: str = "repo", timeout: int = 90) -> bool:
    """Clone with automatic URL fixup when host is malformed."""
    if not auth_url:
        return False

    def _attempt(url: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "clone", "--depth", "1", url, dest],
            capture_output=True, text=True, timeout=timeout,
        )

    result = _attempt(auth_url)
    if result.returncode == 0:
        return True

    stderr = (result.stderr or "") + (result.stdout or "")
    slug = _normalize_github_slug(auth_url)
    fixed = _github_clone_url(slug, S._token) if slug else ""
    if fixed and fixed != auth_url and (
        "Could not resolve host" in stderr
        or "not found" in stderr.lower()
        or "github.com" not in auth_url
    ):
        log.warning(f"{label} clone failed — retrying with corrected github.com URL")
        retry = _attempt(fixed)
        if retry.returncode == 0:
            log.info(f"{label} clone succeeded after URL auto-fix")
            return True
        stderr = (retry.stderr or "") + (retry.stdout or "")

    log.warning(f"{label} clone failed: {stderr.strip()[:300]}")
    return False
def _git_pull_rebase_origin(cwd: str, timeout: int = 90) -> bool:
    """Rebase local commits onto remote main (clone temp dirs use origin)."""
    result = subprocess.run(
        ["git", "pull", "--rebase", "origin", "main"],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        log.debug(f"git pull --rebase origin main failed: {(result.stderr or result.stdout or '')[:200]}")
    return result.returncode == 0
def _git_push_origin_main(cwd: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )
def _git_push_with_rebase_retry(cwd: str, timeout: int = 90) -> subprocess.CompletedProcess:
    """Push to origin/main; on rejection pull --rebase then retry once."""
    result = _git_push_origin_main(cwd, timeout=timeout)
    combined = (result.stderr or "") + (result.stdout or "")
    if result.returncode != 0 and "rejected" in combined.lower():
        log.info("Push rejected — pulling --rebase origin main then retrying")
        _git_pull_rebase_origin(cwd, timeout=timeout)
        result = _git_push_origin_main(cwd, timeout=timeout)
    return result
def _sanitize_github_repos(cfg: BotConfig) -> None:
    """Auto-correct malformed GitHub repo slugs on startup."""
    for attr in ("GITHUB_REPO", "GITHUB_HANOON_REPO", "GITHUB_GRANDMASTER_REPO", "GITHUB_LOGS_REPO"):
        raw = (getattr(cfg, attr, None) or os.getenv(attr, "") or "").strip()
        if not raw:
            continue
        fixed = _normalize_github_slug(raw)
        if not fixed:
            continue
        if fixed != raw:
            log.info(f"Auto-fixed {attr}: {raw!r} → {fixed!r}")
        setattr(cfg, attr, fixed)
def _remote_url() -> Optional[str]:
    """Build authenticated remote URL from global state."""
    , S._token
    if not S._repo:
        return None
    return _github_clone_url(_normalize_github_slug(S._repo), S._token) or None
def _verify_repo() -> bool:
    """Verify the GitHub repo is reachable."""
    if not S._repo:
        return False
    
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    try:
        # Check if it's a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            log.warning("Not a git repository")
            return False
        
        # Check remote
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            log.warning("No 'origin' remote configured")
            return False
        
        return True
    except Exception:
        return False
