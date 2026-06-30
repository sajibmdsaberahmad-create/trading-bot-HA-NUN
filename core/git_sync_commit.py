#!/usr/bin/env python3
"""Commit message helpers — extracted from git_sync."""

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

def _brain_snapshot_line() -> str:
    """One-line owned-brain context for commit messages."""
    try:
        p = os.path.join(REPO_DIR, "models", "owned_brain_state.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            stage = d.get("stage", "?")
            ds = int(d.get("dataset_pairs", 0) or 0)
            evo = int(d.get("evolution_count", 0) or 0)
            return f"brain={stage} dataset={ds} evolutions={evo}"
    except Exception:
        pass
    return ""
def _summarize_changed_files(files: List[str]) -> str:
    """Bucket changed paths for human-readable commit bodies."""
    buckets: Dict[str, int] = {
        "halim": 0, "models": 0, "replay": 0, "docs": 0, "core": 0, "other": 0,
    }
    for raw in files:
        f = raw.replace("\\", "/")
        if f.startswith("halim/"):
            buckets["halim"] += 1
        elif f.startswith("models/") or f.endswith(".zip"):
            buckets["models"] += 1
        elif f.startswith("data/replay/"):
            buckets["replay"] += 1
        elif f.startswith("docs/"):
            buckets["docs"] += 1
        elif f.startswith("core/"):
            buckets["core"] += 1
        else:
            buckets["other"] += 1
    return " ".join(f"{k}={v}" for k, v in buckets.items() if v)
def _enrich_commit_message(
    message: str,
    category: str,
    files: Optional[List[str]],
) -> str:
    """Build descriptive multi-line commit message (title + context)."""
    title = (message or category or "sync").strip().split("\n")[0][:200]
    lines = [title]
    brain = _brain_snapshot_line()
    if brain:
        lines.append(brain)
    if files:
        summary = _summarize_changed_files(files)
        if summary:
            lines.append(f"artifacts: {summary}")
        preview = ", ".join(os.path.basename(f) for f in files[:4])
        if len(files) > 4:
            preview += f" +{len(files) - 4} more"
        lines.append(f"files({len(files)}): {preview}")
    if category:
        lines.append(f"category: {category}")
    return "\n".join(lines)
def _build_auto_commit_message(files: List[str]) -> str:
    """Replace opaque 'auto: N change(s)' with bucket + brain context."""
    summary = _summarize_changed_files(files)
    preview = ", ".join(os.path.basename(f) for f in files[:3])
    if len(files) > 3:
        preview += f" +{len(files) - 3} more"
    brain = _brain_snapshot_line()
    msg = f"sync: {len(files)} files — {preview}"
    if summary:
        msg += f" | {summary}"
    if brain:
        msg += f" | {brain}"
    return msg
def _record_auto_commit_in_brain_log(message: str, category: str) -> None:
    """Append git auto-commit line to BRAIN_DEVELOPMENT_LOG (session audit)."""
    if category not in _AUTO_COMMIT_LOG_CATEGORIES:
        return
    try:
        log_path = os.path.join(REPO_DIR, "docs", "BRAIN_DEVELOPMENT_LOG.md")
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        first_line = (message or "").strip().split("\n")[0][:140]
        line = f"- `{ts}` **git_{category}** — {first_line}"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        log.debug(f"Brain development log append: {exc}")
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
    os.makedirs(os.path.dirname(_GIT_JOURNAL_PATH), exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = {
        "timestamp": ts,
        "ok": ok,
        "category": category,
        "repo": repo,
        "message": (message or "")[:500],
    }
    with S._git_journal_lock:
        if ok:
            S._git_session_stats["ok"] = int(S._git_session_stats.get("ok", 0)) + 1
            S._git_session_stats["last_ok_at"] = ts
            S._git_session_stats["last_message"] = entry["message"]
        else:
            S._git_session_stats["fail"] = int(S._git_session_stats.get("fail", 0)) + 1
        by_cat = S._git_session_stats.setdefault("by_category", {})
        by_cat[category] = int(by_cat.get(category, 0)) + 1
        try:
            with open(_GIT_JOURNAL_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.debug(f"Git journal write failed: {exc}")
def write_git_session_summary() -> str:
    """Write end-of-session summary file from journal stats."""
    os.makedirs(os.path.dirname(_GIT_SESSION_SUMMARY_PATH), exist_ok=True)
    with S._git_journal_lock:
        stats = dict(S._git_session_stats)
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
    ok_count = int(S._git_session_stats.get("ok", 0))
    fail_count = int(S._git_session_stats.get("fail", 0))
    if ok_count == 0 and fail_count == 0:
        return
    try:
        from core.telegram_broadcast import broadcast_ops

        fallback = (
            f"GIT SESSION SUMMARY\n"
            f"OK: {ok_count} | Failed: {fail_count}\n"
            f"{S._git_session_stats.get('last_message', '')[:200]}\n"
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
