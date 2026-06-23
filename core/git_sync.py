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

# These files are ALWAYS tracked and pushed on change
TRACKED_FILES: Set[str] = {
    "ppo_trader.zip",               # Model
    "performance.csv",              # Trade log
    "audit_trail.jsonl",            # Guardrail audit log
    "live_metrics.json",            # Dashboard metrics
    "bot_state.json",               # Bot state
    "training_journal.json",        # Training history
    "HA-NUN.log",                   # Logs
    "models/scalper_weights.json",  # Learned weights
    "models/daily_guidelines.txt",  # Daily self-improvement
    "core/config.py",               # Configuration
    "core/agent.py",                # Agent logic
    "core/agent_enhanced.py",       # Enhanced AI
    "core/ai_guardrails.py",        # Guardrails
    "core/features_enhanced.py",    # Features
    "core/risk.py",                 # Risk management
    "core/hmrs.py",                 # HMRS engine
    "core/stationary_features.py",  # Stationary features
    "core/transformer_model.py",    # TFT + Distillation
    "core/multi_model_fusion.py",   # Fusion engine
}

# Multi-repo routing: which files go to which repo
REPO_ROUTES: Dict[str, Set[str]] = {
    "code": {
        "ppo_trader.zip",
        "core/config.py",
        "core/agent.py",
        "core/agent_enhanced.py",
        "core/ai_guardrails.py",
        "core/features_enhanced.py",
        "core/features.py",
        "core/risk.py",
        "core/hmrs.py",
        "core/stationary_features.py",
        "core/transformer_model.py",
        "core/multi_model_fusion.py",
        "core/fusion_overrides.py",
        "core/scalper_sniper_integration.py",
        "core/trader.py",
        "core/scalper_runner.py",
        "core/scanner.py",
        "core/sniper.py",
        "core/sniper_heartbeat.py",
        "core/sniper_orchestrator.py",
        "core/sniper_screener.py",
        "main.py",
        "requirements.txt",
    },
    "logs": {
        "HA-NUN.log",
        "trading_bot.log",
        "performance.csv",
        "live_metrics.json",
        "bot_state.json",
        "audit_trail.jsonl",
        "training_journal.json",
        "models/scalper_weights.json",
        "models/daily_guidelines.txt",
        "models/thought_journal.jsonl",
        "models/consciousness.json",
    },
    "grandmaster": {
        "ppo_trader.zip",
        "models/ppo_trader.zip",
        "models/transformer_model.pth",
        "models/lstm_model.h5",
        "models/fusion_state.json",
        "models/model_accuracy.json",
        "models/model_manifest.json",
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

def init(cfg: BotConfig):
    """
    Initialize from BotConfig env vars.
    
    Sets up HA-NUN repo (primary), Grandmaster (models), and Logs repos.
    """
    global _repo, _token, _enabled
    _repo = (getattr(cfg, "GITHUB_HA_NUN_REPO", None) or os.getenv("GITHUB_HA_NUN_REPO", "") or
             getattr(cfg, "GITHUB_REPO", None) or os.getenv("GITHUB_REPO", ""))
    _token = getattr(cfg, "GITHUB_TOKEN", None) or os.getenv("GITHUB_TOKEN", "")
    _enabled = bool(_repo and _token)
    
    _grandmaster_repo = (getattr(cfg, "GITHUB_GRANDMASTER_REPO", None) or os.getenv("GITHUB_GRANDMASTER_REPO", "") or "").strip()
    _logs_repo = (getattr(cfg, "GITHUB_LOGS_REPO", None) or os.getenv("GITHUB_LOGS_REPO", "") or "").strip()
    
    if _enabled:
        log.info(f"GitHub sync initialized — HA-NUN={_repo} | Grandmaster={_grandmaster_repo or 'disabled'} | Logs={_logs_repo or 'disabled'}")
        if not _verify_repo():
            log.warning("HA-NUN repo verification failed — sync disabled")
            _enabled = False
        set_global_config(cfg)
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
        
        # Filter to only tracked files
        tracked = [f for f in files if os.path.basename(f) in TRACKED_FILES or 
                   any(f.endswith(tf) or tf in f for tf in TRACKED_FILES)]
        
        # Add all tracked files if we're unsure
        if not tracked:
            tracked = [f for f in TRACKED_FILES if os.path.exists(os.path.join(repo_root, f))]
        
        return tracked[:20]  # Limit batch size
        
    except Exception:
        return list(TRACKED_FILES)


def _remote_url() -> Optional[str]:
    """Build authenticated remote URL from global state."""
    global _repo, _token
    if not _repo:
        return None
    if _token and "@" not in _repo:
        return f"https://{_token}@github.com/{_repo}.git"
    return f"https://github.com/{_repo}.git"


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
    commits, and pushes — without touching the primary HA-NUN repo.
    """
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="grandmaster_push_")
        
        # Authenticated clone
        if _token and "@" not in repo_url:
            auth_url = repo_url.replace("https://", f"https://{_token}@")
        else:
            auth_url = repo_url
        
        clone_cmd = ["git", "clone", "--depth", "1", auth_url, tmpdir]
        result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            log.warning(f"Grandmaster clone failed: {result.stderr.strip()}")
            shutil.rmtree(tmpdir, ignore_errors=True)
            return False
        
        # Copy weights
        for wf in weight_files:
            src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), wf)
            if os.path.exists(src):
                dst = os.path.join(tmpdir, os.path.basename(wf))
                shutil.copy2(src, dst)
        
        # Configure git identity
        subprocess.run(["git", "config", "user.email", "bot@ha-nun.local"], cwd=tmpdir, capture_output=True)
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
    """Get authenticated repo URL for HA-NUN, Grandmaster, or Logs."""
    if not _token:
        return None
    if repo_key == "code":
        return _remote_url()
    elif repo_key == "grandmaster":
        repo = (getattr(cfg_bot, "GITHUB_GRANDMASTER_REPO", None) or os.getenv("GITHUB_GRANDMASTER_REPO", "") or "").strip()
        if repo:
            if "@" not in repo:
                return f"https://{_token}@{repo}.git" if not repo.startswith("https://") else f"https://{_token}@{repo.split('https://')[1]}"
            return repo
    elif repo_key == "logs":
        repo = (getattr(cfg_bot, "GITHUB_LOGS_REPO", None) or os.getenv("GITHUB_LOGS_REPO", "") or "").strip()
        if repo:
            if "@" not in repo:
                return f"https://{_token}@{repo}.git" if not repo.startswith("https://") else f"https://{_token}@{repo.split('https://')[1]}"
            return repo
    return None


def _resolve_target_repos(files: Optional[List[str]], category: str) -> Dict[str, List[str]]:
    """Determine which repos get which files based on category and file paths."""
    if files is None:
        files = list(TRACKED_FILES)
    
    result = {"code": [], "logs": [], "grandmaster": []}
    
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


def push_to_secondary_repo(repo_key: str, files: List[str], message: str, category: str) -> bool:
    """Push files to a secondary repo (logs or grandmaster) via clone-push."""
    repo_url = _get_repo_url(repo_key)
    if not repo_url:
        return False
    
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix=f"{repo_key}_push_")
        
        # Authenticated clone
        if _token and "@" not in repo_url:
            auth_url = f"https://{_token}@{repo_url.split('https://')[1]}" if "https://" in repo_url else repo_url
        else:
            auth_url = repo_url
        
        clone_cmd = ["git", "clone", "--depth", "1", auth_url, tmpdir]
        result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            log.debug(f"{repo_key} clone failed: {result.stderr.strip()}")
            shutil.rmtree(tmpdir, ignore_errors=True)
            return False
        
        # Copy files
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for wf in files:
            src = os.path.join(repo_root, wf)
            if os.path.exists(src):
                dst = os.path.join(tmpdir, wf)
                os.makedirs(os.path.dirname(dst), exist_ok=True) if os.path.dirname(dst) else None
                shutil.copy2(src, dst)
        
        subprocess.run(["git", "config", "user.email", "bot@ha-nun.local"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "HANUN-Bot"], cwd=tmpdir, capture_output=True)
        
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        commit_msg = f"[{repo_key}] {message}\n\nCategory: {category}\nTimestamp: {timestamp}\nAuto-pushed by git_sync.py"
        subprocess.run(["git", "commit", "-m", commit_msg, "--allow-empty"], cwd=tmpdir, capture_output=True)
        push_cmd = ["git", "push", auth_url, "HEAD:main"]
        result = subprocess.run(push_cmd, cwd=tmpdir, capture_output=True, text=True, timeout=60)
        
        shutil.rmtree(tmpdir, ignore_errors=True)
        
        if result.returncode == 0:
            log.info(f"✅ {repo_key} push success: {message[:60]}")
            return True
        else:
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
    """Push after training completion."""
    return push_change(
        f"train: {ticker} {timesteps} steps return={return_pct:+.1f}%",
        files=[f"models/ppo_trader_warmup_*.zip", "training_journal.json", "audit_trail.jsonl"],
        category="training",
    )


def push_daily_summary(nav: float, equity: float):
    """Push after daily summary."""
    return push_change(
        f"daily: NAV=${nav:,.0f} equity=${equity:,.0f}",
        files=["performance.csv", "live_metrics.json", "audit_trail.jsonl"],
        category="daily",
    )


def push_model_update(model_path: str = "ppo_trader.zip"):
    """Push after model update (online fine-tune)."""
    return push_change(
        f"model: updated {os.path.basename(model_path)}",
        files=[model_path, "audit_trail.jsonl"],
        category="model",
    )


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
        files=["HA-NUN.log", "audit_trail.jsonl", "bot_state.json"],
        category="error",
    )


def push_startup(mode: str, ticker: str):
    """Push on bot startup (force push)."""
    return push_change(
        f"startup: mode={mode} ticker={ticker}",
        files=["HA-NUN.log", "audit_trail.jsonl"],
        category="startup",
    )


def push_shutdown(final_nav: float, return_pct: float):
    """Push on bot shutdown."""
    return push_change(
        f"shutdown: NAV=${final_nav:,.0f} return={return_pct:+.1f}%",
        files=["performance.csv", "live_metrics.json", "bot_state.json", "audit_trail.jsonl"],
        category="shutdown",
    )


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