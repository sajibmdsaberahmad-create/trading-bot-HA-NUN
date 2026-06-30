#!/usr/bin/env python3
"""Split git_sync.py into state, commit, push, routing, learning modules."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "core" / "git_sync.py"

COMMIT_FUNCS = {
    "_brain_snapshot_line", "_summarize_changed_files", "_enrich_commit_message",
    "_build_auto_commit_message", "_record_auto_commit_in_brain_log",
    "_git_notify_mode", "record_git_push_event", "write_git_session_summary",
    "flush_git_telegram_summary", "_notify_git_push_result",
}

PUSH_FUNCS = {
    "_is_pushable_path", "filter_git_addable", "_git_porcelain_files",
    "_collect_dirty_files", "_queue_push", "_flush_pending",
    "_build_combined_message", "_do_push", "_apply_bloat_guard",
    "_detect_changed_files", "_normalize_github_slug", "_github_clone_url",
    "_resolve_clone_url", "_git_clone", "_git_pull_rebase_origin",
    "_git_push_origin_main", "_git_push_with_rebase_retry",
    "_sanitize_github_repos", "_remote_url", "_verify_repo",
}

ROUTING_FUNCS = {
    "push_weights_to_repo", "_get_repo_url", "_resolve_target_repos",
    "_bootstrap_empty_repo", "push_to_secondary_repo", "set_global_config",
    "flush_batched_git_sync", "flush_replay_session_git_sync",
    "push_trade", "push_training", "push_daily_summary", "push_model_update",
    "push_guardrail_event", "push_config_change", "push_feature_update",
    "push_error", "push_startup", "push_shutdown", "push_full_shutdown_sync",
    "get_stats", "push_model_release", "push_large_file_to_release",
    "sync_all_learning_artifacts",
}

LEARNING_FUNCS = {
    "_learning_files_flat", "_force_learning_restore", "_local_learning_file_ok",
    "_hanoon_learning_needs_fetch", "_repo_patterns_need_pull",
    "_model_needs_release_download", "is_learning_current", "_should_restore_file",
    "pull_from_secondary_repo", "restore_hanoon_learning", "restore_model_from_release",
    "restore_all_learning", "push_learning_checkpoint", "push_learning_checkpoint_async",
    "verify_all_repos", "sync_all_repos",
}

ALL_EXTRACTED = COMMIT_FUNCS | PUSH_FUNCS | ROUTING_FUNCS | LEARNING_FUNCS

HEADER = '''#!/usr/bin/env python3
"""{title} — extracted from git_sync."""

from __future__ import annotations

import glob as glob_mod
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Timer
from typing import Any, Dict, List, Optional, Set

from core.config import BotConfig
from core.notify import log
from core import git_sync_defer as _defer
from core import git_sync_state as S

REPO_DIR = S.REPO_DIR

'''


def _chunk(lines: list[str], node: ast.AST) -> str:
    return "".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)

    # State module: globals block lines 52-90 + MIN_PUSH + NEVER_PUSH
    state_lines = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not names:
                continue
            n0 = names[0]
            if n0 in {
                "_repo", "_token", "_enabled", "_push_lock", "_last_push_ts",
                "_push_count", "_failed_pushes", "_ollama_brain", "_gh_cli_cached",
                "_gh_missing_logged", "_gh_auth_verified", "_git_init_done",
                "_learning_restore_done", "_last_checkpoint_ts",
                "_CHECKPOINT_MIN_INTERVAL_SEC", "_standalone_mode", "_watcher_stop",
                "_watcher_thread", "_last_dirty_fingerprint", "_flush_timer",
                "_git_journal_lock", "_git_session_stats", "MIN_PUSH_INTERVAL_SEC",
            }:
                state_lines.append(_chunk(lines, node))
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_NEVER_PUSH_FILES":
            state_lines.append(_chunk(lines, node))

    state_src = '''#!/usr/bin/env python3
"""Shared git_sync module state."""

from __future__ import annotations

import hashlib
import os
import threading
from threading import Lock, Timer
from typing import Any, Dict, Optional, Set

from core import git_sync_defer as _defer

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

''' + "".join(state_lines)

    (ROOT / "core" / "git_sync_state.py").write_text(state_src, encoding="utf-8")

    groups = {
        "git_sync_commit": (COMMIT_FUNCS, "Commit message helpers"),
        "git_sync_push": (PUSH_FUNCS, "Push queue and executor"),
        "git_sync_routing": (ROUTING_FUNCS, "Multi-repo routing"),
        "git_sync_learning": (LEARNING_FUNCS, "Learning persistence"),
    }
    extracted: dict[str, list[str]] = {k: [] for k in groups}
    learning_consts: list[str] = []
    keep: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n in ("LEARNING_ARTIFACTS", "LEARNING_REQUIRED_CODE") for n in names):
                learning_consts.append(_chunk(lines, node))
                continue
            if any(n in ("_pending_pushes", "_pending_lock") for n in names):
                extracted["git_sync_push"].append(_chunk(lines, node))
                continue
            state_names = {
                "_repo", "_token", "_enabled", "_push_lock", "_last_push_ts",
                "_push_count", "_failed_pushes", "_ollama_brain", "_gh_cli_cached",
                "_gh_missing_logged", "_gh_auth_verified", "_git_init_done",
                "_learning_restore_done", "_last_checkpoint_ts", "_CHECKPOINT_MIN_INTERVAL_SEC",
                "_standalone_mode", "_watcher_stop", "_watcher_thread",
                "_last_dirty_fingerprint", "_flush_timer", "_git_journal_lock",
                "_git_session_stats", "MIN_PUSH_INTERVAL_SEC",
                "_checkpoint_lock", "_checkpoint_pending", "_checkpoint_batched_reasons",
                "_checkpoint_flush_timer",
            }
            if any(n in state_names for n in names):
                continue
            keep.append(_chunk(lines, node))
            continue
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_NEVER_PUSH_FILES":
            continue
        if isinstance(node, ast.FunctionDef):
            placed = False
            for mod, (names, _) in groups.items():
                if node.name in names:
                    extracted[mod].append(_chunk(lines, node))
                    placed = True
                    break
            if not placed:
                keep.append(_chunk(lines, node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            keep.insert(0, _chunk(lines, node))
        elif isinstance(node, ast.Expr) and isinstance(getattr(node.value, "value", None), str):
            s = _chunk(lines, node)
            if "═══" in s:
                continue
            keep.append(s)
        elif not isinstance(node, ast.Assign):
            try:
                keep.append(_chunk(lines, node))
            except Exception:
                pass

    for mod, (names, title) in groups.items():
        body = "".join(extracted[mod])
        if mod == "git_sync_learning":
            body = "".join(learning_consts) + body
        if mod == "git_sync_push" and "_pending_pushes" not in body:
            body = "_pending_pushes: list = []\n_pending_lock = Lock()\n" + body
        # Rewrite global state refs to S.*
        body = re.sub(r"^\s*global\s+[^\n]+\n", "", body, flags=re.M)
        body = re.sub(r"\b_enabled\b", "S._enabled", body)
        body = re.sub(r"\b_repo\b", "S._repo", body)
        body = re.sub(r"\b_token\b", "S._token", body)
        body = re.sub(r"\b_last_push_ts\b", "S._last_push_ts", body)
        body = re.sub(r"\b_push_count\b", "S._push_count", body)
        body = re.sub(r"\b_failed_pushes\b", "S._failed_pushes", body)
        body = re.sub(r"\b_last_checkpoint_ts\b", "S._last_checkpoint_ts", body)
        body = re.sub(r"\b_CHECKPOINT_MIN_INTERVAL_SEC\b", "S._CHECKPOINT_MIN_INTERVAL_SEC", body)
        body = re.sub(r"\b_git_session_stats\b", "S._git_session_stats", body)
        body = re.sub(r"\b_git_journal_lock\b", "S._git_journal_lock", body)
        body = re.sub(r"\b_learning_restore_done\b", "S._learning_restore_done", body)
        body = re.sub(r"\b_git_init_done\b", "S._git_init_done", body)
        body = re.sub(r"\bglobal S\.(_enabled|_repo|_token|_last_push_ts|_push_count|_failed_pushes|_last_checkpoint_ts)\b", "", body)
        body = body.replace("S.S.", "S.")
        (ROOT / "core" / f"{mod}.py").write_text(HEADER.format(title=title) + body, encoding="utf-8")
        print(f"wrote {mod}.py ({len(extracted[mod])} funcs)")

    bridge = '''
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
'''

    backup = SRC.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")

    kept_text = "".join(keep)
    kept_text = re.sub(r"^\s*global\s+[^\n]+\n", "", kept_text, flags=re.M)
    for name in (
        "_enabled", "_repo", "_token", "_ollama_brain", "_git_init_done",
        "_learning_restore_done", "_last_push_ts", "_gh_cli_cached",
        "_gh_missing_logged", "_gh_auth_verified", "_standalone_mode",
        "_last_dirty_fingerprint", "_watcher_stop", "_checkpoint_lock",
        "_checkpoint_flush_timer",
    ):
        kept_text = re.sub(rf"\b{name}\b", f"S.{name}", kept_text)
    kept_text = kept_text.replace("S.S.", "S.")
    kept_text = kept_text.replace("S._checkpoint_lock", "_defer.checkpoint_lock")
    kept_text = kept_text.replace("S._checkpoint_batched_reasons", "_defer.checkpoint_batched_reasons")
    kept_text = kept_text.replace("S._checkpoint_flush_timer", "_defer.checkpoint_flush_timer")
    kept_text = re.sub(r"\bREPO_DIR\b", "S.REPO_DIR", kept_text)
    kept_text = kept_text.replace("S.REPO_DIR", "REPO_DIR")  # keep REPO_DIR alias
    kept_text = "REPO_DIR = S.REPO_DIR\n" + kept_text

    header = '''#!/usr/bin/env python3
"""
core/git_sync.py — Automatic GitHub push for EVERY change.

Facade over git_sync_commit, git_sync_push, git_sync_routing, git_sync_learning.
"""

'''
    bridge_at_top = '''
from core import git_sync_state as S
from core import git_sync_commit as _gcommit
from core import git_sync_push as _gpush
from core import git_sync_routing as _groute
from core import git_sync_learning as _glearn
from core import git_sync_defer as _defer

REPO_DIR = S.REPO_DIR

'''
    # defer helpers used in kept code
    defer_imports = """
_is_replay_live = _defer.is_replay_live
_git_session_push_enabled = _defer.git_session_push_enabled
_batch_checkpoints_enabled = _defer.batch_checkpoints_enabled
_queue_batched_checkpoint = _defer.queue_batched_checkpoint
_schedule_batched_checkpoint_flush = _defer.schedule_batched_checkpoint_flush
_should_defer_git_push = _defer.should_defer_git_push
_shutdown_git_reason = _defer.shutdown_git_reason
_checkpoint_lock = _defer.checkpoint_lock
cfg_bot = _defer.cfg_bot

"""

    SRC.write_text(header + bridge_at_top + defer_imports + kept_text + bridge, encoding="utf-8")
    print(f"git_sync.py -> {len(kept_text.splitlines())} lines + bridge")


if __name__ == "__main__":
    main()
