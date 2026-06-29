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
PENDING_RETRAIN_PATH = Path("models/halim_retrain_pending.json")
MERGED_CKPT = Path("halim/data/checkpoints/toddler_v1/merged/model.safetensors")

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


def _uses_hf_merged(root: Path) -> bool:
    if os.getenv("HALIM_LM_BACKEND", "hf").lower() != "hf":
        return False
    merged = root / MERGED_CKPT
    return merged.is_file()


def _queue_pending_retrain(export_result: Dict[str, Any], trigger: str) -> None:
    state = _load_state()
    pending = dict(state.get("pending_retrain") or {})
    pending["total_gold"] = max(
        int(pending.get("total_gold") or 0),
        int(export_result.get("total_gold") or 0),
    )
    pending["added"] = int(pending.get("added") or 0) + int(export_result.get("added") or 0)
    pending["trigger"] = trigger
    pending["queued_at"] = datetime.now(timezone.utc).isoformat()
    state["pending_retrain"] = pending
    _save_state(state)
    try:
        PENDING_RETRAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        PENDING_RETRAIN_PATH.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    except Exception:
        pass
    log.info(
        f"🧠 Halim auto-retrain deferred (learn loop active) — "
        f"+{pending['added']} queued, total gold {pending['total_gold']}"
    )


def flush_pending_retrain(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Run deferred retrain after learn loop stops (legacy — prefer finalize_learn_session)."""
    return finalize_learn_session(cfg)


def finalize_learn_session(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """
    End of learn session: export gold and overwrite halim_sft.zip — one Colab upload file.
    Same path every time; rm + rebuild, never separate timestamped zips.
    """
    cfg = cfg or BotConfig()
    if os.getenv("HALIM_LEARN_PACKAGE_ON_STOP", "true").lower() not in ("1", "true", "yes"):
        return {"ok": False, "reason": "HALIM_LEARN_PACKAGE_ON_STOP_disabled"}

    root = _repo_root()
    result: Dict[str, Any] = {"ok": False, "zip": str(root / "halim_sft.zip")}

    try:
        from core.halim_gold_pipeline import export_halim_gold
        prev_retrain = os.environ.get("HALIM_AUTO_LM_RETRAIN")
        os.environ["HALIM_AUTO_LM_RETRAIN"] = "false"
        try:
            export_result = export_halim_gold(include_learn_cache=True)
        finally:
            if prev_retrain is None:
                os.environ.pop("HALIM_AUTO_LM_RETRAIN", None)
            else:
                os.environ["HALIM_AUTO_LM_RETRAIN"] = prev_retrain
        result["export"] = export_result
        action = export_result.get("action_gold") or {}
        result["total_gold"] = action.get("total_gold", 0)
    except Exception as exc:
        result["error"] = str(exc)[:120]
        return result

    state = _load_state()
    state.pop("pending_retrain", None)
    _save_state(state)
    try:
        PENDING_RETRAIN_PATH.unlink(missing_ok=True)
    except Exception:
        pass

    py = sys.executable
    env = os.environ.copy()
    env.setdefault("HALIM_REPO_ROOT", str(root))
    env["PYTHONPATH"] = f"{root / 'halim'}{os.pathsep}{root}{os.pathsep}{env.get('PYTHONPATH', '')}"
    min_sft = os.getenv("HALIM_AUTO_LM_MIN_SFT_PAIRS", "400")
    env["HALIM_TODDLER_MIN_PAIRS"] = min_sft

    try:
        proc = subprocess.run(
            [py, str(root / "halim/scripts/prepare_sft.py"), "--min-pairs", min_sft],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        parsed = _parse_json_stdout(proc.stdout)
        result["prepare_sft"] = parsed or {"ok": proc.returncode == 0}
        if proc.returncode != 0 or not parsed.get("ok", True):
            log.warning(f"Halim learn finalize — prepare_sft failed: {result['prepare_sft']}")
            return result
    except Exception as exc:
        result["prepare_sft"] = {"ok": False, "error": str(exc)[:120]}
        return result

    pkg = _package_colab_sft(root, env)
    result["colab_package"] = pkg
    result["ok"] = bool(pkg.get("ok"))
    zip_path = root / "halim_sft.zip"

    if result["ok"] and zip_path.is_file():
        meta = {
            "file": "halim_sft.zip",
            "path": str(zip_path.resolve()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pairs_total": (result.get("prepare_sft") or {}).get("pairs_total"),
            "total_gold": export_result.get("total_gold"),
        }
        meta_path = root / "models/halim_sft_package.meta.json"
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception:
            pass
        state["last_sft_gold_total"] = int(export_result.get("total_gold") or 0)
        state["last_colab_package_at"] = meta["updated_at"]
        _save_state(state)
        pairs = meta.get("pairs_total", "?")
        log.info(f"📦 halim_sft.zip updated — {pairs} SFT pairs (single Colab upload file)")
        print(f"\n📦 Updated: {zip_path}", flush=True)
        print("   Same file every time — upload halim_sft.zip to Colab to train.", flush=True)

    return result


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

    last_total = int(
        state.get("last_train_gold_total")
        or state.get("last_sft_gold_total")
        or 0
    )
    new_pairs = int(export_result.get("added") or 0)
    delta = total - last_total
    min_new = int(os.getenv("HALIM_AUTO_LM_MIN_NEW_PAIRS", "150"))
    if not force and delta < min_new and new_pairs < min_new:
        return False, f"delta_{delta}_lt_{min_new}"

    try:
        from core.halim_learn_browse import is_learn_loop_active
        if not force and is_learn_loop_active():
            return False, "learn_loop_active"
    except Exception:
        pass

    global _running
    if _running:
        return False, "already_running"

    return True, "ok"


def _parse_json_stdout(stdout: str) -> Dict[str, Any]:
    stdout = (stdout or "").strip()
    if not stdout:
        return {}
    try:
        return json.loads(stdout)
    except Exception:
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _stop_serve_for_train(root: Path) -> Dict[str, Any]:
    if os.getenv("HALIM_AUTO_LM_STOP_SERVE", "true").lower() not in ("1", "true", "yes"):
        return {"ok": True, "skipped": True}
    try:
        proc = subprocess.run(
            [str(root / "scripts/halim_stop.sh"), "--serve-only"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"ok": proc.returncode == 0, "code": proc.returncode}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _package_colab_sft(root: Path, env: Dict[str, str]) -> Dict[str, Any]:
    script = root / "scripts/halim_package_colab.sh"
    if not script.is_file():
        return {"ok": False, "reason": "missing_package_script"}
    try:
        env = dict(env)
        env["HALIM_SKIP_PREPARE"] = "true"
        proc = subprocess.run(
            [str(script)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        zip_path = root / "halim_sft.zip"
        ok = proc.returncode == 0 and zip_path.is_file()
        return {
            "ok": ok,
            "code": proc.returncode,
            "zip": str(zip_path) if zip_path.is_file() else None,
            "stdout_tail": (proc.stdout or "")[-400:],
            "stderr_tail": (proc.stderr or "")[-400:],
            "next": "Upload halim_sft.zip to Google Colab (halim/colab/COLAB_GUIDE.md)",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


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

    min_sft = os.getenv("HALIM_AUTO_LM_MIN_SFT_PAIRS", "400")
    env["HALIM_TODDLER_MIN_PAIRS"] = min_sft
    hf_colab = _uses_hf_merged(root)
    result["train_mode"] = "colab_package" if hf_colab else "mlx_lora"

    # Free RAM on 8GB Mac before any heavy step
    result["steps"]["stop_serve"] = _stop_serve_for_train(root)
    time.sleep(2)
    try:
        proc = subprocess.run(
            [py, str(root / "halim/scripts/prepare_sft.py"), "--min-pairs", min_sft],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        parsed = _parse_json_stdout(proc.stdout)
        if proc.returncode == 0 and parsed.get("ok", True):
            result["steps"]["prepare_sft"] = parsed or {"ok": True}
        else:
            result["steps"]["prepare_sft"] = {
                "ok": False,
                "code": proc.returncode,
                "stderr": (proc.stderr or "")[:300],
                "parsed": parsed,
            }
            return result
    except Exception as exc:
        result["steps"]["prepare_sft"] = {"ok": False, "error": str(exc)[:120]}
        return result

    # 2. Train — HF Colab zip (your Mac uses merged HF weights) or local MLX LoRA
    if hf_colab:
        pkg = _package_colab_sft(root, env)
        result["steps"]["colab_package"] = pkg
        result["ok"] = bool(pkg.get("ok"))
        if result["ok"]:
            log.info(
                "🧠 Halim SFT ready for Colab — upload halim_sft.zip "
                "(see halim/colab/COLAB_GUIDE.md)"
            )
        if os.getenv("HALIM_AUTO_LM_RESTART_SERVE", "true").lower() in ("1", "true", "yes"):
            try:
                start = subprocess.run(
                    [str(root / "scripts/ensure_halim_active.sh"), "--serve-only"],
                    cwd=str(root),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                result["steps"]["serve_restart"] = {"ok": start.returncode == 0, "start": start.returncode}
            except Exception as exc:
                result["steps"]["serve_restart"] = {"ok": False, "error": str(exc)[:120]}
        return result

    iters = int(os.getenv("HALIM_AUTO_LM_ITERS", "150"))
    batch_size = int(os.getenv("HALIM_AUTO_LM_BATCH_SIZE", "1"))
    out_name = os.getenv("HALIM_AUTO_LM_OUT_NAME", "toddler_v1_mlx")
    train_cmd = [
        py,
        str(root / "halim/scripts/train_toddler.py"),
        "--out-name",
        out_name,
        "--iters",
        str(iters),
        "--batch-size",
        str(batch_size),
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
        parsed = _parse_json_stdout(proc.stdout)
        if proc.returncode == 0 and parsed.get("ok"):
            result["steps"]["train"] = parsed
        else:
            err_tail = (proc.stderr or "")[-2000:]
            out_tail = (proc.stdout or "")[-800:]
            result["steps"]["train"] = {
                "ok": False,
                "code": proc.returncode,
                "stderr": err_tail,
                "stdout_tail": out_tail,
                "parsed": parsed,
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
                [str(root / "scripts/halim_stop.sh"), "--serve-only"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            start = subprocess.run(
                [str(root / "scripts/ensure_halim_active.sh"), "--serve-only", "--restart"],
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
            gold_total = int(export_result.get("total_gold") or 0)
            if outcome.get("train_mode") == "colab_package":
                state["last_sft_gold_total"] = gold_total
            else:
                state["last_train_gold_total"] = gold_total
            state["last_success_at"] = state["last_train_finished_at"]
            mode = outcome.get("train_mode", "train")
            log.info(f"🧠 Halim auto-LM retrain done ({mode}) — gold total {gold_total}")
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
        if reason == "learn_loop_active":
            _queue_pending_retrain(export_result, trigger)
            return {"scheduled": False, "reason": reason, "queued": True}
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
