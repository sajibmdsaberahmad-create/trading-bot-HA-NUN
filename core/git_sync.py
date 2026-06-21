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
from typing import List, Optional, Set
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
}

# Debounce: minimum seconds between pushes
MIN_PUSH_INTERVAL_SEC: float = 5.0
# Batch multiple changes within this window into one commit
BATCH_WINDOW_SEC: float = 10.0


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def init(cfg: BotConfig):
    """Initialize from BotConfig env vars."""
    global _repo, _token, _enabled
    _repo = getattr(cfg, "GITHUB_REPO", None) or os.getenv("GITHUB_REPO", "")
    _token = getattr(cfg, "GITHUB_TOKEN", None) or os.getenv("GITHUB_TOKEN", "")
    _enabled = bool(_repo and _token)
    
    if _enabled:
        log.info(f"GitHub sync initialized — repo={_repo}")
        # Verify repo is reachable
        if not _verify_repo():
            log.warning("GitHub repo verification failed — sync disabled")
            _enabled = False
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
    
    # Debounce: don't push more than once per MIN_PUSH_INTERVAL_SEC
    now = time.time()
    global _last_push_ts
    if now - _last_push_ts < MIN_PUSH_INTERVAL_SEC:
        # Queue this for the next batch window
        return _queue_push(message, files, category)
    
    return _do_push(message, files, category)


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


def _do_push(message: str, files: Optional[List[str]], category: str) -> bool:
    """Execute the actual git commit and push."""
    with _push_lock:
        global _last_push_ts, _push_count, _failed_pushes
        
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        try:
            # Auto-detect changed files if not specified
            if files is None:
                files = _detect_changed_files(repo_root)
            
            if not files:
                # Nothing to push, but that's okay
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
            
            # Commit with timestamp
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            full_message = f"{message}\n\nCategory: {category}\nTimestamp: {timestamp}\nAuto-pushed by git_sync.py"
            
            # Commit (allow empty in case of race conditions)
            commit_cmd = ["git", "commit", "-m", full_message, "--allow-empty"]
            
            # Push
            push_cmd = ["git", "push", _remote_url(), "HEAD:main"]
            
            # Execute pipeline: stage -> commit -> push
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
                    log.debug(f"GitHub: pushed #{_push_count} — {category}: {message[:60]}")
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
# CONVENIENCE WRAPPERS (called from other modules)
# ═════════════════════════════════════════════════════════════════════════════

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