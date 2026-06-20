#!/usr/bin/env python3
"""
core/journal.py — Training registry and model versioning ledger.
Keeps an automatic journal of all model training/fine-tuning sessions
and saves versioned backup files in `models/` to prevent lost progress.
"""

import os
import json
import shutil
import datetime
import subprocess
from typing import Dict, Any, Optional
from core.config import BotConfig
from core.notify import log


def get_git_commit_hash() -> str:
    """Get current git commit hash if in a git repository."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        return res.stdout.strip()
    except Exception:
        return "not_a_git_repository"


def git_commit_journal(journal_path: str, message: str):
    """Optionally stage and commit the training journal to git."""
    try:
        # Check if git is initialized
        subprocess.run(["git", "status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        # Stage the journal file
        subprocess.run(["git", "add", journal_path], check=True)
        # Commit
        subprocess.run(["git", "commit", "-m", message], check=True)
        log.info(f"Journal: Auto-committed journal updates to git: {message}")
    except Exception as exc:
        log.debug(f"Journal: Git auto-commit skipped or failed: {exc}")


def record_training_session(
    cfg: BotConfig,
    event: str,
    metrics: Dict[str, Any],
    source_model_path: str
) -> Optional[str]:
    """
    Save a timestamped backup of the model and write an entry in training_journal.json.
    Returns the path of the versioned backup model or None.
    """
    try:
        # 1. Create models directory if it doesn't exist
        models_dir = "models"
        os.makedirs(models_dir, exist_ok=True)

        # 2. Formulate versioned model path
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        versioned_filename = f"ppo_trader_{event.lower()}_{timestamp}.zip"
        versioned_path = os.path.join(models_dir, versioned_filename)

        # 3. Copy the model to versioned backup if it exists
        if os.path.exists(source_model_path):
            shutil.copy2(source_model_path, versioned_path)
            log.info(f"Journal: Backup versioned model saved -> {versioned_path}")
        else:
            log.warning(f"Journal: Source model {source_model_path} not found to backup.")
            versioned_path = None

        # 4. Compile entry details
        commit_hash = get_git_commit_hash()
        journal_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "event": event,
            "ticker": cfg.TICKER,
            "sizing_mode": getattr(cfg, "SIZING_MODE", "risk_based"),
            "initial_cash": cfg.INITIAL_CASH,
            "git_commit": commit_hash,
            "versioned_model_path": versioned_path,
            "hyperparameters": {
                "PPO_LR": cfg.PPO_LR,
                "PPO_ENT_COEF": cfg.PPO_ENT_COEF,
                "PPO_N_STEPS": cfg.PPO_N_STEPS,
                "PPO_BATCH_SIZE": cfg.PPO_BATCH_SIZE,
                "PPO_CLIP_RANGE": cfg.PPO_CLIP_RANGE,
                "WINDOW_SIZE": cfg.WINDOW_SIZE,
            },
            "metrics": metrics
        }

        # 5. Load and append to journal file
        journal_path = "training_journal.json"
        journal_data = []
        if os.path.exists(journal_path):
            try:
                with open(journal_path, "r") as f:
                    journal_data = json.load(f)
            except Exception as e:
                log.warning(f"Journal: Failed to read existing journal file: {e}. Re-creating.")

        journal_data.append(journal_entry)

        with open(journal_path, "w") as f:
            json.dump(journal_data, f, indent=2)

        log.info(f"Journal: Training entry recorded in {journal_path}")

        # 6. Auto-commit the journal file to Git
        git_commit_journal(journal_path, f"journal: auto-record {event} session for {cfg.TICKER} at {timestamp}")

        return versioned_path

    except Exception as exc:
        log.error(f"Journal: Failed to record training session: {exc}")
        return None
