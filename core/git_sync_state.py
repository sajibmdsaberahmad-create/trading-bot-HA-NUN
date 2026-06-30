#!/usr/bin/env python3
"""Shared git_sync module state."""

from __future__ import annotations

import hashlib
import os
import threading
from threading import Lock, Timer
from typing import Any, Dict, Optional, Set

from core import git_sync_defer as _defer

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_push_lock = Lock()
_watcher_stop = threading.Event()
_git_journal_lock = Lock()
_NEVER_PUSH_FILES: Set[str] = {
    ".env", ".env.local", ".env.production", ".env.backup",
    "credentials.json", "secrets.json",
}
