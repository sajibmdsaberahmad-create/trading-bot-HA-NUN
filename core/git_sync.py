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
from typing import List, Optional, Set, Dict, Any
from pathlib import Path
from datetime import datetime
from threading import Lock

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
# Batch multiple changes within this window into one commit
BATCH_WINDOW_SEC: float = 10.0


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def init(cfg: BotConfig, ollama_brain: Optional[Any] = None):
    """
    Initialize from BotConfig env vars.
    
    Sets up HANOON repo (primary), Grandmaster (models), and Logs repos.

    Args:
        cfg: Bot configuration.
        ollama_brain: Optional LLM brain used to generate AI commit messages.
    """
    global _repo, _token, _enabled, _ollama_brain
    if ollama_brain is not None:
        _ollama_brain = ollama_brain
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
        if getattr(cfg, "LEARNING_RESTORE_ON_STARTUP", True):
            try:
                restore_all_learning(cfg)
            except Exception as exc:
                log.debug(f"Learning restore at init: {exc}")
    else:
        log.info("GitHub sync disabled (no token/repo configured)")


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


# ═════════════════════════════════════════════════════════════════════════════
# INTERNALS
# ═════════════════════════════════════════════════════════════════════════════

_pending_pushes: List[dict] = []
_pending_lock = Lock()


def _queue_push(message: str, files: Optional[List[str]], category: str) -> bool:
    """Queue a push for the next batch window."""
    with _pending_lock:
        _pending_pushes.append({
            "message": message,
            "files": files,
            "category": category,
            "queued_at": time.time(),
        })
    
    # If this is the first queued item, set a timer to flush
    if len(_pending_pushes) == 1:
        time.sleep(BATCH_WINDOW_SEC)
        _flush_pending()
    
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
            
            # Stage files
            stage_cmds = []
            for f in files:
                full = os.path.join(repo_root, f)
                if os.path.exists(full):
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
            else:
                _failed_pushes += 1
            
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
    """Detect which tracked files have changed since last push."""
    try:
        # Get list of modified files
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            # Fallback: use git status
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root, capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return list(TRACKED_FILES)  # Push everything if we can't tell
            files = []
            for line in result.stdout.strip().splitlines():
                if line:
                    # Format: "XY filename"
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        files.append(parts[1])
        else:
            files = result.stdout.strip().splitlines()
        
        # Filter to only tracked / routable files
        tracked = []
        for f in files:
            if f in TRACKED_FILES or any(f.endswith(p) or p in f for p in TRACKED_FILES):
                tracked.append(f)
            elif f.startswith("core/") and f.endswith(".py"):
                tracked.append(f)
            elif f.startswith("scripts/"):
                tracked.append(f)
        
        # Add all tracked files if we're unsure
        if not tracked:
            tracked = [f for f in TRACKED_FILES if os.path.exists(os.path.join(repo_root, f))]
        
        return tracked[:20]  # Limit batch size
        
    except Exception:
        return list(TRACKED_FILES)


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
              "models/ai_decision_log.jsonl", "models/thought_journal.jsonl", "audit_trail.jsonl"]:
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


def _learning_files_flat() -> List[str]:
    out: List[str] = []
    for files in LEARNING_ARTIFACTS.values():
        out.extend(files)
    return list(dict.fromkeys(out))


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
    target = os.path.join(REPO_DIR, "ppo_trader.zip")
    if os.path.exists(target) and os.path.getsize(target) > 100_000:
        return False
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


def push_learning_checkpoint(reason: str = "checkpoint") -> bool:
    """Push all learning artifacts to HANOON + Logs + Grandmaster repos."""
    if not _enabled:
        return False

    global _last_push_ts
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

    try:
        sync_all_learning_artifacts(release_tag=tag)
    except Exception:
        pass

    return ok


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
    out["learning"] = push_learning_checkpoint(reason)
    return out