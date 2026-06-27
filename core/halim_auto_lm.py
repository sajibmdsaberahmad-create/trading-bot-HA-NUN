#!/usr/bin/env python3
"""
core/halim_auto_lm.py — Auto export → SFT → MLX LoRA retrain when gold grows.

Runs off-hours (or when forced), never blocks trading. Restarts serve optionally
so the new adapter loads without manual steps.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig
from core.notify import log

STATE_PATH = Path("models/halim_lm_evolve_state.json")
JOURNAL_PATH = Path("models/halim_lm_evolve.jsonl")

_lock = threading.Lock()
_running = False


def auto_lm_enabled() -> bool:
    return os.getenv("HALIM_AUTO_LM_RETRAIN", "true").lower() in ("1", "true", "yes")


def _load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.is_file():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _journal(row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _off_hours_ok(cfg: BotConfig) -> bool:
    if os.getenv("HALIM_AUTO_LM_OFF_HOURS_ONLY", "true").lower() not in ("1", "true", "yes"):
        return True
    try:
        from core.market_hours import can_trade_now
        can_trade, _ = can_trade_now(cfg)
        return not can_trade
    except Exception:
        return True


def _cooldown_ok(state: Dict[str, Any]) -> bool:
    last = state.get("last_train_started_at")
    if not last:
        return True
    cooldown = float(os.getenv("HALIM_AUTO_LM_COOLDOWN_SEC", "21600"))
    try:
        started = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return elapsed >= cooldown
    except Exception:
        return True


def should_auto_retrain(
    export_result: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
    *,
    force: bool = False,
) -> Tuple[bool, str]:
    if not auto_lm_enabled() and not force:
        return False, "disabled"
    cfg = cfg or BotConfig()
    if not force and not _off_hours_ok(cfg):
        return False, "market_open"

    state = _load_state()
    if not force and not _cooldown_ok(state):
        return False, "cooldown"

    total = int(export_result.get("total_gold") or 0)
    min_total = int(os.getenv("HALIM_AUTO_LM_MIN_TOTAL_PAIRS", "400"))
    if total < min_total and not force:
        return False, f"total_gold_{total}_lt_{min_total}"

    last_total = int(state.get("last_train_gold_total") or 0)
    new_pairs = int(export_result.get("added") or 0)
    delta = total - last_total
    min_new = int(os.getenv("HALIM_AUTO_LM_MIN_NEW_PAIRS", "150"))
    if not force and delta < min_new and new_pairs < min_new:
        return False, f"delta_{delta}_lt_{min_new}"

    global _running
    if _running:
        return False, "already_running"

    return True, "ok"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_train_pipeline(cfg: BotConfig, *, trigger: str) -> Dict[str, Any]:
    root = _repo_root()
    state = _load_state()
    state["last_train_started_at"] = datetime.now(timezone.utc).isoformat()
    state["last_trigger"] = trigger
    _save_state(state)

    result: Dict[str, Any] = {"trigger": trigger, "steps": {}}
    py = sys.executable
    env = os.environ.copy()
    env.setdefault("HALIM_REPO_ROOT", str(root))
    env["PYTHONPATH"] = f"{root / 'halim'}{os.pathsep}{root}{os.pathsep}{env.get('PYTHONPATH', '')}"

    # 1. Prepare SFT
    try:
        proc = subprocess.run(
            [py, str(root / "halim/scripts/prepare_sft.py")],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                result["steps"]["prepare_sft"] = json.loads(proc.stdout.strip().split("\n")[-1])
            except Exception:
                result["steps"]["prepare_sft"] = {"ok": True}
        else:
            result["steps"]["prepare_sft"] = {
                "ok": False,
                "code": proc.returncode,
                "stderr": (proc.stderr or "")[:300],
            }
            return result
    except Exception as exc:
        result["steps"]["prepare_sft"] = {"ok": False, "error": str(exc)[:120]}
        return result

    # 2. MLX LoRA (short incremental iters)
    iters = int(os.getenv("HALIM_AUTO_LM_ITERS", "150"))
    out_name = os.getenv("HALIM_AUTO_LM_OUT_NAME", "toddler_v1")
    train_cmd = [
        py,
        str(root / "halim/scripts/train_toddler.py"),
        "--out-name",
        out_name,
        "--iters",
        str(iters),
        "--force",
    ]
    try:
        proc = subprocess.run(
            train_cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("HALIM_AUTO_LM_TRAIN_TIMEOUT_SEC", "7200")),
        )
        tail = (proc.stdout or "").strip()
        if proc.returncode == 0 and tail:
            try:
                result["steps"]["train"] = json.loads(tail.split("\n")[-1] if "\n" in tail else tail)
            except Exception:
                result["steps"]["train"] = {"ok": True, "raw": tail[:200]}
        else:
            result["steps"]["train"] = {
                "ok": False,
                "code": proc.returncode,
                "stderr": (proc.stderr or "")[:400],
            }
            return result
    except subprocess.TimeoutExpired:
        result["steps"]["train"] = {"ok": False, "error": "train_timeout"}
        return result
    except Exception as exc:
        result["steps"]["train"] = {"ok": False, "error": str(exc)[:120]}
        return result

    # 3. Register checkpoint
    backend = os.getenv("HALIM_LM_BACKEND", "mlx")
    try:
        proc = subprocess.run(
            [
                py,
                str(root / "halim/scripts/register_checkpoint.py"),
                out_name,
                "--backend",
                backend,
            ],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result["steps"]["register"] = json.loads(proc.stdout.strip())
        else:
            result["steps"]["register"] = {"ok": False, "stderr": (proc.stderr or "")[:200]}
    except Exception as exc:
        result["steps"]["register"] = {"ok": False, "error": str(exc)[:120]}

    # 4. Clear in-process MLX cache (same process only)
    try:
        from halim.inference_backend import _model_cache
        _model_cache.clear()
    except Exception:
        pass

    # 5. Restart serve so new adapter loads
    if os.getenv("HALIM_AUTO_LM_RESTART_SERVE", "true").lower() in ("1", "true", "yes"):
        try:
            stop = subprocess.run(
                [str(root / "scripts/halim_stop.sh")],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            start = subprocess.run(
                [str(root / "scripts/ensure_halim_active.sh"), "--serve-only"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
            result["steps"]["serve_restart"] = {
                "ok": start.returncode == 0,
                "stop": stop.returncode,
                "start": start.returncode,
            }
        except Exception as exc:
            result["steps"]["serve_restart"] = {"ok": False, "error": str(exc)[:120]}

    train_ok = (result["steps"].get("train") or {}).get("ok", False)
    result["ok"] = train_ok
    return result


def _train_worker(cfg: BotConfig, export_result: Dict[str, Any], trigger: str) -> None:
    global _running
    try:
        log.info(f"🧠 Halim auto-LM retrain starting ({trigger})…")
        outcome = _run_train_pipeline(cfg, trigger=trigger)
        state = _load_state()
        state["last_train_finished_at"] = datetime.now(timezone.utc).isoformat()
        state["last_outcome"] = outcome
        if outcome.get("ok"):
            state["last_train_gold_total"] = int(export_result.get("total_gold") or 0)
            state["last_success_at"] = state["last_train_finished_at"]
            log.info(f"🧠 Halim auto-LM retrain done — gold total {state['last_train_gold_total']}")
        else:
            log.warning(f"Halim auto-LM retrain failed: {outcome.get('steps')}")
        _save_state(state)
        _journal({"event": "auto_lm_retrain", "trigger": trigger, **outcome})
    except Exception as exc:
        log.warning(f"Halim auto-LM worker: {exc}")
        _journal({"event": "auto_lm_retrain", "ok": False, "error": str(exc)[:120]})
    finally:
        with _lock:
            _running = False


def schedule_auto_retrain(
    export_result: Dict[str, Any],
    cfg: Optional[BotConfig] = None,
    *,
    trigger: str = "export",
    force: bool = False,
) -> Dict[str, Any]:
    """
    Non-blocking: spawn background retrain if gold thresholds met.
    Returns {scheduled, reason, ...}.
    """
    global _running
    cfg = cfg or BotConfig()
    ok, reason = should_auto_retrain(export_result, cfg, force=force)
    if not ok:
        return {"scheduled": False, "reason": reason}

    with _lock:
        if _running:
            return {"scheduled": False, "reason": "already_running"}
        _running = True

    t = threading.Thread(
        target=_train_worker,
        args=(cfg, export_result, trigger),
        name="halim-auto-lm",
        daemon=True,
    )
    t.start()
    return {"scheduled": True, "reason": "ok", "trigger": trigger}


def run_auto_retrain_sync(
    cfg: Optional[BotConfig] = None,
    *,
    trigger: str = "manual",
    force: bool = True,
) -> Dict[str, Any]:
    """Blocking train — for scripts."""
    cfg = cfg or BotConfig()
    from core.halim_action_learn import export_action_gold
    export_result = export_action_gold(include_learn_cache=True)
    ok, reason = should_auto_retrain(export_result, cfg, force=force)
    if not ok:
        return {"ok": False, "reason": reason, "export": export_result}
    return _run_train_pipeline(cfg, trigger=trigger)
