#!/usr/bin/env python3
"""
core/hanoon_clean_publish.py — Auto-publish clean snapshot → sajibmdsaberahmad-create/HANOON

The dev workspace (trading-bot-HA-NUN) keeps full history via git_sync.
This module pushes a **filtered portable bundle** to the clean HANOON algo repo
when models, learning state, or secrets change — not on every log line.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from core.config import BotConfig
from core.notify import log

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "publish_hanoon_repo.sh"
MARKER = REPO_ROOT / "runtime" / ".hanoon_clean_last_publish"
_LOCK = threading.Lock()
_in_flight = False
_last_attempt = 0.0

# Triggers that always attempt publish (subject to min interval)
_FORCE_TRIGGERS = frozenset({
    "shutdown",
    "model_release",
    "daily_learning",
    "consciousness_train",
    "manual",
})


def clean_repo_auto_publish_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    return bool(getattr(cfg, "HANOON_CLEAN_REPO_AUTO_PUBLISH", True))


def clean_repo_slug(cfg: Optional[BotConfig] = None) -> str:
    cfg = cfg or BotConfig()
    return (
        getattr(cfg, "GITHUB_CLEAN_ALGO_REPO", "")
        or os.getenv("GITHUB_CLEAN_ALGO_REPO", "")
        or "sajibmdsaberahmad-create/HANOON"
    ).strip()


def min_publish_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    return float(getattr(cfg, "HANOON_CLEAN_PUBLISH_MIN_SEC", 3600))


def schedule_clean_repo_publish(
    cfg: Optional[BotConfig] = None,
    *,
    trigger: str = "general",
    force: bool = False,
) -> bool:
    """
    Queue background publish to clean HANOON repo.
    Returns True if scheduled, False if skipped (debounced/disabled).
    """
    global _last_attempt, _in_flight

    cfg = cfg or BotConfig()
    if not clean_repo_auto_publish_enabled(cfg):
        return False
    if not PUBLISH_SCRIPT.exists():
        log.debug("hanoon_clean_publish: script missing")
        return False

    now = time.time()
    interval = min_publish_interval_sec(cfg)
    is_force = force or trigger in _FORCE_TRIGGERS

    with _LOCK:
        if _in_flight:
            log.debug(f"hanoon_clean_publish: already running ({trigger})")
            return False
        if not is_force and now - _last_attempt < interval:
            log.debug(
                f"hanoon_clean_publish: debounced ({trigger}, "
                f"{interval - (now - _last_attempt):.0f}s left)"
            )
            return False
        _last_attempt = now
        _in_flight = True

    def _worker():
        global _in_flight
        try:
            slug = clean_repo_slug(cfg)
            repo_url = f"https://github.com/{slug}.git"
            env = os.environ.copy()
            env["HANOON_REPO"] = repo_url
            log.info(f"📤 Publishing clean HANOON algo → {slug} ({trigger})…")
            proc = subprocess.run(
                ["bash", str(PUBLISH_SCRIPT)],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode == 0:
                MARKER.parent.mkdir(parents=True, exist_ok=True)
                MARKER.write_text(f"{time.time():.0f}|{trigger}|{slug}\n")
                log.info(f"✅ Clean HANOON repo updated ({trigger})")
            else:
                tail = (proc.stderr or proc.stdout or "")[-400:]
                log.warning(f"Clean HANOON publish failed ({trigger}): {tail}")
        except Exception as exc:
            log.warning(f"Clean HANOON publish error ({trigger}): {exc}")
        finally:
            with _LOCK:
                _in_flight = False

    threading.Thread(
        target=_worker, name=f"hanoon-clean-publish-{trigger}", daemon=True,
    ).start()
    return True
