#!/usr/bin/env python3
"""Extract git_sync functions into push, routing, and learning modules."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "core" / "git_sync.py"

PUSH_METHODS = {
    "_is_pushable_path", "filter_git_addable", "_git_porcelain_files",
    "_collect_dirty_files", "_queue_push", "_flush_pending",
    "_build_combined_message", "_do_push", "_apply_bloat_guard",
    "_detect_changed_files", "_normalize_github_slug", "_github_clone_url",
    "_resolve_clone_url", "_git_clone", "_git_pull_rebase_origin",
    "_git_push_origin_main", "_git_push_with_rebase_retry",
    "_sanitize_github_repos", "_remote_url", "_verify_repo",
}

ROUTING_METHODS = {
    "push_weights_to_repo", "_get_repo_url", "_resolve_target_repos",
    "_bootstrap_empty_repo", "push_to_secondary_repo", "set_global_config",
    "flush_batched_git_sync", "flush_replay_session_git_sync",
    "push_trade", "push_training", "push_daily_summary", "push_model_update",
    "push_guardrail_event", "push_config_change", "push_feature_update",
    "push_error", "push_startup", "push_shutdown", "push_full_shutdown_sync",
    "get_stats", "push_model_release", "push_large_file_to_release",
    "sync_all_learning_artifacts",
}

LEARNING_METHODS = {
    "_learning_files_flat", "_force_learning_restore", "_local_learning_file_ok",
    "_hanoon_learning_needs_fetch", "_repo_patterns_need_pull",
    "_model_needs_release_download", "is_learning_current", "_should_restore_file",
    "pull_from_secondary_repo", "restore_hanoon_learning", "restore_model_from_release",
    "restore_all_learning", "push_learning_checkpoint", "push_learning_checkpoint_async",
    "verify_all_repos", "sync_all_repos",
}

GROUPS: Dict[str, Tuple[str, Set[str]]] = {
    "git_sync_push": ("_push_module", PUSH_METHODS),
    "git_sync_routing": ("_routing_module", ROUTING_METHODS),
    "git_sync_learning": ("_learning_module", LEARNING_METHODS),
}

HEADER = '''#!/usr/bin/env python3
"""Extracted from git_sync — {title}."""

from __future__ import annotations

import glob as glob_mod
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Timer
from typing import Any, Dict, List, Optional, Set

from core.config import BotConfig
from core.notify import log
from core import git_sync_defer as _defer

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


'''


def _src(lines: List[str], node: ast.FunctionDef) -> str:
    return "".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])


def _assign_src(lines: List[str], node: ast.Assign) -> str:
    return "".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)

    extracted: Dict[str, List[str]] = {k: [] for k in GROUPS}
    remain: List[str] = []

    learning_consts: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in (
                    "LEARNING_ARTIFACTS", "LEARNING_REQUIRED_CODE",
                    "_pending_pushes", "_pending_lock",
                ):
                    if t.id.startswith("LEARNING") or t.id == "_pending_pushes":
                        learning_consts.append(_assign_src(lines, node))
                    elif t.id == "_pending_lock":
                        extracted["git_sync_push"].append(_assign_src(lines, node))
                    continue
        if not isinstance(node, ast.FunctionDef):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                remain.append(_src(lines, node) if isinstance(node, ast.FunctionDef) else "")
            continue
        name = node.name
        placed = False
        for mod, (_, names) in GROUPS.items():
            if name in names:
                extracted[mod].append(_src(lines, node))
                placed = True
                break
        if not placed:
            remain.append(_src(lines, node))

    # Rebuild remain from non-function nodes + unassigned functions
    remain = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if any(node.name in names for _, names in GROUPS.values()):
                continue
            remain.append(_src(lines, node))
        elif isinstance(node, ast.Assign):
            names_assigned = [
                t.id for t in node.targets if isinstance(t, ast.Name)
            ]
            if any(n in ("LEARNING_ARTIFACTS", "LEARNING_REQUIRED_CODE", "_pending_pushes", "_pending_lock") for n in names_assigned):
                continue
            remain.append(_assign_src(lines, node))
        elif isinstance(node, ast.AnnAssign):
            remain.append(_assign_src(lines, node))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            remain.append(_assign_src(lines, node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            remain.append(_assign_src(lines, node) if hasattr(node, 'lineno') else "")
        else:
            try:
                remain.append("".join(lines[node.lineno - 1 : node.end_lineno or node.lineno]))
            except Exception:
                pass

    for mod, (_, _) in GROUPS.items():
        body = "".join(extracted[mod])
        if mod == "git_sync_learning":
            body = "".join(learning_consts) + body
        if mod == "git_sync_push" and "_pending_pushes" not in body:
            body = "_pending_pushes: List[dict] = []\n" + body
        (ROOT / "core" / f"{mod}.py").write_text(
            HEADER.format(title=mod.replace("_", " ")) + body, encoding="utf-8",
        )
        print(f"wrote {mod}.py")

    bridge = '''
# ── Extracted modules ──
from core import git_sync_push as _gpush
from core import git_sync_routing as _groute
from core import git_sync_learning as _glearn

# Push internals (module-level aliases for backward compat)
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

'''

    backup = SRC.with_suffix(".py.bak")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")

    new_text = "".join(remain) + bridge
    SRC.write_text(new_text, encoding="utf-8")
    print(f"git_sync.py trimmed to {len(new_text.splitlines())} lines")


if __name__ == "__main__":
    main()
