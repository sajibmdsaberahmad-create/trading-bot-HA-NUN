#!/usr/bin/env python3
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

import os
import subprocess
import sys
import time
import hashlib
import json
import shutil
import threading
from typing import List, Optional, Set, Dict, Any
from pathlib import Path
from datetime import datetime
from threading import Lock, Timer

from core.config import BotConfig
from core.notify import log

# Repository directory (project root)
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═════════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═════════════════════════════════════════════════════════════════════════════

_repo: Optional[str] = None
_token: Optional[str] = None
_enabled: bool = False
_push_lock = Lock()
_last_push_ts: float = 0.0
_push_count: int = 0
_failed_pushes: int = 0
_ollama_brain: Optional[Any] = None  # Optional LLM for AI-generated commit messages
_gh_cli_cached: Optional[bool] = None
_gh_missing_logged: bool = False
_gh_auth_verified: bool = False
_git_init_done: bool = False
_learning_restore_done: bool = False
_checkpoint_lock = Lock()
_checkpoint_pending: Set[str] = set()
_last_checkpoint_ts: float = 0.0
_CHECKPOINT_MIN_INTERVAL_SEC: float = 45.0

# Auto-push watcher (standalone daemon only)
_standalone_mode: bool = False
_watcher_stop = threading.Event()
_watcher_thread: Optional[threading.Thread] = None
_last_dirty_fingerprint: str = ""
_flush_timer: Optional[Timer] = None
_git_journal_lock = Lock()
_git_session_stats: Dict[str, Any] = {
    "ok": 0,
    "fail": 0,
    "by_category": {},
    "last_ok_at": "",
    "last_message": "",
}

_NEVER_PUSH_FILES: Set[str] = {
    ".env", ".env.local", ".env.production", ".env.backup",
    "credentials.json", "secrets.json",
}


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

# Files the CODE repo (HA-NUN) may auto-push — lean: source + light AI state only
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

# Union used for change detection allowlist
TRACKED_FILES: Set[str] = CODE_TRACKED | LOGS_TRACKED | GM_TRACKED

# Multi-repo routing: which files go to which repo
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

# Category → repo mapping
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

# Raw data files get pruned to prevent bloat
RAW_DATA_FILES: Set[str] = {
    "data/live_market_features.csv",
    "backtest_results/results_latest.csv",
    "models/experience_buffer.jsonl",
}
# Max days of raw data to keep in git history
MAX_RAW_DATA_DAYS: int = 30

# Debounce: minimum seconds between pushes
MIN_PUSH_INTERVAL_SEC: float = 5.0
_GIT_JOURNAL_PATH = os.path.join(REPO_DIR, "logs", "git_sync_journal.jsonl")
_GIT_SESSION_SUMMARY_PATH = os.path.join(REPO_DIR, "logs", "git_session_summary.txt")


def _git_notify_mode(cfg: Optional[BotConfig] = None) -> str:
    """log=journal only | session=journal + one Telegram at shutdown | failures | all | off"""
    c = cfg or cfg_bot
    if c is not None and getattr(c, "TELEGRAM_BROADCAST_GIT", False):
        return "all"
    if c is not None:
        mode = (getattr(c, "GIT_NOTIFY_MODE", "") or os.getenv("GIT_NOTIFY_MODE", "log")).strip().lower()
    else:
        mode = os.getenv("GIT_NOTIFY_MODE", "log").strip().lower()
    if mode in ("log", "session", "failures", "all", "off"):
        return mode
    return "log"


def record_git_push_event(
    message: str,
    category: str,
    *,
    ok: bool,
    repo: str = "code",
) -> None:
    """Append every push to logs/git_sync_journal.jsonl (no Telegram spam)."""
    global _git_session_stats
    os.makedirs(os.path.dirname(_GIT_JOURNAL_PATH), exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = {
        "timestamp": ts,
        "ok": ok,
        "category": category,
        "repo": repo,
        "message": (message or "")[:500],
    }
    with _git_journal_lock:
        if ok:
            _git_session_stats["ok"] = int(_git_session_stats.get("ok", 0)) + 1
            _git_session_stats["last_ok_at"] = ts
            _git_session_stats["last_message"] = entry["message"]
        else:
            _git_session_stats["fail"] = int(_git_session_stats.get("fail", 0)) + 1
        by_cat = _git_session_stats.setdefault("by_category", {})
        by_cat[category] = int(by_cat.get(category, 0)) + 1
        try:
            with open(_GIT_JOURNAL_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.debug(f"Git journal write failed: {exc}")


def write_git_session_summary() -> str:
    """Write end-of-session summary file from journal stats."""
    os.makedirs(os.path.dirname(_GIT_SESSION_SUMMARY_PATH), exist_ok=True)
    with _git_journal_lock:
        stats = dict(_git_session_stats)
    lines = [
        f"Git sync session summary — {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Pushes OK: {stats.get('ok', 0)} | Failed: {stats.get('fail', 0)}",
        f"Last OK: {stats.get('last_ok_at', '—')}",
        f"Last message: {stats.get('last_message', '—')}",
        "By category:",
    ]
    for cat, n in sorted((stats.get("by_category") or {}).items()):
        lines.append(f"  {cat}: {n}")
    lines.append(f"Full journal: logs/git_sync_journal.jsonl")
    text = "\n".join(lines)
    try:
        with open(_GIT_SESSION_SUMMARY_PATH, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception as exc:
        log.debug(f"Git session summary write failed: {exc}")
    return text


def flush_git_telegram_summary(cfg: Optional[BotConfig] = None) -> None:
    """One Telegram digest at session end (mode=session) or on failures (mode=failures)."""
    c = cfg or cfg_bot
    mode = _git_notify_mode(c)
    summary = write_git_session_summary()
    if mode == "off" or mode == "log":
        log.debug(f"Git push logged only [{category}]: {message[:80]}")
        return
    if mode != "session" and mode != "all":
        return
    if c is None:
        return
    ok_count = int(_git_session_stats.get("ok", 0))
    fail_count = int(_git_session_stats.get("fail", 0))
    if ok_count == 0 and fail_count == 0:
        return
    try:
        from core.telegram_broadcast import broadcast_ops

        fallback = (
            f"GIT SESSION SUMMARY\n"
            f"OK: {ok_count} | Failed: {fail_count}\n"
            f"{_git_session_stats.get('last_message', '')[:200]}\n"
            f"Details: logs/git_session_summary.txt"
        )
        broadcast_ops(
            c,
            "git_session_summary",
            {
                "ok": ok_count,
                "fail": fail_count,
                "summary_path": "logs/git_session_summary.txt",
                "journal_path": "logs/git_sync_journal.jsonl",
            },
            fallback,
        )
    except Exception as exc:
        log.debug(f"Git session telegram: {exc}")


def _notify_git_push_result(
    cfg: Optional[BotConfig],
    message: str,
    category: str,
    *,
    ok: bool,
    repo: str = "code",
) -> None:
    record_git_push_event(message, category, ok=ok, repo=repo)
    mode = _git_notify_mode(cfg)
    if mode == "off" or mode == "log" or mode == "session":
        return
    if mode == "failures" and ok:
        return
    if cfg is None:
        return
    try:
        from core.telegram_broadcast import notify_git_push

        notify_git_push(cfg, message[:200], category=category, ok=ok)
    except Exception:
        pass
# Batch multiple changes within this window into one commit
BATCH_WINDOW_SEC: float = 10.0


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def init(cfg: BotConfig, ollama_brain: Optional[Any] = None):
    """
    Initialize from BotConfig env vars.
    
    Sets up HANOON repo (primary), Grandmaster (models), and Logs repos.
    Idempotent: restore and repo verification run at most once per process.

    Args:
        cfg: Bot configuration.
        ollama_brain: Optional LLM brain used to generate AI commit messages.
    """
    global _repo, _token, _enabled, _ollama_brain, _git_init_done, _learning_restore_done
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
    # Git auto-watch runs only via scripts/start_git_sync.sh (standalone daemon).
    # HANOON session never starts the watcher — zero impact on trading loop.


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
        log.debug(f"Git push deferred until shutdown: {message[:70]}")
        return True
    if not _enabled:
        return False
    
    now = time.time()
    global _last_push_ts
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
        log.debug(f"Git push deferred until shutdown: {message[:70]}")
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
                        f"auto: {len(dirty)} change(s) — {preview}",
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


# ═════════════════════════════════════════════════════════════════════════════
# INTERNALS
# ═════════════════════════════════════════════════════════════════════════════

_pending_pushes: List[dict] = []
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


# Large / local-only artifacts — never `git add` from training hooks (.gitignore)
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
        global _last_push_ts, _push_count, _failed_pushes
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
                _last_push_ts = time.time()
                return True

            files = filter_git_addable(files, repo_root)
            if not files:
                _last_push_ts = time.time()
                return True
            
            # Stage files
            stage_cmds = []
            for f in files:
                stage_cmds.append(["git", "add", f])
            
            if not stage_cmds:
                _last_push_ts = time.time()
                return True
            
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            full_message = f"{message}\n\nCategory: {category}\nTimestamp: {timestamp}\nAuto-pushed by git_sync.py"
            commit_cmd = ["git", "commit", "-m", full_message, "--allow-empty"]
            push_cmd = ["git", "push", target_repo, "HEAD:main"]
            
            all_success = True
            for cmd in stage_cmds:
                result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    log.debug(f"Git stage failed: {' '.join(cmd)}: {result.stderr.strip()}")
                    all_success = False
            
            if all_success:
                result = subprocess.run(commit_cmd, cwd=repo_root, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    log.debug(f"Git commit failed: {result.stderr.strip()}")
                    all_success = False
                else:
                    log.debug(f"Git commit: {message[:60]}")
            
            if all_success:
                result = subprocess.run(push_cmd, cwd=repo_root, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    log.debug(f"Git push failed: {result.stderr.strip()}")
                    all_success = False
                else:
                    _push_count += 1
                    _last_push_ts = time.time()
                    log.debug(f"GitHub: pushed #{_push_count} to {repo_url or 'default'} — {category}: {message[:60]}")
                    _notify_git_push_result(
                        cfg_bot, message, category, ok=True, repo=repo_url or "code"
                    )
            else:
                _failed_pushes += 1
                _notify_git_push_result(
                    cfg_bot, message, category, ok=False, repo=repo_url or "code"
                )
            
            return all_success
            
        except subprocess.TimeoutExpired:
            log.warning(f"Git push timed out [{category}]")
            _failed_pushes += 1
            return False
        except Exception as exc:
            log.debug(f"Git push failed [{category}]: {exc}")
            _failed_pushes += 1
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
    return _github_clone_url(slug, _token)


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
    fixed = _github_clone_url(slug, _token) if slug else ""
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
    global _repo, _token
    if not _repo:
        return None
    return _github_clone_url(_normalize_github_slug(_repo), _token) or None


def _verify_repo() -> bool:
    """Verify the GitHub repo is reachable."""
    if not _repo:
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


# ═════════════════════════════════════════════════════════════════════════════
# GRANDMASTER PUSH (Secondary Repo for Model Weights)
# ═════════════════════════════════════════════════════════════════════════════

def push_weights_to_repo(weight_files: List[str], repo_url: str, message: str) -> bool:
    """
    Push model weights to a secondary repo (e.g. Grandmaster).
    Clones the target repo into a temp directory, copies weights,
    commits, and pushes — without touching the primary HANOON repo.
    """
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="grandmaster_push_")
        
        auth_url = _resolve_clone_url(repo_url) or (
            _github_clone_url(_normalize_github_slug(repo_url), _token) if repo_url else ""
        )
        if not auth_url or not _git_clone(auth_url, tmpdir, label="Grandmaster", timeout=60):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return False
        
        # Copy weights
        for wf in weight_files:
            src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), wf)
            if os.path.exists(src):
                dst = os.path.join(tmpdir, os.path.basename(wf))
                shutil.copy2(src, dst)
        
        # Configure git identity
        subprocess.run(["git", "config", "user.email", "bot@hanoon.local"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "HANUN-Bot"], cwd=tmpdir, capture_output=True)
        
        # Commit & push
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        commit_cmd = ["git", "commit", "-m", message, "--allow-empty"]
        subprocess.run(commit_cmd, cwd=tmpdir, capture_output=True)
        push_cmd = ["git", "push", auth_url, "HEAD:main"]
        result = subprocess.run(push_cmd, cwd=tmpdir, capture_output=True, text=True, timeout=60)
        
        shutil.rmtree(tmpdir, ignore_errors=True)
        
        if result.returncode == 0:
            log.info(f"🏛️ Grandmaster push success: {message}")
            return True
        else:
            log.warning(f"Grandmaster push failed: {result.stderr.strip()}")
            return False
            
    except Exception as exc:
        log.error(f"Grandmaster push error: {exc}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# MULTI-REPO ROUTING
# ═════════════════════════════════════════════════════════════════════════════

def _get_repo_url(repo_key: str) -> Optional[str]:
    """Get authenticated repo URL for HANOON, Grandmaster, or Logs."""
    if not _token:
        return None
    if repo_key == "code":
        return _remote_url()
    elif repo_key == "grandmaster":
        repo = (getattr(cfg_bot, "GITHUB_GRANDMASTER_REPO", None) or os.getenv("GITHUB_GRANDMASTER_REPO", "") or "").strip()
        return _resolve_clone_url(repo)
    elif repo_key == "logs":
        repo = (getattr(cfg_bot, "GITHUB_LOGS_REPO", None) or os.getenv("GITHUB_LOGS_REPO", "") or "").strip()
        return _resolve_clone_url(repo)
    return None


def _resolve_target_repos(files: Optional[List[str]], category: str) -> Dict[str, List[str]]:
    """Determine which repos get which files based on category and file paths."""
    if files is None:
        files = _detect_changed_files(REPO_DIR)
        files = _apply_bloat_guard(files, REPO_DIR)

    result: Dict[str, List[str]] = {"code": [], "logs": [], "grandmaster": []}
    
    for f in files:
        basename = os.path.basename(f)
        routed = False
        
        # Route based on file path patterns (most specific)
        for route_key, patterns in REPO_ROUTES.items():
            for pattern in patterns:
                if basename == pattern or pattern in f:
                    result[route_key].append(f)
                    routed = True
                    break
            if routed:
                break
        
        if not routed:
            # Fallback: use category as hint
            repo_from_category = CATEGORY_TO_REPO.get(category, "code")
            result[repo_from_category].append(f)
    
    # Remove empty entries
    return {k: v for k, v in result.items() if v}


def _bootstrap_empty_repo(tmpdir: str, repo_key: str) -> None:
    """Initialize an empty secondary repo before first push."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmpdir, capture_output=True)
    readme = (
        f"# HANOON {repo_key.upper()} repo\n\n"
        f"Auto-synced by HANOON git_sync — do not edit manually.\n"
        f"Repo role: **{repo_key}**\n"
    )
    with open(os.path.join(tmpdir, "README.md"), "w") as f:
        f.write(readme)
    subprocess.run(["git", "add", "README.md"], cwd=tmpdir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"init: HANOON {repo_key} repo"],
        cwd=tmpdir, capture_output=True,
    )


def push_to_secondary_repo(repo_key: str, files: List[str], message: str, category: str) -> bool:
    """Push files to a secondary repo (logs or grandmaster) via clone-push."""
    repo_url = _get_repo_url(repo_key)
    if not repo_url:
        return False
    
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix=f"{repo_key}_push_")
        
        auth_url = repo_url or _get_repo_url(repo_key)
        cloned = bool(auth_url and _git_clone(auth_url, tmpdir, label=repo_key, timeout=60))
        if not cloned:
            _bootstrap_empty_repo(tmpdir, repo_key)
            log.info(f"📦 Initialized empty {repo_key} repo — first push")
        
        # Copy files
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for wf in files:
            src = os.path.join(repo_root, wf)
            if os.path.exists(src):
                dst = os.path.join(tmpdir, wf)
                os.makedirs(os.path.dirname(dst), exist_ok=True) if os.path.dirname(dst) else None
                shutil.copy2(src, dst)
        
        subprocess.run(["git", "config", "user.email", "bot@hanoon.local"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "HANUN-Bot"], cwd=tmpdir, capture_output=True)
        
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        commit_msg = f"[{repo_key}] {message}\n\nCategory: {category}\nTimestamp: {timestamp}\nAuto-pushed by git_sync.py"
        subprocess.run(["git", "commit", "-m", commit_msg, "--allow-empty"], cwd=tmpdir, capture_output=True)
        push_cmd = ["git", "push", "-u", auth_url, "HEAD:main"] if auth_url else ["git", "push", "-u", "origin", "main"]
        result = subprocess.run(push_cmd, cwd=tmpdir, capture_output=True, text=True, timeout=90)
        if result.returncode != 0 and "rejected" in (result.stderr or result.stdout or "").lower():
            subprocess.run(
                ["git", "pull", "--rebase", auth_url or "origin", "main"],
                cwd=tmpdir, capture_output=True, text=True, timeout=90,
            )
            result = subprocess.run(push_cmd, cwd=tmpdir, capture_output=True, text=True, timeout=90)
        
        shutil.rmtree(tmpdir, ignore_errors=True)
        
        if result.returncode == 0:
            log.info(f"✅ {repo_key} push success: {message[:60]}")
            return True
        else:
            log.warning(f"{repo_key} push failed: {(result.stderr or result.stdout or '')[:200]}")
            log.debug(f"{repo_key} push failed: {result.stderr.strip()}")
            return False
            
    except Exception as exc:
        log.debug(f"{repo_key} push error: {exc}")
        return False


# Global config reference for routing
cfg_bot: Any = None

def set_global_config(cfg: Any):
    """Set global config reference for repo routing."""
    global cfg_bot
    cfg_bot = cfg


def _should_defer_git_push(category: str = "general") -> bool:
    """HANOON defers session pushes; standalone daemon never defers."""
    if is_standalone_mode():
        return False
    if category in ("shutdown", "manual_sync"):
        return False
    if cfg_bot is not None and not getattr(cfg_bot, "GIT_PUSH_DURING_SESSION", False):
        return True
    return False

def push_trade(ticker: str, action: str, price: float, qty: float):
    """Push after a trade event."""
    return push_change(
        f"trade: {action} {qty:.0f}x {ticker} @ ${price:.2f}",
        files=["performance.csv", "live_metrics.json", "audit_trail.jsonl"],
        category="trade",
    )


def push_training(ticker: str, timesteps: int, return_pct: float):
    """Push after training completion — to HANOON and Grandmaster."""
    ha_ok = push_change(
        f"train: {ticker} {timesteps} steps return={return_pct:+.1f}%",
        files=[f"models/ppo_trader_warmup_*.zip", "training_journal.json", "audit_trail.jsonl"],
        category="training",
    )
    gm_ok = push_weights_to_repo(
        ["ppo_trader.zip", "models/transformer_model.pth", "models/lstm_model.h5",
         "models/fusion_state.json", "models/model_accuracy.json"],
        repo_url=_get_repo_url("grandmaster"),
        message=f"train: {ticker} {timesteps} steps return={return_pct:+.1f}%",
    ) if _get_repo_url("grandmaster") else False
    return ha_ok or gm_ok


def push_daily_summary(nav: float, equity: float):
    """Push after daily summary."""
    return push_change(
        f"daily: NAV=${nav:,.0f} equity=${equity:,.0f}",
        files=["performance.csv", "live_metrics.json", "audit_trail.jsonl"],
        category="daily",
    )


def push_model_update(model_path: str = "ppo_trader.zip"):
    """Push after model update (online fine-tune) — to HANOON and Grandmaster."""
    ha_nun_ok = push_change(
        f"model: updated {os.path.basename(model_path)}",
        files=[model_path, "audit_trail.jsonl"],
        category="model",
    )
    gm_ok = push_weights_to_repo(
        [model_path],
        repo_url=_get_repo_url("grandmaster"),
        message=f"model: checkpoint {os.path.basename(model_path)}",
    ) if _get_repo_url("grandmaster") else False
    return ha_nun_ok or gm_ok


def push_guardrail_event(event_type: str, details: str = ""):
    """Push after significant guardrail event."""
    return push_change(
        f"guardrail: {event_type} — {details}",
        files=["audit_trail.jsonl"],
        category="guardrail",
    )


def push_config_change(config_hash_old: str, config_hash_new: str):
    """Push after configuration change."""
    return push_change(
        f"config: hash {config_hash_old[:8]} → {config_hash_new[:8]}",
        files=["core/config.py", "audit_trail.jsonl"],
        category="config",
    )


def push_feature_update():
    """Push after feature engineering update."""
    return push_change(
        "features: enhanced features deployed",
        files=["core/features_enhanced.py", "core/features.py", "audit_trail.jsonl"],
        category="features",
    )


def push_error(error_message: str, context: str = ""):
    """Push error snapshot for debugging."""
    return push_change(
        f"error: {context} — {error_message[:100]}",
        files=["HANOON.log", "audit_trail.jsonl", "bot_state.json"],
        category="error",
    )


def push_startup(mode: str, ticker: str):
    """Push on bot startup (force push)."""
    return push_change(
        f"startup: mode={mode} ticker={ticker}",
        files=["HANOON.log", "audit_trail.jsonl"],
        category="startup",
    )


def push_shutdown(final_nav: float, return_pct: float):
    """Push on bot shutdown."""
    return push_change(
        f"shutdown: NAV=${final_nav:,.0f} return={return_pct:+.1f}%",
        files=["performance.csv", "live_metrics.json", "bot_state.json", "audit_trail.jsonl"],
        category="shutdown",
    )


def push_full_shutdown_sync(final_nav: float, return_pct: float, report_path: str = "") -> bool:
    """
    On bot close: push ALL artifacts to HANOON, Logs, and Grandmaster repos.
    Blocks until complete (synchronous).
    """
    global _last_push_ts
    _last_push_ts = 0  # bypass debounce for shutdown

    tag = f"shutdown_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    log.info("📤 Full shutdown sync → HANOON + Logs + Grandmaster...")

    # ── HANOON (code + bot state) ──
    hanoon_files = [
        "performance.csv", "live_metrics.json", "bot_state.json", "audit_trail.jsonl",
        "models/scalper_weights.json", "models/pilot_experience.json",
        "models/flight_log.jsonl", "models/pattern_memory_bank.json",
        "models/consciousness.json", "models/thought_journal.jsonl",
        "models/ai_decision_log.jsonl", "models/trade_journal.json",
        "models/experience_buffer.jsonl", "models/cognitive_state.json",
        "models/profit_hunt_ledger.jsonl",
        "models/account_snapshots.jsonl", "models/account_evaluation_log.jsonl",
        "ppo_trader.zip",
    ]
    if report_path and os.path.exists(report_path):
        hanoon_files.append(report_path)

    ok_ha = push_change(
        f"shutdown: NAV=${final_nav:,.0f} return={return_pct:+.1f}% | {tag}",
        files=hanoon_files,
        category="shutdown",
    )

    import glob as glob_mod
    log_paths = []
    for f in ["HANOON.log", "logs/HANOON.log", "training_journal.json",
              "models/ai_decision_log.jsonl", "models/thought_journal.jsonl", "audit_trail.jsonl",
              "logs/git_sync_journal.jsonl", "logs/git_session_summary.txt"]:
        if os.path.exists(os.path.join(REPO_DIR, f)):
            log_paths.append(f)
    log_paths.extend(glob_mod.glob(os.path.join(REPO_DIR, "models/daily_reports/*.json")))
    log_paths = [os.path.relpath(p, REPO_DIR) for p in log_paths]
    if report_path and os.path.exists(report_path):
        log_paths.append(report_path if not os.path.isabs(report_path) else os.path.relpath(report_path, REPO_DIR))

    ok_logs = push_to_secondary_repo(
        "logs", log_paths,
        f"session close {tag} | NAV=${final_nav:,.0f}",
        "shutdown",
    ) if _get_repo_url("logs") and log_paths else False

    # ── Grandmaster (models + training) ──
    gm_files = [
        "ppo_trader.zip", "models/ppo_trader.zip",
        "models/transformer_model.pth", "models/lstm_model.h5",
        "models/fusion_state.json", "models/model_accuracy.json",
        "models/scalper_weights.json", "models/pilot_experience.json",
    ]
    ok_gm = push_weights_to_repo(
        [f for f in gm_files if os.path.exists(os.path.join(REPO_DIR, f))],
        repo_url=_get_repo_url("grandmaster"),
        message=f"shutdown checkpoint {tag} | return={return_pct:+.1f}%",
    ) if _get_repo_url("grandmaster") else False

    try:
        sync_all_learning_artifacts(release_tag=tag)
    except Exception as exc:
        log.debug(f"Learning artifact sync: {exc}")

    log.info(f"📤 Shutdown sync: HANOON={'✓' if ok_ha else '✗'} Logs={'✓' if ok_logs else '✗'} Grandmaster={'✓' if ok_gm else '✗'}")
    try:
        flush_git_telegram_summary(cfg_bot)
    except Exception:
        pass
    return ok_ha or ok_logs or ok_gm


def get_stats() -> dict:
    """Get git sync statistics."""
    return {
        "enabled": _enabled,
        "total_pushes": _push_count,
        "failed_pushes": _failed_pushes,
        "last_push_ts": _last_push_ts,
        "last_push_age_sec": time.time() - _last_push_ts if _last_push_ts else None,
        "pending_queue": len(_pending_pushes),
        "tracked_files": len(TRACKED_FILES),
        "repo": _repo,
    }


def push_model_release(version: str, model_path: str = "ppo_trader.zip", notes: str = ""):
    """
    Create a git tag/release after training completion.
    Tags every model version so we can roll back and track progress.
    """
    if _should_defer_git_push("release"):
        log.debug(f"Model release deferred until shutdown: v{version}")
        return True
    try:
        now = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        tag_name = f"v{version}_{now}"

        ensure_github_cli(force_auth=True)

        subprocess.run(
            ["git", "tag", "-a", tag_name, "-m", f"HANOON model release {version} | {notes}"],
            cwd=REPO_DIR,
            capture_output=True,
        )

        push_change(
            f"release: model v{version} tagged as {tag_name}",
            files=[model_path, "models/scalper_weights.json", "models/training_history.json",
                   "models/experience_buffer.jsonl", "models/pilot_experience.json"],
            category="release",
        )

        # Push tags and create GitHub release for large artifacts
        try:
            subprocess.run(["git", "push", "origin", tag_name], cwd=REPO_DIR,
                           capture_output=True, timeout=120)
        except Exception:
            subprocess.run(["git", "push", "--tags"], cwd=REPO_DIR, capture_output=True, timeout=120)

        repo_root = REPO_DIR
        if _token and _repo and _gh_cli_available():
            _run_gh(
                ["release", "create", tag_name,
                 "--title", f"HANOON {tag_name}",
                 "--notes", notes or f"Model release {version}"],
                cwd=repo_root,
            )
        if os.path.exists(os.path.join(REPO_DIR, model_path)):
            push_large_file_to_release(model_path, tag_name, notes)

        log.info(f"🏷 Git release tagged: {tag_name}")
        try:
            from core.telegram_broadcast import notify_model_release
            if cfg_bot is not None:
                notify_model_release(cfg_bot, str(version), tag_name, notes)
        except Exception:
            pass
        return True
    except Exception as exc:
        log.warning(f"Git release failed: {exc}")
        return False


def push_large_file_to_release(file_path: str, release_tag: str, description: str = "") -> bool:
    """
    Upload large files (model weights >10MB) to a GitHub release using gh CLI.
    """
    ensure_github_cli(force_auth=True)
    if not _gh_cli_available():
        return False
    try:
        full_path = os.path.join(REPO_DIR, file_path)
        if not os.path.exists(full_path):
            log.debug(f"Large file not found: {file_path}")
            return False

        file_size_mb = os.path.getsize(full_path) / (1024 * 1024)
        if file_size_mb < 10:
            log.debug(f"File {file_path} is only {file_size_mb:.1f}MB — use normal git push")
            return True

        gh_args = ["release", "upload", release_tag, file_path, "-n", description or file_path]
        if _repo:
            gh_args.extend(["--repo", _repo])

        if _run_gh(gh_args, cwd=REPO_DIR, timeout=300):
            log.info(f"📦 Large file uploaded to release {release_tag}: {file_path} ({file_size_mb:.1f}MB)")
            return True
        return False

    except Exception as exc:
        log.debug(f"Large file release upload skipped: {exc}")
        return False


def sync_all_learning_artifacts(release_tag: str = None) -> bool:
    """
    Sync all AI learning artifacts to GitHub, using releases for large files.
    This is called after major training sessions.
    
    Large files (model weights) go to releases, small files go to git.
    Everything is linked and version-tracked.
    """
    if _should_defer_git_push("training"):
        log.debug(f"Full learning sync deferred until shutdown: {release_tag or 'auto'}")
        return True
    if not release_tag:
        release_tag = f"training_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    ensure_github_cli(force_auth=True)
    
    import glob as glob_mod
    large_patterns = [
        "models/transformer_model.pth",
        "models/lstm_model.h5",
        "ppo_trader.zip",
        "models/ppo_trader.zip",
    ]
    large_files = []
    for pattern in large_patterns:
        if "*" in pattern:
            large_files.extend(glob_mod.glob(os.path.join(REPO_DIR, pattern)))
        else:
            p = os.path.join(REPO_DIR, pattern)
            if os.path.exists(p):
                large_files.append(pattern)

    # Ensure release exists before uploading large files (requires gh CLI)
    if _enabled and _token and _repo and _gh_cli_available():
        _run_gh(
            ["release", "create", release_tag,
             "--title", f"HANOON training {release_tag}",
             "--notes", f"Training sync {datetime.utcnow().isoformat()}"],
            cwd=REPO_DIR,
        )

    if _gh_cli_available():
        for lf in large_files:
            rel = lf if not lf.startswith(REPO_DIR) else os.path.relpath(lf, REPO_DIR)
            push_large_file_to_release(rel, release_tag, f"Training model weights - {datetime.utcnow().isoformat()}")

    small_files = [
        "models/pilot_experience.json",
        "models/flight_log.jsonl",
        "models/pattern_memory_bank.json",
        "models/pattern_snapshots.jsonl",
        "models/scalper_weights.json",
        "models/ai_guidelines.txt",
        "models/parameter_adjustments.json",
        "models/improvement_history.json",
        "models/trained_record_hashes.jsonl",
        "models/consciousness.json",
        "models/cognitive_state.json",
        "models/thought_journal.jsonl",
        "models/trade_journal.json",
        "models/experience_buffer.jsonl",
        "models/ai_decision_log.jsonl",
        "models/account_snapshots.jsonl",
        "models/account_evaluation_log.jsonl",
    ]

    push_change(
        f"training: all learning artifacts synced | {release_tag}",
        files=small_files,
        category="training"
    )
    
    if _enabled:
        try:
            subprocess.run(["git", "tag", "-a", release_tag, "-m", f"Training sync {release_tag}"], 
                        cwd=REPO_DIR, capture_output=True)
            subprocess.run(["git", "push", "origin", release_tag], cwd=REPO_DIR, capture_output=True, timeout=120)
            log.info(f"🏷 Release tag: {release_tag}")
        except Exception:
            pass

    return True


# ═════════════════════════════════════════════════════════════════════════════
# LEARNING PERSISTENCE — cross-device experience sync
# ═════════════════════════════════════════════════════════════════════════════

LEARNING_ARTIFACTS: Dict[str, List[str]] = {
    "code": [
        "models/consciousness.json",
        "models/pilot_experience.json",
        "models/flight_log.jsonl",
        "models/pattern_memory_bank.json",
        "models/pattern_snapshots.jsonl",
        "models/scalper_weights.json",
        "models/improvement_history.json",
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
    ],
}

# Required on disk before skipping remote HANOON fetch (optional artifacts may be created at runtime)
LEARNING_REQUIRED_CODE: List[str] = [
    "models/consciousness.json",
    "models/pilot_experience.json",
    "models/scalper_weights.json",
]


def _learning_files_flat() -> List[str]:
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
    if not _enabled and not _repo:
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
    if not _enabled:
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
    if not _gh_cli_available() or not _repo:
        return False
    if not _model_needs_release_download():
        return False
    target = os.path.join(REPO_DIR, "ppo_trader.zip")
    try:
        if _run_gh(
            ["release", "download", "--repo", _repo, "latest", "--pattern", "ppo_trader.zip", "--dir", REPO_DIR],
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
    if not _enabled:
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


def push_learning_checkpoint(reason: str = "checkpoint", full_sync: bool = False) -> bool:
    """Push learning artifacts to HANOON + Logs + Grandmaster (never blocks trading loop if called via async)."""
    if not full_sync and _should_defer_git_push("training"):
        log.debug(f"Learning checkpoint deferred until shutdown: {reason}")
        return True
    if not _enabled:
        return False

    global _last_push_ts, _last_checkpoint_ts
    now = time.time()
    with _checkpoint_lock:
        if now - _last_checkpoint_ts < _CHECKPOINT_MIN_INTERVAL_SEC and not full_sync:
            log.debug(f"Learning checkpoint skipped (throttled): {reason}")
            return False
        _last_checkpoint_ts = now
        _last_push_ts = 0

        tag = f"learn_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        existing = [f for f in _learning_files_flat() if os.path.exists(os.path.join(REPO_DIR, f))]

        hanoon_files = [f for f in existing if f in LEARNING_ARTIFACTS.get("code", [])]
        logs_files = [f for f in existing if f in LEARNING_ARTIFACTS.get("logs", [])]
        gm_files = [f for f in existing if f in LEARNING_ARTIFACTS.get("grandmaster", [])]

        ok = False
        if hanoon_files:
            ok = push_change(f"learn: {reason} | {tag}", files=hanoon_files, category="training") or ok
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
    """Non-blocking learning checkpoint — safe during startup / trading loop."""
    if not _enabled:
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
    if not _enabled:
        return {}
    out: Dict[str, bool] = {}
    out["learning"] = push_learning_checkpoint(reason, full_sync=True)
    return out