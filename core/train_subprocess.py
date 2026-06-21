#!/usr/bin/env python3
"""
core/train_subprocess.py — Isolated subprocess launcher for model training.

PURPOSE
═══════════════════════════════════════════════════════════════════════════
Training (especially deep learning) fragments GPU/MPS memory and can
leak resources over time. By spawning a SHORT-LIVED child process that
exits after training completes, we guarantee:
  - All GPU/MPS memory is freed back to the OS
  - No TensorFlow/PyTorch state lingers in the trading process
  - Crashes in training cannot kill the live trading loop
  - Multiple training runs don't accumulate memory fragmentation

USAGE from scalper runner:
    from core.train_subprocess import launch_training
    
    # Fire-and-forget off-hours training
    launch_training([
        "python", "-m", "core.advanced_training",
        "--mode", "full",
        "--ticker", self.cfg.TICKER,
        "--save-model", "models/transformer_model.pth"
    ])
    
    # The trading loop continues immediately. Training runs in background.
    
USAGE standalone:
    python -m core.train_subprocess --mode full --ticker SPY --timesteps 500000
"""

import os
import sys
import json
import time
import signal
import subprocess
import tempfile
from typing import Optional, List
from datetime import datetime
from pathlib import Path

from core.notify import log


# ═════════════════════════════════════════════════════════════════════════════
# TRAINING LAUNCHER
# ═════════════════════════════════════════════════════════════════════════════

def launch_training(cmd: List[str], 
                    timeout_minutes: int = 60,
                    output_file: Optional[str] = None) -> Optional[str]:
    """
    Launch a training subprocess and return immediately.
    
    Args:
        cmd: Command list (e.g. ["python", "-m", "core.advanced_training", ...])
        timeout_minutes: Max training runtime before SIGTERM
        output_file: Optional file to capture stdout/stderr
        
    Returns:
        Training session ID, or None on submission failure
    """
    session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    
    try:
        # Prepare output capture
        if output_file is None:
            output_file = f"models/daily_reports/train_{session_id}.log"
        
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        with open(output_file, 'w') as f:
            f.write(f"Training session: {session_id}\n")
            f.write(f"Command: {' '.join(cmd)}\n")
            f.write(f"Started: {datetime.utcnow().isoformat()}\n\n")
        
        # Launch subprocess (non-blocking)
        # We use Popen with stdout/stderr redirected to file
        # The child process is completely isolated from the parent
        proc = subprocess.Popen(
            cmd,
            stdout=open(output_file, 'a'),
            stderr=subprocess.STDOUT,
            # Detach from parent process group so it survives parent exit
            start_new_session=True,
            # Ensure we don't inherit any weird file descriptors
            close_fds=True,
        )
        
        log.info(f"🏋️ Training subprocess launched | pid={proc.pid} | session={session_id}")
        
        # Start watchdog timer in a daemon thread (won't block parent exit)
        import threading
        watchdog = threading.Thread(
            target=_watchdog,
            args=(proc, session_id, timeout_minutes * 60, output_file),
            daemon=True,
            name=f"train-watchdog-{session_id}"
        )
        watchdog.start()
        
        return session_id
        
    except Exception as exc:
        log.warning(f"Failed to launch training subprocess: {exc}")
        return None


def _watchdog(proc: subprocess.Popen, session_id: str, timeout: int, output_file: str):
    """
    Background watchdog that kills training if it exceeds timeout.
    Runs in daemon thread, won't prevent parent exit.
    """
    try:
        proc.wait(timeout=timeout)
        rc = proc.returncode
        log.info(f"🏋️ Training {session_id} completed | exit_code={rc}")
        
        # Append completion marker
        try:
            with open(output_file, 'a') as f:
                f.write(f"\nCompleted: {datetime.utcnow().isoformat()}\n")
                f.write(f"Exit code: {rc}\n")
        except Exception:
            pass
        
        # If training produced model weights, trigger async git commit
        if rc == 0:
            from core.async_utils import get_background_worker
            worker = get_background_worker()
            worker.submit_git_commit(
                files=["models/transformer_model.pth", "models/scalper_weights.json"],
                message=f"train: subprocess training {session_id}",
                push=False,
            )
        
    except subprocess.TimeoutExpired:
        log.warning(f"🏋️ Training {session_id} timed out after {timeout}s — terminating")
        try:
            proc.kill()
        except Exception:
            pass
        try:
            with open(output_file, 'a') as f:
                f.write(f"\nTIMEOUT after {timeout}s | Killed at {datetime.utcnow().isoformat()}\n")
        except Exception:
            pass
    
    except Exception as exc:
        log.debug(f"Watchdog for {session_id} error: {exc}")


# ═════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT (called by subprocess)
# ═════════════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point for isolated training runs."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Isolated training subprocess")
    parser.add_argument("--mode", default="full", choices=["full", "ppo", "transformer", "lstm"])
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--timesteps", type=int, default=500000)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--save-model", default="models/transformer_model.pth")
    parser.add_argument("--output", default=None)
    parser.add_argument("--timeout", type=int, default=3600)
    
    args = parser.parse_args()
    
    # Build command for advanced training module
    cmd = [
        sys.executable, "-m", "core.advanced_training",
        "--mode", args.mode,
        "--ticker", args.ticker,
        "--ppo-timesteps", str(args.timesteps),
        "--epochs", str(args.epochs),
        "--save-model", args.save_model,
    ]
    
    log.info(f"Subprocess training starting: mode={args.mode} ticker={args.ticker}")
    
    # Launch via Popen so this script becomes the isolated training runner
    output_file = args.output or f"models/daily_reports/train_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    with open(output_file, 'w') as f:
        f.write(f"Training: {args.mode} | ticker={args.ticker}\n")
        f.write(f"Started: {datetime.utcnow().isoformat()}\n\n")
    
    proc = subprocess.Popen(
        cmd,
        stdout=open(output_file, 'a'),
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    
    try:
        proc.wait(timeout=args.timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        log.warning(f"Training timed out after {args.timeout}s")
        proc.kill()
        rc = -1
    
    with open(output_file, 'a') as f:
        f.write(f"\nCompleted: {datetime.utcnow().isoformat()}\nExit: {rc}\n")
    
    sys.exit(rc)


if __name__ == "__main__":
    main()