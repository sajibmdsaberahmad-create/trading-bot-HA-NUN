#!/usr/bin/env python3
"""Split git_sync.py into push, routing, and learning persistence modules."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "core" / "git_sync.py"

# Line ranges (1-based inclusive) from section headers
PUSH_START, PUSH_END = 895, 1382
ROUTING_START, ROUTING_END = 1383, 2015
LEARNING_START = 2016

PUSH_HEADER = '''#!/usr/bin/env python3
"""Git push queue and executor — extracted from git_sync."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Timer
from typing import Any, Dict, List, Optional, Set

from core.config import BotConfig
from core.notify import log
from core import git_sync_defer as _defer

# Wired by git_sync.init()
_repo: Optional[str] = None
_token: Optional[str] = None
_enabled: bool = False
_push_lock = Lock()
_last_push_ts: float = 0.0
_push_count: int = 0
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bind_push_state(repo: Optional[str], token: Optional[str], enabled: bool) -> None:
    global _repo, _token, _enabled
    _repo = repo
    _token = token
    _enabled = enabled


'''

ROUTING_HEADER = '''#!/usr/bin/env python3
"""Grandmaster + multi-repo routing — extracted from git_sync."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from core.notify import log

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_token: Optional[str] = None


def bind_routing_token(token: Optional[str]) -> None:
    global _token
    _token = token


'''

LEARNING_HEADER = '''#!/usr/bin/env python3
"""Learning artifact sync — extracted from git_sync."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


'''


def main() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
    backup = SRC.with_suffix(".py.bak")
    if not backup.exists():
        backup.write_text("".join(lines), encoding="utf-8")

    push_body = "".join(lines[PUSH_START - 1 : PUSH_END])
    routing_body = "".join(lines[ROUTING_START - 1 : ROUTING_END])
    learning_body = "".join(lines[LEARNING_START - 1 :])

    (ROOT / "core" / "git_sync_push.py").write_text(PUSH_HEADER + push_body, encoding="utf-8")
    (ROOT / "core" / "git_sync_routing.py").write_text(ROUTING_HEADER + routing_body, encoding="utf-8")
    (ROOT / "core" / "git_sync_learning.py").write_text(LEARNING_HEADER + learning_body, encoding="utf-8")

    # Trim git_sync.py — keep through PUBLIC API + wire imports
    head = "".join(lines[: PUSH_START - 1])
    bridge = '''

# ── Extracted modules (push internals, routing, learning persistence) ──
from core import git_sync_push as _push
from core import git_sync_routing as _routing
from core import git_sync_learning as _learning

# Re-export push internals used by public API
_pending_pushes = _push._pending_pushes
_pending_lock = _push._pending_lock
_do_push = _push._do_push
_queue_push = _push._queue_push
_flush_pending = _push._flush_pending
_filter_git_addable = _push.filter_git_addable
_git_porcelain_files = _push._git_porcelain_files
_collect_dirty_files = _push._collect_dirty_files
_detect_changed_files = _push._detect_changed_files
_apply_bloat_guard = _push._apply_bloat_guard
_build_combined_message = _push._build_combined_message
_is_pushable_path = _push._is_pushable_path

push_weights_to_repo = _routing.push_weights_to_repo
_get_repo_url = _routing._get_repo_url
_route_push_to_repo = _routing._route_push_to_repo
_push_to_secondary_repo = _routing._push_to_secondary_repo

sync_all_learning_artifacts = _learning.sync_all_learning_artifacts
push_learning_checkpoint_async = _learning.push_learning_checkpoint_async
restore_learning_artifacts = _learning.restore_learning_artifacts
LEARNING_ARTIFACTS = _learning.LEARNING_ARTIFACTS

'''
    new_src = head + bridge
    SRC.write_text(new_src, encoding="utf-8")
    print("git_sync.py trimmed; wrote push/routing/learning modules")


if __name__ == "__main__":
    main()
