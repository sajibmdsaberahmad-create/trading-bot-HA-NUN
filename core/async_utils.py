#!/usr/bin/env python3
"""
core/async_utils.py — Non-blocking background workers for Git, council I/O, and heavy tasks.

PURPOSE
═══════════════════════════════════════════════════════════════════════════
Prevents the core trading loop from blocking on:
  - Git commits/pushes (can take 500ms+)
  - Ollama LLM inference (can take 1-3s)
  - File I/O, HTTP requests, notifications

All heavy operations are dispatched to a ThreadPoolExecutor running
in the background. The trading loop continues immediately.

USAGE
    from core.async_utils import BackgroundWorker
    worker = BackgroundWorker()
    
    # Fire-and-forget git push
    worker.submit_git_commit(["models/weights.json"], "message")
    
    # Fire-and-forget ollama reasoning
    explanation = worker.submit_ollama(brain.explain_decision, decision, price, ticker)
    # explanation is None if not ready yet, actual text later
    
    # Wait for completion (non-blocking poll)
    if explanation is not None:
        print(explanation)
"""

import os
import sys
import time
import json
import subprocess
import threading
from typing import Optional, Callable, Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime

from core.notify import log
from core.time_utils import utc_now, utc_now_iso, utc_today


@dataclass
class GitTask:
    """Task for async git operations."""
    files: List[str]
    message: str
    push: bool = False
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass
class OllamaTask:
    """Task for async Ollama inference."""
    func: Callable
    args: tuple
    kwargs: dict
    timestamp: str = field(default_factory=utc_now_iso)


class BackgroundWorker:
    """
    Singleton thread pool for all non-blocking background operations.
    
    All public methods return immediately. Results are available
    via callback or polling.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, max_workers: int = 4):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self, max_workers: int = 4):
        if self._initialized:
            return
        self._initialized = True
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._git_queue: List[GitTask] = []
        self._ollama_futures: Dict[str, Future] = {}
        self._stats = {
            "git_commits": 0,
            "git_pushes": 0,
            "ollama_calls": 0,
            "failures": 0,
        }
        log.info(f"BackgroundWorker started | workers={max_workers}")
    
    def submit_git_commit(self, files: List[str], message: str, push: bool = False) -> Optional[str]:
        """
        Submit a git commit task to background thread pool.
        NEVER blocks the trading loop.
        
        Args:
            files: List of file paths to add
            message: Commit message
            push: If True, also push to remote (blocking network)
            
        Returns:
            Task ID for tracking, or None if submission failed
        """
        task = GitTask(files=files, message=message, push=push)
        self._git_queue.append(task)
        self._executor.submit(self._process_git_queue)
        return task.timestamp
    
    def _process_git_queue(self):
        """Process all pending git tasks in background."""
        while self._git_queue:
            task = self._git_queue.pop(0)
            try:
                self._do_git_commit(task)
            except Exception as exc:
                self._stats["failures"] += 1
                log.debug(f"Background git commit failed: {exc}")
    
    def _do_git_commit(self, task: GitTask):
        """Execute git add + commit + optional push."""
        try:
            # Stage files
            files_str = " ".join(task.files)
            subprocess.run(
                ["git", "add"] + task.files,
                capture_output=True, timeout=10, check=False
            )
            
            # Commit
            result = subprocess.run(
                ["git", "commit", "-m", task.message],
                capture_output=True, text=True, timeout=10, check=False
            )
            
            if result.returncode == 0:
                self._stats["git_commits"] += 1
                log.debug(f"Background commit: {task.message[:50]}")
            else:
                # Nothing to commit (no changes) is fine
                if "nothing to commit" not in result.stderr:
                    log.debug(f"Git commit issue: {result.stderr[:100]}")
            
            # Push if requested (non-blocking network I/O in thread)
            if task.push and result.returncode == 0:
                push_result = subprocess.run(
                    ["git", "push"],
                    capture_output=True, text=True, timeout=30, check=False
                )
                if push_result.returncode == 0:
                    self._stats["git_pushes"] += 1
                    try:
                        from core.git_sync import cfg_bot, record_git_push_event
                        if cfg_bot is not None:
                            record_git_push_event(
                                task.message[:200], "background", ok=True, repo="background"
                            )
                            from core.git_sync import _git_notify_mode
                            if _git_notify_mode(cfg_bot) == "all":
                                from core.telegram_broadcast import notify_git_push
                                notify_git_push(cfg_bot, task.message[:200], category="background", ok=True)
                    except Exception:
                        pass
                else:
                    log.debug(f"Background push issue: {push_result.stderr[:100]}")
                    
        except subprocess.TimeoutExpired:
            log.warning(f"Git operation timed out: {task.message[:50]}")
        except Exception as exc:
            log.debug(f"Git background error: {exc}")
    
    def submit_ollama(self, func: Callable, *args, **kwargs) -> Optional[str]:
        """
        Submit an Ollama function call to background thread pool.
        Returns task ID for tracking.
        """
        task = OllamaTask(func=func, args=args, kwargs=kwargs)
        future = self._executor.submit(self._run_ollama_task, task)
        task_id = task.timestamp
        self._ollama_futures[task_id] = future
        self._stats["ollama_calls"] += 1
        return task_id
    
    def _run_ollama_task(self, task: OllamaTask):
        """Execute Ollama call in background."""
        try:
            return task.func(*task.args, **task.kwargs)
        except Exception as exc:
            log.debug(f"Ollama background call failed: {exc}")
            return None
    
    def get_ollama_result(self, task_id: str) -> Optional[Any]:
        """
        Poll for Ollama result. Returns None if not ready yet.
        Call this periodically (not every bar) to avoid overhead.
        """
        future = self._ollama_futures.get(task_id)
        if future is None:
            return None
        if not future.done():
            return None
        try:
            result = future.result(timeout=0.1)
            del self._ollama_futures[task_id]
            return result
        except Exception:
            return None
    
    def shutdown(self, wait: bool = False):
        """Shutdown the thread pool."""
        try:
            self._executor.shutdown(wait=wait, cancel_futures=True)
        except Exception:
            pass
        log.info(f"BackgroundWorker shutdown | stats={self._stats}")


# ═════════════════════════════════════════════════════════════════════════════
# ATOMIC FILE WRITER
# ═════════════════════════════════════════════════════════════════════════════

class AtomicFileWriter:
    """
    Thread-safe, atomic file writer to prevent corruption during
    hot-reloads and concurrent access.
    
    Writes to a .tmp file first, then swaps with os.replace().
    This guarantees that readers never see partial writes.
    """
    
    @staticmethod
    def write(filepath: str, data: str, encoding: str = "utf-8") -> bool:
        """
        Atomically write string data to file.
        
        Args:
            filepath: Target file path
            data: String content to write
            encoding: File encoding
            
        Returns:
            True on success, False on failure
        """
        tmp_path = f"{filepath}.tmp"
        try:
            with open(tmp_path, 'w', encoding=encoding) as f:
                f.write(data)
            # Atomic swap (POSIX rename is atomic)
            os.replace(tmp_path, filepath)
            return True
        except Exception as exc:
            log.debug(f"Atomic write failed for {filepath}: {exc}")
            # Clean up temp file
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return False
    
    @staticmethod
    def write_json(filepath: str, obj: Any, indent: int = 2) -> bool:
        """Atomically write JSON object to file."""
        try:
            data = json.dumps(obj, indent=indent, default=str)
            return AtomicFileWriter.write(filepath, data)
        except Exception as exc:
            log.debug(f"Atomic JSON write failed for {filepath}: {exc}")
            return False


# ═════════════════════════════════════════════════════════════════════════════
# FILE WATCHER FOR HOT-RELOAD
# ═════════════════════════════════════════════════════════════════════════════

class FileWatcher:
    """
    Simple file modification watcher for hot-reloading weights/config.
    Checks file mtime on poll() and calls callback if changed.
    """
    
    def __init__(self, filepath: str, callback: Callable, poll_interval: float = 5.0):
        self.filepath = filepath
        self.callback = callback
        self.poll_interval = poll_interval
        self._last_mtime: float = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._suppress_until: float = 0.0

    def suppress_for(self, seconds: float = 20.0):
        """Ignore self-triggered writes (e.g. bot saving weights) for N seconds."""
        self._suppress_until = time.time() + seconds
        try:
            self._last_mtime = self._get_mtime()
        except Exception:
            pass
    
    def start(self):
        """Start watching in background thread."""
        self._running = True
        self._last_mtime = self._get_mtime()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop watching."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
    
    def _get_mtime(self) -> float:
        try:
            return os.path.getmtime(self.filepath)
        except Exception:
            return 0.0
    
    def _watch_loop(self):
        while self._running:
            try:
                current_mtime = self._get_mtime()
                if current_mtime > self._last_mtime and current_mtime > 0:
                    self._last_mtime = current_mtime
                    if time.time() < self._suppress_until:
                        continue
                    log.debug(f"FileWatcher: {self.filepath} changed — triggering reload")
                    try:
                        self.callback(self.filepath)
                    except Exception as exc:
                        log.debug(f"FileWatcher callback failed: {exc}")
                time.sleep(self.poll_interval)
            except Exception:
                time.sleep(self.poll_interval)
    
    def check_now(self) -> bool:
        """Check immediately if file changed. Returns True if changed."""
        current_mtime = self._get_mtime()
        if current_mtime > self._last_mtime and current_mtime > 0:
            self._last_mtime = current_mtime
            return True
        return False


# Global background worker instance
_worker: Optional[BackgroundWorker] = None

def get_background_worker() -> BackgroundWorker:
    """Get or create the global BackgroundWorker singleton."""
    global _worker
    if _worker is None:
        _worker = BackgroundWorker()
    return _worker