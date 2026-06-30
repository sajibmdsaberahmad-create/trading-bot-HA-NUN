#!/usr/bin/env python3
"""Multi-repo routing — extracted from git_sync."""

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

def push_weights_to_repo(weight_files: List[str], repo_url: str, message: str) -> bool:
    """
    Push model weights to a secondary repo (e.g. Grandmaster).
    Clones the target repo into a temp directory, copies weights,
    commits, and pushes — without touching the primary HANOON repo.
    """
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="grandmaster_push_")
        
        auth_url = _resolve_clone_url(repo_url) or (
            _github_clone_url(_normalize_github_slug(repo_url), S._token) if repo_url else ""
        )
        if not auth_url or not _git_clone(auth_url, tmpdir, label="Grandmaster", timeout=60):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return False
        
        # Copy weights
        for wf in weight_files:
            src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), wf)
            if os.path.exists(src):
                dst = os.path.join(tmpdir, os.path.basename(wf))
                shutil.copy2(src, dst)
        
        # Configure git identity
        subprocess.run(["git", "config", "user.email", "bot@hanoon.local"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "HANUN-Bot"], cwd=tmpdir, capture_output=True)
        
        # Commit & push
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        commit_cmd = ["git", "commit", "-m", message, "--allow-empty"]
        subprocess.run(commit_cmd, cwd=tmpdir, capture_output=True)
        result = _git_push_with_rebase_retry(tmpdir, timeout=60)
        
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
def _get_repo_url(repo_key: str) -> Optional[str]:
    """Get authenticated repo URL for HANOON, Grandmaster, or Logs."""
    if not S._token:
        return None
    if repo_key == "code":
        return _remote_url()
    elif repo_key == "grandmaster":
        repo = (getattr(cfg_bot, "GITHUB_GRANDMASTER_REPO", None) or os.getenv("GITHUB_GRANDMASTER_REPO", "") or "").strip()
        return _resolve_clone_url(repo)
    elif repo_key == "logs":
        repo = (getattr(cfg_bot, "GITHUB_LOGS_REPO", None) or os.getenv("GITHUB_LOGS_REPO", "") or "").strip()
        return _resolve_clone_url(repo)
    return None
def _resolve_target_repos(files: Optional[List[str]], category: str) -> Dict[str, List[str]]:
    """Determine which repos get which files based on category and file paths."""
    if files is None:
        files = _detect_changed_files(REPO_DIR)
        files = _apply_bloat_guard(files, REPO_DIR)

    result: Dict[str, List[str]] = {"code": [], "logs": [], "grandmaster": []}
    
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
def _bootstrap_empty_repo(tmpdir: str, repo_key: str) -> None:
    """Initialize an empty secondary repo before first push."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmpdir, capture_output=True)
    readme = (
        f"# HANOON {repo_key.upper()} repo\n\n"
        f"Auto-synced by HANOON git_sync — do not edit manually.\n"
        f"Repo role: **{repo_key}**\n"
    )
    with open(os.path.join(tmpdir, "README.md"), "w") as f:
        f.write(readme)
    subprocess.run(["git", "add", "README.md"], cwd=tmpdir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"init: HANOON {repo_key} repo"],
        cwd=tmpdir, capture_output=True,
    )
def push_to_secondary_repo(repo_key: str, files: List[str], message: str, category: str) -> bool:
    """Push files to a secondary repo (logs or grandmaster) via clone-push."""
    repo_url = _get_repo_url(repo_key)
    if not repo_url:
        return False
    
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix=f"{repo_key}_push_")
        
        auth_url = repo_url or _get_repo_url(repo_key)
        cloned = bool(auth_url and _git_clone(auth_url, tmpdir, label=repo_key, timeout=60))
        if not cloned:
            _bootstrap_empty_repo(tmpdir, repo_key)
            log.info(f"📦 Initialized empty {repo_key} repo — first push")
        
        # Copy files
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for wf in files:
            src = os.path.join(repo_root, wf)
            if os.path.exists(src):
                dst = os.path.join(tmpdir, wf)
                os.makedirs(os.path.dirname(dst), exist_ok=True) if os.path.dirname(dst) else None
                shutil.copy2(src, dst)
        
        subprocess.run(["git", "config", "user.email", "bot@hanoon.local"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "HANUN-Bot"], cwd=tmpdir, capture_output=True)
        
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        commit_msg = f"[{repo_key}] {message}\n\nCategory: {category}\nTimestamp: {timestamp}\nAuto-pushed by git_sync.py"
        subprocess.run(["git", "commit", "-m", commit_msg, "--allow-empty"], cwd=tmpdir, capture_output=True)
        result = _git_push_with_rebase_retry(tmpdir, timeout=90)
        
        shutil.rmtree(tmpdir, ignore_errors=True)
        
        if result.returncode == 0:
            log.info(f"✅ {repo_key} push success: {message[:60]}")
            return True
        else:
            log.warning(f"{repo_key} push failed: {(result.stderr or result.stdout or '')[:200]}")
            log.debug(f"{repo_key} push failed: {result.stderr.strip()}")
            return False
            
    except Exception as exc:
        log.debug(f"{repo_key} push error: {exc}")
        return False
def set_global_config(cfg: Any):
    """Set global config reference for repo routing."""
    cfg_bot = cfg
    _defer.set_defer_config(cfg)
def flush_batched_git_sync(
    summary_reason: str = "batched",
    *,
    full_sync: bool = False,
    force: bool = True,
) -> bool:
    """One consolidated learning push from all queued checkpoint reasons."""
    if (
        not is_replay_live()
        and not _shutdown_git_reason(summary_reason)
        and not _git_session_push_enabled()
    ):
        with _checkpoint_lock:
            n = len(_checkpoint_batched_reasons)
        if n:
            log.debug(
                f"Git batch flush deferred ({summary_reason}) — {n} reason(s) queued for shutdown"
            )
        return False

    with _checkpoint_lock:
        reasons = sorted(_checkpoint_batched_reasons)
        _checkpoint_batched_reasons.clear()
        if _checkpoint_flush_timer is not None:
            _checkpoint_flush_timer.cancel()
            _checkpoint_flush_timer = None

    if not reasons and not full_sync:
        return False

    combined = summary_reason
    if reasons:
        preview = ", ".join(reasons[:8])
        if len(reasons) > 8:
            preview += f" +{len(reasons) - 8} more"
        combined = f"{summary_reason}: {preview}"

    S._last_checkpoint_ts = 0
    log.info(f"📤 Consolidated git sync — {len(reasons)} batched reason(s)")
    return push_learning_checkpoint(combined, full_sync=full_sync, force=force)
def flush_replay_session_git_sync(
    final_nav: float = 0.0,
    return_pct: float = 0.0,
    report_path: str = "",
) -> bool:
    """Single end-of-replay push (after evolution). Replaces per-event triple-repo spam."""
    n = len(_checkpoint_batched_reasons)
    if n:
        log.info(f"📤 Replay session end — 1 git sync ({n} deferred event(s) batched)")
    with _checkpoint_lock:
        _checkpoint_batched_reasons.clear()
    S._last_push_ts = 0
    S._last_checkpoint_ts = 0
    return push_full_shutdown_sync(final_nav, return_pct, report_path)
def push_trade(ticker: str, action: str, price: float, qty: float):
    """Push after a trade event."""
    return push_change(
        f"trade: {action} {qty:.0f}x {ticker} @ ${price:.2f}",
        files=["performance.csv", "live_metrics.json", "audit_trail.jsonl"],
        category="trade",
    )
def push_training(ticker: str, timesteps: int, return_pct: float):
    """Push after training completion — to HANOON and Grandmaster."""
    ha_ok = push_change(
        f"train: {ticker} {timesteps} steps return={return_pct:+.1f}%",
        files=[f"models/ppo_trader_warmup_*.zip", "training_journal.json", "audit_trail.jsonl"],
        category="training",
    )
    gm_ok = push_weights_to_repo(
        ["ppo_trader.zip", "models/transformer_model.pth", "models/lstm_model.h5",
         "models/fusion_state.json", "models/model_accuracy.json"],
        repo_url=_get_repo_url("grandmaster"),
        message=f"train: {ticker} {timesteps} steps return={return_pct:+.1f}%",
    ) if _get_repo_url("grandmaster") else False
    return ha_ok or gm_ok
def push_daily_summary(nav: float, equity: float):
    """Push after daily summary."""
    return push_change(
        f"daily: NAV=${nav:,.0f} equity=${equity:,.0f}",
        files=["performance.csv", "live_metrics.json", "audit_trail.jsonl"],
        category="daily",
    )
def push_model_update(model_path: str = "ppo_trader.zip"):
    """Push after model update (online fine-tune) — to HANOON and Grandmaster."""
    ha_nun_ok = push_change(
        f"model: updated {os.path.basename(model_path)}",
        files=[model_path, "audit_trail.jsonl"],
        category="model",
    )
    gm_ok = push_weights_to_repo(
        [model_path],
        repo_url=_get_repo_url("grandmaster"),
        message=f"model: checkpoint {os.path.basename(model_path)}",
    ) if _get_repo_url("grandmaster") else False
    return ha_nun_ok or gm_ok
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
        files=["HANOON.log", "audit_trail.jsonl", "bot_state.json"],
        category="error",
    )
def push_startup(mode: str, ticker: str):
    """Push on bot startup (force push)."""
    return push_change(
        f"startup: mode={mode} ticker={ticker}",
        files=["HANOON.log", "audit_trail.jsonl"],
        category="startup",
    )
def push_shutdown(final_nav: float, return_pct: float):
    """Push on bot shutdown."""
    return push_change(
        f"shutdown: NAV=${final_nav:,.0f} return={return_pct:+.1f}%",
        files=["performance.csv", "live_metrics.json", "bot_state.json", "audit_trail.jsonl"],
        category="shutdown",
    )
def push_full_shutdown_sync(final_nav: float, return_pct: float, report_path: str = "") -> bool:
    """
    On bot close: push ALL artifacts to HANOON, Logs, and Grandmaster repos.
    Blocks until complete (synchronous).
    """
    S._last_push_ts = 0  # bypass debounce for shutdown

    tag = f"shutdown_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    brain = _brain_snapshot_line()
    shutdown_title = (
        f"shutdown: NAV=${final_nav:,.0f} return={return_pct:+.1f}% | {tag}"
    )
    if brain:
        shutdown_title += f" | {brain}"
    log.info("📤 Full shutdown sync → HANOON + Logs + Grandmaster...")

    # ── HANOON (code + bot state) ──
    hanoon_files = [
        "performance.csv", "live_metrics.json", "bot_state.json", "audit_trail.jsonl",
        "models/scalper_weights.json", "models/pilot_experience.json",
        "models/flight_log.jsonl", "models/pattern_memory_bank.json",
        "models/consciousness.json", "models/thought_journal.jsonl",
        "models/ai_decision_log.jsonl", "models/trade_journal.json",
        "models/experience_buffer.jsonl", "models/cognitive_state.json",
        "models/profit_hunt_ledger.jsonl",
        "models/account_snapshots.jsonl", "models/account_evaluation_log.jsonl",
        "ppo_trader.zip",
    ]
    if report_path and os.path.exists(report_path):
        hanoon_files.append(report_path)

    ok_ha = push_change(
        shutdown_title,
        files=hanoon_files,
        category="shutdown",
    )

    import glob as glob_mod
    log_paths = []
    for f in ["HANOON.log", "logs/HANOON.log", "training_journal.json",
              "models/ai_decision_log.jsonl", "models/thought_journal.jsonl", "audit_trail.jsonl",
              "logs/git_sync_journal.jsonl", "logs/git_session_summary.txt"]:
        if os.path.exists(os.path.join(REPO_DIR, f)):
            log_paths.append(f)
    log_paths.extend(glob_mod.glob(os.path.join(REPO_DIR, "models/daily_reports/*.json")))
    log_paths = [os.path.relpath(p, REPO_DIR) for p in log_paths]
    if report_path and os.path.exists(report_path):
        log_paths.append(report_path if not os.path.isabs(report_path) else os.path.relpath(report_path, REPO_DIR))

    ok_logs = push_to_secondary_repo(
        "logs", log_paths,
        f"session close {tag} | NAV=${final_nav:,.0f}",
        "shutdown",
    ) if _get_repo_url("logs") and log_paths else False

    # ── Grandmaster (models + training) ──
    gm_files = [
        "ppo_trader.zip", "models/ppo_trader.zip",
        "models/transformer_model.pth", "models/lstm_model.h5",
        "models/fusion_state.json", "models/model_accuracy.json",
        "models/scalper_weights.json", "models/pilot_experience.json",
    ]
    ok_gm = push_weights_to_repo(
        [f for f in gm_files if os.path.exists(os.path.join(REPO_DIR, f))],
        repo_url=_get_repo_url("grandmaster"),
        message=f"shutdown checkpoint {tag} | return={return_pct:+.1f}%",
    ) if _get_repo_url("grandmaster") else False

    try:
        sync_all_learning_artifacts(release_tag=tag)
    except Exception as exc:
        log.debug(f"Learning artifact sync: {exc}")

    log.info(f"📤 Shutdown sync: HANOON={'✓' if ok_ha else '✗'} Logs={'✓' if ok_logs else '✗'} Grandmaster={'✓' if ok_gm else '✗'}")
    try:
        from core.hanoon_clean_publish import schedule_clean_repo_publish
        schedule_clean_repo_publish(cfg_bot, trigger="shutdown", force=True)
    except Exception as exc:
        log.debug(f"Clean algo repo publish: {exc}")
    try:
        flush_git_telegram_summary(cfg_bot)
    except Exception:
        pass
    return ok_ha or ok_logs or ok_gm
def get_stats() -> dict:
    """Get git sync statistics."""
    return {
        "enabled": S._enabled,
        "total_pushes": S._push_count,
        "failed_pushes": S._failed_pushes,
        "last_push_ts": S._last_push_ts,
        "last_push_age_sec": time.time() - S._last_push_ts if S._last_push_ts else None,
        "pending_queue": len(_pending_pushes),
        "tracked_files": len(TRACKED_FILES),
        "repo": S._repo,
    }
def push_model_release(version: str, model_path: str = "ppo_trader.zip", notes: str = ""):
    """
    Create a git tag/release after training completion.
    Tags every model version so we can roll back and track progress.
    """
    if _should_defer_git_push("release"):
        log.debug(f"Model release deferred until shutdown: v{version}")
        return True
    try:
        now = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        tag_name = f"v{version}_{now}"

        ensure_github_cli(force_auth=True)

        subprocess.run(
            ["git", "tag", "-a", tag_name, "-m", f"HANOON model release {version} | {notes}"],
            cwd=REPO_DIR,
            capture_output=True,
        )

        push_change(
            f"release: model v{version} tagged as {tag_name}",
            files=[model_path, "models/scalper_weights.json", "models/training_history.json",
                   "models/experience_buffer.jsonl", "models/pilot_experience.json"],
            category="release",
        )

        # Push tags and create GitHub release for large artifacts
        try:
            subprocess.run(["git", "push", "origin", tag_name], cwd=REPO_DIR,
                           capture_output=True, timeout=120)
        except Exception:
            subprocess.run(["git", "push", "--tags"], cwd=REPO_DIR, capture_output=True, timeout=120)

        repo_root = REPO_DIR
        if S._token and S._repo and _gh_cli_available():
            _run_gh(
                ["release", "create", tag_name,
                 "--title", f"HANOON {tag_name}",
                 "--notes", notes or f"Model release {version}"],
                cwd=repo_root,
            )
        if os.path.exists(os.path.join(REPO_DIR, model_path)):
            push_large_file_to_release(model_path, tag_name, notes)

        log.info(f"🏷 Git release tagged: {tag_name}")
        try:
            from core.hanoon_clean_publish import schedule_clean_repo_publish
            schedule_clean_repo_publish(cfg_bot, trigger="model_release", force=True)
        except Exception as exc:
            log.debug(f"Clean algo repo publish: {exc}")
        try:
            from core.telegram_broadcast import notify_model_release
            if cfg_bot is not None:
                notify_model_release(cfg_bot, str(version), tag_name, notes)
        except Exception:
            pass
        return True
    except Exception as exc:
        log.warning(f"Git release failed: {exc}")
        return False
def push_large_file_to_release(file_path: str, release_tag: str, description: str = "") -> bool:
    """
    Upload large files (model weights >10MB) to a GitHub release using gh CLI.
    """
    ensure_github_cli(force_auth=True)
    if not _gh_cli_available():
        return False
    try:
        full_path = os.path.join(REPO_DIR, file_path)
        if not os.path.exists(full_path):
            log.debug(f"Large file not found: {file_path}")
            return False

        file_size_mb = os.path.getsize(full_path) / (1024 * 1024)
        if file_size_mb < 10:
            log.debug(f"File {file_path} is only {file_size_mb:.1f}MB — use normal git push")
            return True

        gh_args = ["release", "upload", release_tag, file_path, "-n", description or file_path]
        if S._repo:
            gh_args.extend(["--repo", S._repo])

        if _run_gh(gh_args, cwd=REPO_DIR, timeout=300):
            log.info(f"📦 Large file uploaded to release {release_tag}: {file_path} ({file_size_mb:.1f}MB)")
            return True
        return False

    except Exception as exc:
        log.debug(f"Large file release upload skipped: {exc}")
        return False
def sync_all_learning_artifacts(release_tag: str = None) -> bool:
    """
    Sync all AI learning artifacts to GitHub, using releases for large files.
    This is called after major training sessions.
    
    Large files (model weights) go to releases, small files go to git.
    Everything is linked and version-tracked.
    """
    if _should_defer_git_push("training"):
        log.debug(f"Full learning sync deferred until shutdown: {release_tag or 'auto'}")
        return True
    if not release_tag:
        release_tag = f"training_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    ensure_github_cli(force_auth=True)
    
    import glob as glob_mod
    large_patterns = [
        "models/transformer_model.pth",
        "models/lstm_model.h5",
        "ppo_trader.zip",
        "models/ppo_trader.zip",
    ]
    large_files = []
    for pattern in large_patterns:
        if "*" in pattern:
            large_files.extend(glob_mod.glob(os.path.join(REPO_DIR, pattern)))
        else:
            p = os.path.join(REPO_DIR, pattern)
            if os.path.exists(p):
                large_files.append(pattern)

    # Ensure release exists before uploading large files (requires gh CLI)
    if S._enabled and S._token and S._repo and _gh_cli_available():
        _run_gh(
            ["release", "create", release_tag,
             "--title", f"HANOON training {release_tag}",
             "--notes", f"Training sync {datetime.utcnow().isoformat()}"],
            cwd=REPO_DIR,
        )

    if _gh_cli_available():
        for lf in large_files:
            rel = lf if not lf.startswith(REPO_DIR) else os.path.relpath(lf, REPO_DIR)
            push_large_file_to_release(rel, release_tag, f"Training model weights - {datetime.utcnow().isoformat()}")

    small_files = [
        "models/pilot_experience.json",
        "models/flight_log.jsonl",
        "models/pattern_memory_bank.json",
        "models/pattern_snapshots.jsonl",
        "models/scalper_weights.json",
        "models/ai_guidelines.txt",
        "models/parameter_adjustments.json",
        "models/improvement_history.json",
        "models/trained_record_hashes.jsonl",
        "models/consciousness.json",
        "models/cognitive_state.json",
        "models/thought_journal.jsonl",
        "models/trade_journal.json",
        "models/experience_buffer.jsonl",
        "models/ai_decision_log.jsonl",
        "models/account_snapshots.jsonl",
        "models/account_evaluation_log.jsonl",
    ]

    push_change(
        f"training: all learning artifacts synced | {release_tag}",
        files=small_files,
        category="training"
    )
    
    if S._enabled:
        try:
            subprocess.run(["git", "tag", "-a", release_tag, "-m", f"Training sync {release_tag}"], 
                        cwd=REPO_DIR, capture_output=True)
            subprocess.run(["git", "push", "origin", release_tag], cwd=REPO_DIR, capture_output=True, timeout=120)
            log.info(f"🏷 Release tag: {release_tag}")
        except Exception:
            pass

    return True
