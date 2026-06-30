#!/usr/bin/env python3
"""
core/halim_promotion_gate.py — Gate before promoting Halim LoRA checkpoint to latest.

Mirrors promotion_gate.py for PPO: golden commander probes must pass before
halim/data/checkpoints/latest symlink updates.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

STATE_PATH = Path("models/halim_promotion_state.json")
CHECKPOINTS_ROOT = Path("halim/data/checkpoints")


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    return bool(getattr(cfg, "HALIM_PROMOTION_GATE", True))


def _min_token_passed() -> int:
    try:
        return max(1, int(os.getenv("HALIM_PROMOTION_MIN_TOKEN_SCORE", "3")))
    except (TypeError, ValueError):
        return 3


def _min_json_passed() -> int:
    try:
        return max(1, int(os.getenv("HALIM_PROMOTION_MIN_JSON_SCORE", "3")))
    except (TypeError, ValueError):
        return 3


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(row: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(row, indent=2), encoding="utf-8")


def evaluate_halim_checkpoint(
    checkpoint_name: str,
    *,
    use_server: bool = False,
) -> Dict[str, Any]:
    """Run toddler golden probes against a named checkpoint directory."""
    ckpt = CHECKPOINTS_ROOT / checkpoint_name
    result: Dict[str, Any] = {
        "checkpoint": checkpoint_name,
        "path": str(ckpt),
        "pass": False,
        "reasons": [],
    }
    if not ckpt.is_dir():
        result["reasons"].append("checkpoint_not_found")
        return result

    prev = os.environ.get("HALIM_MODEL_PATH")
    os.environ["HALIM_MODEL_PATH"] = str(ckpt)
    try:
        root = Path(__file__).resolve().parents[1]
        cmd = [
            sys.executable,
            str(root / "halim/scripts/eval_toddler.py"),
            "--style",
            "both",
        ]
        if use_server:
            cmd.append("--server")
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=int(os.getenv("HALIM_PROMOTION_EVAL_TIMEOUT_SEC", "600")),
            env={**os.environ, "HALIM_REPO_ROOT": str(root)},
        )
        if proc.returncode not in (0, 1) and not proc.stdout.strip():
            result["reasons"].append(f"eval_failed_rc={proc.returncode}")
            if proc.stderr:
                result["stderr"] = proc.stderr[-400:]
            return result
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result["reasons"].append("eval_output_not_json")
            result["stdout_tail"] = (proc.stdout or "")[-500:]
            return result
    finally:
        if prev is None:
            os.environ.pop("HALIM_MODEL_PATH", None)
        else:
            os.environ["HALIM_MODEL_PATH"] = prev

    token_passed = int(report.get("token_passed", 0) or 0)
    json_passed = int(report.get("json_passed", 0) or 0)
    min_tok = _min_token_passed()
    min_json = _min_json_passed()
    reasons: List[str] = []
    if token_passed < min_tok:
        reasons.append(f"token_score {token_passed} < {min_tok}")
    if json_passed < min_json:
        reasons.append(f"json_score {json_passed} < {min_json}")

    # Compare to incumbent latest if present
    incumbent_score = None
    state = _load_state()
    if state.get("last_promoted_token_passed") is not None:
        incumbent_score = int(state["last_promoted_token_passed"])
        if token_passed < incumbent_score:
            reasons.append(
                f"token_score {token_passed} < incumbent {incumbent_score}"
            )

    result.update({
        "report": report,
        "token_passed": token_passed,
        "json_passed": json_passed,
        "thresholds": {"min_token": min_tok, "min_json": min_json},
        "pass": not reasons,
        "reasons": reasons,
    })
    return result


def try_promote_halim_checkpoint(
    checkpoint_name: str,
    *,
    cfg: Optional[BotConfig] = None,
    force: bool = False,
    use_server: bool = False,
) -> Dict[str, Any]:
    """
    Evaluate checkpoint; update latest symlink only when gate passes (or gate off / force).
    Returns evaluation dict with promoted=True when latest points at checkpoint_name.
    """
    cfg = cfg or BotConfig()
    result: Dict[str, Any] = {"ok": False, "promoted": False, "checkpoint": checkpoint_name}

    ckpt = CHECKPOINTS_ROOT / checkpoint_name
    if not ckpt.is_dir():
        result["reason"] = "checkpoint_not_found"
        return result

    gate_on = _enabled(cfg) and not force
    eval_result = evaluate_halim_checkpoint(checkpoint_name, use_server=use_server)
    result["evaluation"] = eval_result

    if gate_on and not eval_result.get("pass"):
        result["ok"] = True
        result["reason"] = "gate_blocked"
        result["blocked_reasons"] = eval_result.get("reasons", [])
        log.info(
            f"⏸ Halim promotion blocked for {checkpoint_name}: "
            f"{', '.join(result['blocked_reasons'])}"
        )
        _save_state({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "promoted": False,
            "checkpoint": checkpoint_name,
            "evaluation": {
                "token_passed": eval_result.get("token_passed"),
                "json_passed": eval_result.get("json_passed"),
                "reasons": eval_result.get("reasons"),
            },
        })
        return result

    latest = CHECKPOINTS_ROOT / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(checkpoint_name)

    result["ok"] = True
    result["promoted"] = True
    result["reason"] = "force" if force else ("gate_pass" if gate_on else "gate_disabled")
    log.info(
        f"✓ Halim promoted → latest ({checkpoint_name}) "
        f"token={eval_result.get('token_passed')} json={eval_result.get('json_passed')} "
        f"({result['reason']})"
    )
    _save_state({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "promoted": True,
        "checkpoint": checkpoint_name,
        "last_promoted_token_passed": eval_result.get("token_passed"),
        "last_promoted_json_passed": eval_result.get("json_passed"),
        "evaluation": {
            "token_passed": eval_result.get("token_passed"),
            "json_passed": eval_result.get("json_passed"),
        },
    })
    return result
