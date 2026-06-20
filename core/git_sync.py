#!/usr/bin/env python3
"""
core/git_sync.py — Automatic GitHub push for every significant event.

Every time the algo:
- Saves a new trained model
- Records a completed trade
- Hits a daily summary
- Starts up

...this module auto-commits and pushes to the configured GitHub repo.

Setup:
  1. Add GITHUB_TOKEN and GITHUB_REPO to .env
  2. Call git_sync.push(message, files_to_add) from anywhere in the code
"""

import os
import subprocess
import sys
from typing import List, Optional

from core.config import BotConfig
from core.notify import log


_repo: Optional[str] = None
_token: Optional[str] = None
_enabled: bool = False


def init(cfg: BotConfig):
    """Initialize from BotConfig env vars."""
    global _repo, _token, _enabled
    _repo = getattr(cfg, "GITHUB_REPO", None) or os.getenv("GITHUB_REPO", "")
    _token = getattr(cfg, "GITHUB_TOKEN", None) or os.getenv("GITHUB_TOKEN", "")
    _enabled = bool(_repo and _token)


def _remote_url() -> str:
    return f"https://{_token}@github.com/{_repo}.git"


def push(message: str, files: List[str] = None, allow_empty: bool = False) -> bool:
    """
    Commit current state and push to GitHub.
    
    Args:
        message: Commit message
        files: Specific files to add (None = add all tracked + new)
        allow_empty: Allow empty commits (for "no-op" pushes)
    
    Returns True if push succeeded.
    """
    if not _enabled:
        return False
    
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    try:
        cmds = []
        
        if files:
            # Stage specific files
            for f in files:
                full = os.path.join(repo_root, f)
                if os.path.exists(full):
                    cmds.append(["git", "add", f])
        else:
            # Stage all changes
            cmds.append(["git", "add", "-A"])
        
        # Commit
        cmds.append(["git", "commit", "-m", message, "--allow-empty" if allow_empty else ""])
        
        # Push
        cmds.append(["git", "push", _remote_url(), "HEAD:main"])
        
        for cmd in cmds:
            result = subprocess.run(
                cmd,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                log.debug(f"Git sync step failed: {' '.join(cmd)}: {result.stderr.strip()}")
                return False
        
        log.info(f"GitHub: pushed — {message[:60]}")
        return True
    
    except subprocess.TimeoutExpired:
        log.warning("GitHub push timed out")
        return False
    except Exception as exc:
        log.debug(f"GitHub push failed: {exc}")
        return False


def push_trade(ticker: str, action: str, price: float, qty: float):
    """Push after a trade event."""
    return push(
        f"trade: {action} {qty:.0f}x {ticker} @ ${price:.2f}",
        files=["performance.csv", "live_metrics.json", "training_journal.json"],
    )


def push_training(ticker: str, timesteps: int, return_pct: float):
    """Push after training completion."""
    return push(
        f"train: {ticker} {timesteps} steps return={return_pct:+.1f}%",
        files=[f"models/ppo_trader_warmup_*.zip", "training_journal.json"],
    )


def push_daily_summary(nav: float, equity: float):
    """Push after daily summary."""
    return push(
        f"daily: NAV=${nav:,.0f} equity=${equity:,.0f}",
        files=["performance.csv", "live_metrics.json"],
    )


def push_startup(mode: str, ticker: str):
    """Push on bot startup."""
    return push(
        f"startup: mode={mode} ticker={ticker}",
        files=["trading_bot.log"],
        allow_empty=True,
    )