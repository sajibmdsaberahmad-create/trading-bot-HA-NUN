from core import git_sync_defer as _defer
from core import git_sync_state as S
from core.notify import log
from core.config import BotConfig
from threading import Lock, Timer
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Dict, Any
import threading
import shutil
import json
import hashlib
import time
import sys
import subprocess
import os
def _resolve_github_token(cfg: Optional[BotConfig] = None) -> str:
    if cfg is not None:
        t = (getattr(cfg, "GITHUB_TOKEN", "") or getattr(cfg, "GITHUB_PAT", "") or "").strip()
        if t:
            return t
    return (os.getenv("GITHUB_TOKEN", "") or os.getenv("GITHUB_PAT", "") or _token or "").strip()
def ensure_github_cli(cfg: Optional[BotConfig] = None, force_auth: bool = True) -> bool:
    """
    Install gh if missing (Homebrew) and authenticate with GITHUB_TOKEN.
    Called at startup and before release uploads so artifacts stay synced.
    """
    global _gh_cli_cached, _gh_missing_logged, _gh_auth_verified, _token

    token = _resolve_github_token(cfg)
    if token:
        _token = token

    force_install = True
    if cfg is not None:
        force_install = bool(getattr(cfg, "GITHUB_FORCE_CLI", True))
    else:
        force_install = os.getenv("GITHUB_FORCE_CLI", "true").lower() not in ("0", "false", "no")

    if not force_install and shutil.which("gh"):
        _gh_cli_cached = True
        return True

    if not shutil.which("gh"):
        brew = shutil.which("brew")
        if brew:
            log.info("📦 Installing GitHub CLI (gh) via Homebrew...")
            try:
                subprocess.run([brew, "install", "gh"], capture_output=True, timeout=600, check=False)
            except Exception as exc:
                log.warning(f"gh install failed: {exc}")
        _gh_cli_cached = None

    if not shutil.which("gh"):
        if not _gh_missing_logged:
            _gh_missing_logged = True
            log.warning("GitHub CLI (gh) not found — install: brew install gh")
        _gh_cli_cached = False
        return False

    _gh_cli_cached = True

    if not token:
        log.debug("GITHUB_TOKEN not set — gh installed; set token in .env for releases")
        return True

    env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}

    try:
        st = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, env=env, timeout=15,
        )
        if st.returncode == 0:
            _gh_auth_verified = True
            subprocess.run(["gh", "auth", "setup-git"], capture_output=True, timeout=30, env=env)
            return True
    except Exception:
        pass

    log.info("🔐 Authenticating GitHub CLI (gh)...")
    try:
        # Login without GH_TOKEN in env so gh can persist credentials to keyring
        login_env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
        proc = subprocess.run(
            ["gh", "auth", "login", "--with-token"],
            input=token + "\n", text=True, capture_output=True, timeout=30, env=login_env,
        )
        if proc.returncode != 0:
            # Token via env still works for API — treat as OK if gh responds
            st2 = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, env=env, timeout=15,
            )
            if st2.returncode == 0:
                _gh_auth_verified = True
                return True
            log.warning(f"gh auth failed: {(proc.stderr or proc.stdout or '')[:200]}")
            return False
        subprocess.run(["gh", "auth", "setup-git"], capture_output=True, timeout=30, env=env)
        _gh_auth_verified = True
        log.info("✅ GitHub CLI authenticated")
    except Exception as exc:
        log.warning(f"gh auth error: {exc}")
        return False

    return True
def _gh_cli_available() -> bool:
    """True if GitHub CLI is installed (required for release asset uploads)."""
    global _gh_cli_cached
    if _gh_cli_cached is None:
        _gh_cli_cached = shutil.which("gh") is not None
    return _gh_cli_cached
def _run_gh(args: List[str], cwd: str = REPO_DIR, timeout: int = 60) -> bool:
    """Run gh subprocess; auto-install/auth if configured."""
    if not _gh_cli_available():
        ensure_github_cli(force_auth=True)
    if not _gh_cli_available():
        return False
    token = _resolve_github_token()
    env = {**os.environ}
    if token:
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
    try:
        result = subprocess.run(
            ["gh", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        if result.returncode != 0:
            log.debug(f"gh {' '.join(args[:3])}… failed: {(result.stderr or result.stdout or '')[:200]}")
            return False
        return True
    except Exception as exc:
        log.debug(f"gh command failed: {exc}")
        return False
def init(cfg: BotConfig, ollama_brain: Optional[Any] = None):
    """
    Initialize from BotConfig env vars.
    
    Sets up HANOON repo (primary), Grandmaster (models), and Logs repos.
    Idempotent: restore and repo verification run at most once per process.

    Args:
        cfg: Bot configuration.
        ollama_brain: Optional LLM brain used to generate AI commit messages.
    """
    global S._repo, _token, _enabled, _ollama_brain, _git_init_done, _learning_restore_done
    if ollama_brain is not None:
        _ollama_brain = ollama_brain
    if _git_init_done:
        return
    _repo = (
        getattr(cfg, "GITHUB_HANOON_REPO", None) or os.getenv("GITHUB_HANOON_REPO", "")
        or os.getenv("GITHUB_HA_NUN_REPO", "")
        or getattr(cfg, "GITHUB_REPO", None) or os.getenv("GITHUB_REPO", "")
    )
    _token = (getattr(cfg, "GITHUB_TOKEN", None) or os.getenv("GITHUB_TOKEN", "") or
              getattr(cfg, "GITHUB_PAT", None) or os.getenv("GITHUB_PAT", ""))
    _enabled = bool(_repo and _token)
    
    _sanitize_github_repos(cfg)
    _repo = _normalize_github_slug(_repo) or _repo
    
    if _enabled:
        ensure_github_cli(cfg)
        _gm = getattr(cfg, "GITHUB_GRANDMASTER_REPO", "") or "disabled"
        _logs = getattr(cfg, "GITHUB_LOGS_REPO", "") or "disabled"
        log.info(f"GitHub sync initialized — HANOON={_repo} | Grandmaster={_gm} | Logs={_logs}")
        if not _verify_repo():
            log.warning("HANOON repo verification failed — sync disabled")
            _enabled = False
        set_global_config(cfg)
        verify_all_repos(cfg)
        if getattr(cfg, "LEARNING_RESTORE_ON_STARTUP", True) and not _learning_restore_done:
            try:
                restore_all_learning(cfg)
            except Exception as exc:
                log.debug(f"Learning restore at init: {exc}")
            finally:
                _learning_restore_done = True
    else:
        log.info("GitHub sync disabled (no token/repo configured)")
    _git_init_done = True
    if _enabled and is_replay_live():
        log.info(
            "📤 Git sync: REPLAY — all pushes deferred until session end "
            "(1 consolidated sync after evolution)"
        )
    elif _enabled and _batch_checkpoints_enabled():
        deb = float(os.getenv("GIT_CHECKPOINT_DEBOUNCE_SEC", "180"))
        log.info(
            f"📤 Git sync: batched checkpoints — one push every ~{deb:.0f}s max "
            "(no per-trade triple-repo spam)"
        )
    elif _enabled and not _git_session_push_enabled():
        log.info(
            "📤 Git sync: session pushes OFF — learning queued until stop_hanoon "
            "(set GIT_PUSH_DURING_SESSION=true to push while trading)"
        )
        with _checkpoint_lock:
            global _checkpoint_flush_timer
            if _checkpoint_flush_timer is not None:
                _checkpoint_flush_timer.cancel()
                _checkpoint_flush_timer = None
def push_change(message: str, files: Optional[List[str]] = None, 
                category: str = "general") -> bool:
    """
    Push a change to GitHub. This is the main entry point.
    
    Auto-called by other modules (trader, agent, risk, etc.)
    
    Args:
        message: Commit message describing the change
        files: Specific files to stage (None = auto-detect changed tracked files)
        category: Category for grouping (model, trade, config, guardrail, etc.)
        
    Returns:
        True if push succeeded, False otherwise
    """
    if _should_defer_git_push(category):
        _queue_batched_checkpoint(message[:80] if category == "training" else f"{category}:{message[:40]}")
        log.debug(f"Git push deferred (batched): {message[:70]}")
        return True
    if not _enabled:
        return False
    
    now = time.time()
    global S._last_push_ts
    if now - _last_push_ts < MIN_PUSH_INTERVAL_SEC:
        return _queue_push(message, files, category)
    
    # Determine which repos need updating based on category and files
    target_repos = _resolve_target_repos(files, category)
    
    all_success = True
    for repo_key, repo_files in target_repos.items():
        repo_url = _get_repo_url(repo_key)
        if not repo_url:
            continue
        # For logs/grandmaster repos, clone-push if not same as main
        if repo_key == "code":
            success = _do_push(message, repo_files, category, repo_url)
        else:
            success = push_to_secondary_repo(repo_key, repo_files, message, category)
        if not success:
            all_success = False
    
    return all_success
def push_all(message: str = "checkpoint: all current state") -> bool:
    """Force push everything (full state backup)."""
    return push_change(message, files=None, category="checkpoint")
def push_change_async(
    message: str,
    files: Optional[List[str]] = None,
    category: str = "general",
) -> None:
    """Non-blocking push — safe from trading loop and file watcher."""
    if _should_defer_git_push(category):
        _queue_batched_checkpoint(f"{category}:{message[:60]}")
        log.debug(f"Git push deferred (batched): {message[:70]}")
        return
    if not _enabled:
        return

    def _run():
        try:
            push_change(message, files, category)
        except Exception as exc:
            log.debug(f"Background git push ({category}): {exc}")

    try:
        from core.async_utils import get_background_worker
        get_background_worker()._executor.submit(_run)
    except Exception:
        threading.Thread(target=_run, name=f"git-push-{category}", daemon=True).start()
def set_standalone_mode(enabled: bool = True) -> None:
    """Mark this process as the standalone git-sync daemon (not HANOON)."""
    global _standalone_mode
    _standalone_mode = enabled
def is_standalone_mode() -> bool:
    return _standalone_mode or os.getenv("GIT_SYNC_STANDALONE", "").lower() in (
        "1", "true", "yes",
    )
def preflight_check(cfg: Optional[BotConfig] = None) -> tuple:
    """
    Startup checklist for standalone git-sync daemon.
    Returns (all_ok, lines).
    """
    c = cfg or cfg_bot
    lines: List[str] = []
    ok = True

    def mark(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        if not passed:
            ok = False
        suffix = f" — {detail}" if detail else ""
        lines.append(f"{'✓' if passed else '✗'} {name}{suffix}")

    token = ""
    repo = ""
    if c is not None:
        token = (
            getattr(c, "GITHUB_TOKEN", "")
            or getattr(c, "GITHUB_PAT", "")
            or os.getenv("GITHUB_TOKEN", "")
            or os.getenv("GITHUB_PAT", "")
        ).strip()
        repo = (
            getattr(c, "GITHUB_HANOON_REPO", "")
            or getattr(c, "GITHUB_REPO", "")
            or os.getenv("GITHUB_HANOON_REPO", "")
            or os.getenv("GITHUB_REPO", "")
            or os.getenv("GITHUB_HA_NUN_REPO", "")
        ).strip()
    else:
        token = (os.getenv("GITHUB_TOKEN", "") or os.getenv("GITHUB_PAT", "")).strip()
        repo = (
            os.getenv("GITHUB_HANOON_REPO", "")
            or os.getenv("GITHUB_REPO", "")
            or os.getenv("GITHUB_HA_NUN_REPO", "")
        ).strip()

    mark("GITHUB_TOKEN in .env", bool(token), "required for push")
    mark("GITHUB_HANOON_REPO (or GITHUB_REPO)", bool(repo), "owner/repo slug")
    mark("git binary", bool(shutil.which("git")))
    mark("repo root", os.path.isdir(REPO_DIR), REPO_DIR)
    mark("git_sync enabled", _enabled, "token + repo verified at init")

    if c is not None:
        interval = getattr(c, "GIT_AUTO_PUSH_INTERVAL_SEC", 12)
        push_all = getattr(c, "GIT_PUSH_ALL_CHANGES", True)
        lines.append(f"  interval: {interval}s | push_all_changes: {push_all}")
        lines.append("  mode: standalone (HANOON not required)")
        enc = os.path.join(REPO_DIR, "secrets", "hanoon.env.enc")
        lines.append(
            f"  env vault: {'present' if os.path.exists(enc) else 'missing'} "
            "(encrypted .env for other devices)"
        )

    return ok, lines
def run_standalone_daemon(cfg: Optional[BotConfig] = None) -> None:
    """
    Blocking auto-push loop for the standalone git-sync process.
    Safe to run 24/7 alongside or without HANOON.
    """
    import signal

    c = cfg or cfg_bot
    if not _enabled:
        log.error("Git sync daemon: disabled — fix preflight checklist and restart")
        return

    set_standalone_mode(True)
    _watcher_stop.clear()

    def _stop(*_args):
        _watcher_stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    interval = float(getattr(c, "GIT_AUTO_PUSH_INTERVAL_SEC", 12)) if c else 12.0
    log.info(f"Git sync daemon active — polling every {interval:.0f}s (independent of HANOON)")

    global _last_dirty_fingerprint
    while not _watcher_stop.is_set():
        try:
            try:
                from core.env_secrets import encrypt_env_to_vault, vault_paths_for_git
                encrypt_env_to_vault()
            except Exception as exc:
                log.debug(f"Env vault sync: {exc}")
            dirty = _collect_dirty_files(REPO_DIR)
            for vp in vault_paths_for_git():
                if vp not in dirty and os.path.exists(os.path.join(REPO_DIR, vp)):
                    dirty.append(vp)
            if dirty:
                fp = hashlib.sha256("|".join(sorted(dirty)).encode()).hexdigest()[:20]
                if fp != _last_dirty_fingerprint:
                    _last_dirty_fingerprint = fp
                    preview = ", ".join(os.path.basename(f) for f in dirty[:5])
                    push_change(
                        _build_auto_commit_message(dirty),
                        files=dirty,
                        category="auto",
                    )
        except Exception as exc:
            log.debug(f"Git sync daemon cycle: {exc}")
        _watcher_stop.wait(interval)

    try:
        dirty = _collect_dirty_files(REPO_DIR)
        if dirty:
            push_change("daemon shutdown: final flush", files=dirty, category="shutdown")
    except Exception:
        pass
    try:
        flush_git_telegram_summary(c)
    except Exception:
        pass
    log.info("Git sync daemon stopped")
def start_auto_push_watcher(cfg: Optional[BotConfig] = None) -> None:
    """Deprecated in HANOON — use scripts/start_git_sync.sh instead."""
    if is_standalone_mode():
        run_standalone_daemon(cfg)
        return
    log.debug("Git auto-watch skipped inside HANOON — run ./scripts/start_git_sync.sh")
def stop_auto_push_watcher() -> None:
    _watcher_stop.set()

from core import git_sync_state as S
from core import git_sync_commit as _gcommit
from core import git_sync_push as _gpush
from core import git_sync_routing as _groute
from core import git_sync_learning as _glearn

# Re-exports for backward compatibility
_brain_snapshot_line = _gcommit._brain_snapshot_line
_summarize_changed_files = _gcommit._summarize_changed_files
_enrich_commit_message = _gcommit._enrich_commit_message
_build_auto_commit_message = _gcommit._build_auto_commit_message
_record_auto_commit_in_brain_log = _gcommit._record_auto_commit_in_brain_log
_git_notify_mode = _gcommit._git_notify_mode
record_git_push_event = _gcommit.record_git_push_event
write_git_session_summary = _gcommit.write_git_session_summary
flush_git_telegram_summary = _gcommit.flush_git_telegram_summary
_notify_git_push_result = _gcommit._notify_git_push_result

_pending_pushes = _gpush._pending_pushes
_pending_lock = _gpush._pending_lock
_is_pushable_path = _gpush._is_pushable_path
filter_git_addable = _gpush.filter_git_addable
_git_porcelain_files = _gpush._git_porcelain_files
_collect_dirty_files = _gpush._collect_dirty_files
_queue_push = _gpush._queue_push
_flush_pending = _gpush._flush_pending
_build_combined_message = _gpush._build_combined_message
_do_push = _gpush._do_push
_apply_bloat_guard = _gpush._apply_bloat_guard
_detect_changed_files = _gpush._detect_changed_files
_normalize_github_slug = _gpush._normalize_github_slug
_github_clone_url = _gpush._github_clone_url
_resolve_clone_url = _gpush._resolve_clone_url
_git_clone = _gpush._git_clone
_git_pull_rebase_origin = _gpush._git_pull_rebase_origin
_git_push_origin_main = _gpush._git_push_origin_main
_git_push_with_rebase_retry = _gpush._git_push_with_rebase_retry
_sanitize_github_repos = _gpush._sanitize_github_repos
_remote_url = _gpush._remote_url
_verify_repo = _gpush._verify_repo

push_weights_to_repo = _groute.push_weights_to_repo
_get_repo_url = _groute._get_repo_url
_resolve_target_repos = _groute._resolve_target_repos
_bootstrap_empty_repo = _groute._bootstrap_empty_repo
push_to_secondary_repo = _groute.push_to_secondary_repo
set_global_config = _groute.set_global_config
flush_batched_git_sync = _groute.flush_batched_git_sync
flush_replay_session_git_sync = _groute.flush_replay_session_git_sync
push_trade = _groute.push_trade
push_training = _groute.push_training
push_daily_summary = _groute.push_daily_summary
push_model_update = _groute.push_model_update
push_guardrail_event = _groute.push_guardrail_event
push_config_change = _groute.push_config_change
push_feature_update = _groute.push_feature_update
push_error = _groute.push_error
push_startup = _groute.push_startup
push_shutdown = _groute.push_shutdown
push_full_shutdown_sync = _groute.push_full_shutdown_sync
get_stats = _groute.get_stats
push_model_release = _groute.push_model_release
push_large_file_to_release = _groute.push_large_file_to_release
sync_all_learning_artifacts = _groute.sync_all_learning_artifacts

LEARNING_ARTIFACTS = _glearn.LEARNING_ARTIFACTS
LEARNING_REQUIRED_CODE = _glearn.LEARNING_REQUIRED_CODE
_learning_files_flat = _glearn._learning_files_flat
_force_learning_restore = _glearn._force_learning_restore
_local_learning_file_ok = _glearn._local_learning_file_ok
_hanoon_learning_needs_fetch = _glearn._hanoon_learning_needs_fetch
_repo_patterns_need_pull = _glearn._repo_patterns_need_pull
_model_needs_release_download = _glearn._model_needs_release_download
is_learning_current = _glearn.is_learning_current
_should_restore_file = _glearn._should_restore_file
pull_from_secondary_repo = _glearn.pull_from_secondary_repo
restore_hanoon_learning = _glearn.restore_hanoon_learning
restore_model_from_release = _glearn.restore_model_from_release
restore_all_learning = _glearn.restore_all_learning
push_learning_checkpoint = _glearn.push_learning_checkpoint
push_learning_checkpoint_async = _glearn.push_learning_checkpoint_async
verify_all_repos = _glearn.verify_all_repos
sync_all_repos = _glearn.sync_all_repos

# Module-level aliases for legacy direct access
_enabled = S._enabled
_repo = S._repo
_token = S._token
