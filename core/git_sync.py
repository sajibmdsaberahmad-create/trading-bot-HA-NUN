#!/usr/bin/env python3
"""
core/git_sync.py — Automatic GitHub push for EVERY change.

Facade over git_sync_commit, git_sync_push, git_sync_routing, git_sync_learning.
"""


from core import git_sync_state as S
from core import git_sync_commit as _gcommit
from core import git_sync_push as _gpush
from core import git_sync_routing as _groute
from core import git_sync_learning as _glearn
from core import git_sync_defer as _defer

REPO_DIR = S.REPO_DIR


_is_replay_live = _defer.is_replay_live
_git_session_push_enabled = _defer.git_session_push_enabled
_batch_checkpoints_enabled = _defer.batch_checkpoints_enabled
_queue_batched_checkpoint = _defer.queue_batched_checkpoint
_schedule_batched_checkpoint_flush = _defer.schedule_batched_checkpoint_flush
_should_defer_git_push = _defer.should_defer_git_push
_shutdown_git_reason = _defer.shutdown_git_reason
_checkpoint_lock = _defer.checkpoint_lock
cfg_bot = _defer.cfg_bot

REPO_DIR = S.REPO_DIR
from core import git_sync_defer as _defer
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
"""
core/git_sync.py — Automatic GitHub push for EVERY change.

This module auto-commits and pushes to GitHub whenever any tracked
file changes. It uses file watchers and hooks to ensure nothing
is lost between pushes.

TRIGGER EVENTS (every one of these auto-pushes):
1. Model saved/updated (ppo_trader.zip, backups)
2. Trade recorded (performance.csv)
3. Guardrail audit entry created (audit_trail.jsonl)
4. Configuration change
5. Feature/model file change
6. Daily summary generated
7. Bot startup/shutdown
8. Manual push request from any module
9. Error/exception event (for debugging)
10. Any file in tracked_files list modified

Setup:
   1. Add GITHUB_TOKEN and GITHUB_REPO to .env
   2. Call init(cfg) at startup
   3. Call push_change(message, files) from anywhere
"""
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S._repo: Optional[str] = None
S._token: Optional[str] = None
S._enabled: bool = False
S._last_push_ts: float = 0.0
_push_count: int = 0
_failed_pushes: int = 0
S._ollama_brain: Optional[Any] = None  # Optional LLM for AI-generated commit messages
S._gh_cli_cached: Optional[bool] = None
S._gh_missing_logged: bool = False
S._gh_auth_verified: bool = False
S._git_init_done: bool = False
S._learning_restore_done: bool = False
_checkpoint_pending: Set[str] = set()  # legacy — use _checkpoint_batched_reasons
_last_checkpoint_ts: float = 0.0
_CHECKPOINT_MIN_INTERVAL_SEC: float = 45.0
S._standalone_mode: bool = False
_watcher_thread: Optional[threading.Thread] = None
S._last_dirty_fingerprint: str = ""
_flush_timer: Optional[Timer] = None
_git_session_stats: Dict[str, Any] = {
    "ok": 0,
    "fail": 0,
    "by_category": {},
    "last_ok_at": "",
    "last_message": "",
}
def _resolve_github_token(cfg: Optional[BotConfig] = None) -> str:
    if cfg is not None:
        t = (getattr(cfg, "GITHUB_TOKEN", "") or getattr(cfg, "GITHUB_PAT", "") or "").strip()
        if t:
            return t
    return (os.getenv("GITHUB_TOKEN", "") or os.getenv("GITHUB_PAT", "") or S._token or "").strip()
def ensure_github_cli(cfg: Optional[BotConfig] = None, force_auth: bool = True) -> bool:
    """
    Install gh if missing (Homebrew) and authenticate with GITHUB_TOKEN.
    Called at startup and before release uploads so artifacts stay synced.
    """
    , S._gh_missing_logged, S._gh_auth_verified, S._token

    token = _resolve_github_token(cfg)
    if token:
        S._token = token

    force_install = True
    if cfg is not None:
        force_install = bool(getattr(cfg, "GITHUB_FORCE_CLI", True))
    else:
        force_install = os.getenv("GITHUB_FORCE_CLI", "true").lower() not in ("0", "false", "no")

    if not force_install and shutil.which("gh"):
        S._gh_cli_cached = True
        return True

    if not shutil.which("gh"):
        brew = shutil.which("brew")
        if brew:
            log.info("📦 Installing GitHub CLI (gh) via Homebrew...")
            try:
                subprocess.run([brew, "install", "gh"], capture_output=True, timeout=600, check=False)
            except Exception as exc:
                log.warning(f"gh install failed: {exc}")
        S._gh_cli_cached = None

    if not shutil.which("gh"):
        if not S._gh_missing_logged:
            S._gh_missing_logged = True
            log.warning("GitHub CLI (gh) not found — install: brew install gh")
        S._gh_cli_cached = False
        return False

    S._gh_cli_cached = True

    if not token:
        log.debug("GITHUB_TOKEN not set — gh installed; set token in .env for releases")
        return True

    env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}

    try:
        st = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, env=env, timeout=15,
        )
        if st.returncode == 0:
            S._gh_auth_verified = True
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
                S._gh_auth_verified = True
                return True
            log.warning(f"gh auth failed: {(proc.stderr or proc.stdout or '')[:200]}")
            return False
        subprocess.run(["gh", "auth", "setup-git"], capture_output=True, timeout=30, env=env)
        S._gh_auth_verified = True
        log.info("✅ GitHub CLI authenticated")
    except Exception as exc:
        log.warning(f"gh auth error: {exc}")
        return False

    return True
def _gh_cli_available() -> bool:
    """True if GitHub CLI is installed (required for release asset uploads)."""
    
    if S._gh_cli_cached is None:
        S._gh_cli_cached = shutil.which("gh") is not None
    return S._gh_cli_cached
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
CODE_TRACKED: Set[str] = {
    "main.py",
    "requirements.txt",
    "start.sh",
    "START.command",
    "core/config.py",
    "core/agent.py",
    "core/agent_enhanced.py",
    "core/ai_commander.py",
    "core/ai_notifier.py",
    "core/ai_guardrails.py",
    "core/async_utils.py",
    "core/broker.py",
    "core/scalper_runner.py",
    "core/scanner.py",
    "core/git_sync.py",
    "core/council_budget.py",
    "core/council_client.py",
    "core/council_brain.py",
    "core/ollama_brain.py",
    "core/live_ai_pipeline.py",
    "core/hybrid_distiller.py",
    "core/cognitive_core.py",
    "core/cognitive_autopilot.py",
    "core/consciousness.py",
    "core/pilot_mode.py",
    "core/pilot_experience.py",
    "core/memory_guard.py",
    "core/market_hours.py",
    "core/account_evaluator.py",
    "core/risk.py",
    "core/telegram_auth.py",
    "core/telegram_listener.py",
    "core/telegram_broadcast.py",
    "core/ai_telegram.py",
    "core/position_intel.py",
    "core/system_status.py",
    "core/commander_learning.py",
    "core/daily_activity_report.py",
    "core/generative_mood.py",
    "core/ollama_vision.py",
    "core/param_bounds.py",
    "core/paper_mode.py",
    "core/hmrs.py",
    "core/features_enhanced.py",
    "core/transformer_model.py",
    "core/multi_model_fusion.py",
    "models/scalper_weights.json",
    "models/pilot_experience.json",
    "models/consciousness.json",
    "models/cognitive_state.json",
    "models/pattern_memory_bank.json",
    "models/improvement_history.json",
    "models/daily_guidelines.txt",
    "models/training_history.json",
    "models/feature_manifest.json",
    "models/model_manifest.json",
    "scripts/start_hanoon.sh",
    "scripts/start_git_sync.sh",
    "scripts/git_auto_push.py",
    "secrets/hanoon.env.enc",
    "secrets/sync.key",
    "scripts/stop_hanoon.sh",
}
LOGS_TRACKED: Set[str] = {
    "performance.csv",
    "live_metrics.json",
    "audit_trail.jsonl",
    "bot_state.json",
    "training_journal.json",
    "models/thought_journal.jsonl",
    "models/trade_journal.json",
    "models/experience_buffer.jsonl",
    "models/ai_decision_log.jsonl",
    "models/flight_log.jsonl",
    "models/account_snapshots.jsonl",
    "models/account_evaluation_log.jsonl",
    "models/trained_record_hashes.jsonl",
}
GM_TRACKED: Set[str] = {
    "ppo_trader.zip",
    "models/ppo_trader.zip",
    "models/fusion_state.json",
    "models/model_accuracy.json",
    "models/teacher_proxy.joblib",
    "models/hybrid_distill_state.json",
}
TRACKED_FILES: Set[str] = CODE_TRACKED | LOGS_TRACKED | GM_TRACKED
REPO_ROUTES: Dict[str, Set[str]] = {
    "code": set(CODE_TRACKED),
    "logs": set(LOGS_TRACKED) | {
        "HANOON.log",
        "trading_bot.log",
        "models/pattern_snapshots.jsonl",
    },
    "grandmaster": set(GM_TRACKED) | {
        "models/transformer_model.pth",
        "models/lstm_model.h5",
        "models/checkpoints/",
        "backtest_results/",
        "training_history_*.json",
    },
}
CATEGORY_TO_REPO: Dict[str, str] = {
    "model": "grandmaster",
    "training": "grandmaster",
    "trade": "logs",
    "daily": "logs",
    "guardrail": "logs",
    "config": "code",
    "features": "code",
    "error": "logs",
    "startup": "code",
    "shutdown": "logs",
    "checkpoint": "code",
    "general": "code",
}
RAW_DATA_FILES: Set[str] = {
    "data/live_market_features.csv",
    "backtest_results/results_latest.csv",
    "models/experience_buffer.jsonl",
}
MAX_RAW_DATA_DAYS: int = 30
MIN_PUSH_INTERVAL_SEC: float = 5.0
_GIT_JOURNAL_PATH = os.path.join(REPO_DIR, "logs", "git_sync_journal.jsonl")
_GIT_SESSION_SUMMARY_PATH = os.path.join(REPO_DIR, "logs", "git_session_summary.txt")
_AUTO_COMMIT_LOG_CATEGORIES = frozenset({
    "shutdown", "training", "replay_end", "auto", "checkpoint", "daily",
})
BATCH_WINDOW_SEC: float = 10.0
def init(cfg: BotConfig, ollama_brain: Optional[Any] = None):
    """
    Initialize from BotConfig env vars.
    
    Sets up HANOON repo (primary), Grandmaster (models), and Logs repos.
    Idempotent: restore and repo verification run at most once per process.

    Args:
        cfg: Bot configuration.
        ollama_brain: Optional LLM brain used to generate AI commit messages.
    """
    , S._token, S._enabled, S._ollama_brain, S._git_init_done, S._learning_restore_done
    if ollama_brain is not None:
        S._ollama_brain = ollama_brain
    if S._git_init_done:
        return
    S._repo = (
        getattr(cfg, "GITHUB_HANOON_REPO", None) or os.getenv("GITHUB_HANOON_REPO", "")
        or os.getenv("GITHUB_HA_NUN_REPO", "")
        or getattr(cfg, "GITHUB_REPO", None) or os.getenv("GITHUB_REPO", "")
    )
    S._token = (getattr(cfg, "GITHUB_TOKEN", None) or os.getenv("GITHUB_TOKEN", "") or
              getattr(cfg, "GITHUB_PAT", None) or os.getenv("GITHUB_PAT", ""))
    S._enabled = bool(S._repo and S._token)
    
    _sanitize_github_repos(cfg)
    S._repo = _normalize_github_slug(S._repo) or S._repo
    
    if S._enabled:
        ensure_github_cli(cfg)
        _gm = getattr(cfg, "GITHUB_GRANDMASTER_REPO", "") or "disabled"
        _logs = getattr(cfg, "GITHUB_LOGS_REPO", "") or "disabled"
        log.info(f"GitHub sync initialized — HANOON={S._repo} | Grandmaster={_gm} | Logs={_logs}")
        if not _verify_repo():
            log.warning("HANOON repo verification failed — sync disabled")
            S._enabled = False
        set_global_config(cfg)
        verify_all_repos(cfg)
        if getattr(cfg, "LEARNING_RESTORE_ON_STARTUP", True) and not S._learning_restore_done:
            try:
                restore_all_learning(cfg)
            except Exception as exc:
                log.debug(f"Learning restore at init: {exc}")
            finally:
                S._learning_restore_done = True
    else:
        log.info("GitHub sync disabled (no token/repo configured)")
    S._git_init_done = True
    if S._enabled and is_replay_live():
        log.info(
            "📤 Git sync: REPLAY — all pushes deferred until session end "
            "(1 consolidated sync after evolution)"
        )
    elif S._enabled and _batch_checkpoints_enabled():
        deb = float(os.getenv("GIT_CHECKPOINT_DEBOUNCE_SEC", "180"))
        log.info(
            f"📤 Git sync: batched checkpoints — one push every ~{deb:.0f}s max "
            "(no per-trade triple-repo spam)"
        )
    elif S._enabled and not _git_session_push_enabled():
        log.info(
            "📤 Git sync: session pushes OFF — learning queued until stop_hanoon "
            "(set GIT_PUSH_DURING_SESSION=true to push while trading)"
        )
        with _defer.checkpoint_lock:
            
            if _defer.checkpoint_flush_timer is not None:
                _defer.checkpoint_flush_timer.cancel()
                _defer.checkpoint_flush_timer = None
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
    if not S._enabled:
        return False
    
    now = time.time()
    
    if now - S._last_push_ts < MIN_PUSH_INTERVAL_SEC:
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
    if not S._enabled:
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
    
    S._standalone_mode = enabled
def is_standalone_mode() -> bool:
    return S._standalone_mode or os.getenv("GIT_SYNC_STANDALONE", "").lower() in (
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
    mark("git_sync enabled", S._enabled, "token + repo verified at init")

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
    if not S._enabled:
        log.error("Git sync daemon: disabled — fix preflight checklist and restart")
        return

    set_standalone_mode(True)
    S._watcher_stop.clear()

    def _stop(*_args):
        S._watcher_stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    interval = float(getattr(c, "GIT_AUTO_PUSH_INTERVAL_SEC", 12)) if c else 12.0
    log.info(f"Git sync daemon active — polling every {interval:.0f}s (independent of HANOON)")

    
    while not S._watcher_stop.is_set():
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
                if fp != S._last_dirty_fingerprint:
                    S._last_dirty_fingerprint = fp
                    preview = ", ".join(os.path.basename(f) for f in dirty[:5])
                    push_change(
                        _build_auto_commit_message(dirty),
                        files=dirty,
                        category="auto",
                    )
        except Exception as exc:
            log.debug(f"Git sync daemon cycle: {exc}")
        S._watcher_stop.wait(interval)

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
    S._watcher_stop.set()
_pending_pushes: List[dict] = []
_GITIGNORED_ARTIFACTS: Set[str] = {
    "models/experience_buffer.jsonl",
    "models/trained_record_hashes.jsonl",
    "models/thought_journal.jsonl",
    "models/ai_decision_log.jsonl",
    "models/flight_log.jsonl",
    "models/account_snapshots.jsonl",
    "models/account_evaluation_log.jsonl",
    "audit_trail.jsonl",
    "live_metrics.json",
    "bot_state.json",
}
cfg_bot: Any = None
is_replay_live = _defer.is_replay_live
_git_session_push_enabled = _defer.git_session_push_enabled
_batch_checkpoints_enabled = _defer.batch_checkpoints_enabled
_should_defer_git_push = _defer.should_defer_git_push
_queue_batched_checkpoint = _defer.queue_batched_checkpoint
_schedule_batched_checkpoint_flush = _defer.schedule_batched_checkpoint_flush
_shutdown_git_reason = _defer.shutdown_git_reason
batched_git_stats = _defer.batched_git_stats
LEARNING_ARTIFACTS: Dict[str, List[str]] = {
    "code": [
        "models/consciousness.json",
        "models/pilot_experience.json",
        "models/flight_log.jsonl",
        "models/pattern_memory_bank.json",
        "models/pattern_snapshots.jsonl",
        "models/scalper_weights.json",
        "models/improvement_history.json",
        "models/owned_brain_state.json",
        "models/owned_brain_manifest.json",
        "models/device_profile.json",
        "models/copilot_state.json",
        "models/council_training_dataset.jsonl",
        "models/owned_brain_journal.jsonl",
        "models/halim_identity.json",
        "models/halim_manifest.json",
        "models/halim_developer.jsonl",
        "models/halim_constitution.json",
        "models/halim_guardrail_state.json",
        "models/halim_kill_switch.json",
        "models/halim_guardrail_audit.jsonl",
        "models/halim_google_search.jsonl",
        "models/halim_web_learn.jsonl",
        "models/halim_web_monitor.jsonl",
        "models/halim_frontier_policy.json",
        "models/halim_frontier_audit.jsonl",
        "models/halim_runtime.jsonl",
        "models/halim_runtime_state.json",
        "halim/data/actions/action_log.jsonl",
        "halim/data/training/action_gold.jsonl",
        "halim/data/registry.jsonl",
        "halim/data/coevolution/correction_log.jsonl",
        "halim/data/coevolution/dialogue.jsonl",
        "halim/data/training/coevolution_gold.jsonl",
        "halim/data/training/dialogue_gold.jsonl",
        "models/halim_companion_state.json",
        "halim/data/companion/conversation_gold.jsonl",
        "models/halim_ppo_coevolution_state.json",
        "models/halim_shutdown.jsonl",
        "docs/OWNED_BRAIN.md",
        "docs/HALIM.md",
        "docs/HALIM_GUARDRAILS.md",
        "docs/BRAIN_DEVELOPMENT_LOG.md",
        "docs/ENGINEERING_FIX_LOG.md",
        "models/profit_hunt_ledger.jsonl",
        "models/market_data_denylist.json",
        "models/market_data_failures.jsonl",
        "models/trained_record_hashes.jsonl",
        "models/cognitive_state.json",
        "models/daily_guidelines.txt",
        "models/training_history.json",
        "models/pattern_snapshots.jsonl",
    ],
    "logs": [
        "models/thought_journal.jsonl",
        "models/trade_journal.json",
        "models/experience_buffer.jsonl",
        "models/profit_hunt_ledger.jsonl",
        "models/market_data_denylist.json",
        "models/market_data_failures.jsonl",
        "models/ai_decision_log.jsonl",
        "models/copilot_journal.jsonl",
        "models/ppo_teacher_sessions.jsonl",
        "models/owned_brain_journal.jsonl",
        "models/flight_log.jsonl",
        "models/account_snapshots.jsonl",
        "models/account_evaluation_log.jsonl",
        "models/trained_record_hashes.jsonl",
        "performance.csv",
        "live_metrics.json",
        "audit_trail.jsonl",
    ],
    "grandmaster": [
        "ppo_trader.zip",
        "models/ppo_trader.zip",
        "models/fusion_state.json",
        "models/model_manifest.json",
        "models/teacher_proxy.joblib",
        "models/hybrid_distill_state.json",
        "models/ppo_trader_replay.zip",
        "models/council_training_dataset.jsonl",
    ],
}
LEARNING_REQUIRED_CODE: List[str] = [
    "models/consciousness.json",
    "models/pilot_experience.json",
    "models/scalper_weights.json",
]
_defer.register_session_flush_hook(
    lambda: flush_batched_git_sync("session_batch", full_sync=False, force=True),
)
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
