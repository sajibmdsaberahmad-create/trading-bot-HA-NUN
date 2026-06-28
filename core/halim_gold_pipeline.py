#!/usr/bin/env python3
"""
core/halim_gold_pipeline.py — One path for all Halim gold export + optional SFT/Colab zip.

Used by replay teardown, evolution, learning snapshots, and weekend replay.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_json_stdout(stdout: str) -> Dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def export_halim_gold(*, include_learn_cache: bool = True) -> Dict[str, Any]:
    """Export action + coevolution + dialogue gold (idempotent dedupe)."""
    root = _repo_root()
    os.environ.setdefault("HALIM_REPO_ROOT", str(root))
    halim_pkg = root / "halim"
    if str(halim_pkg) not in sys.path:
        sys.path.insert(0, str(halim_pkg))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from core.halim_action_learn import export_action_gold
        from core.halim_ppo_coevolution import export_coevolution_gold
        from core.halim_ppo_dialogue import export_dialogue_gold
        from halim.dataset import count_raw_sources

        action = export_action_gold(include_learn_cache=include_learn_cache)
        coev = export_coevolution_gold()
        dialogue = export_dialogue_gold()
        raw = count_raw_sources(root)
        return {
            "ok": True,
            "action_gold": action,
            "coevolution_gold": coev,
            "dialogue_gold": dialogue,
            "raw_sources": raw,
        }
    except Exception as exc:
        log.debug(f"Halim gold export: {exc}")
        return {"ok": False, "error": str(exc)[:120]}


def prepare_halim_sft(*, min_pairs: Optional[int] = None) -> Dict[str, Any]:
    root = _repo_root()
    py = sys.executable
    env = os.environ.copy()
    env.setdefault("HALIM_REPO_ROOT", str(root))
    env["PYTHONPATH"] = f"{root / 'halim'}{os.pathsep}{root}{os.pathsep}{env.get('PYTHONPATH', '')}"
    if min_pairs is not None:
        env["HALIM_TODDLER_MIN_PAIRS"] = str(min_pairs)
    try:
        proc = subprocess.run(
            [py, str(root / "halim/scripts/prepare_sft.py")]
            + (["--min-pairs", str(min_pairs)] if min_pairs is not None else []),
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        parsed = _parse_json_stdout(proc.stdout)
        if proc.returncode == 0 and parsed.get("ok", True):
            return parsed or {"ok": True}
        return {
            "ok": False,
            "code": proc.returncode,
            "stderr": (proc.stderr or "")[:200],
            "parsed": parsed,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def package_halim_colab() -> Dict[str, Any]:
    root = _repo_root()
    py = sys.executable
    env = os.environ.copy()
    env.setdefault("HALIM_REPO_ROOT", str(root))
    env["HALIM_SKIP_PREPARE"] = "true"
    env["PYTHONPATH"] = f"{root / 'halim'}{os.pathsep}{root}{os.pathsep}{env.get('PYTHONPATH', '')}"
    try:
        proc = subprocess.run(
            [py, str(root / "halim/scripts/package_colab_sft.py")],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        parsed = _parse_json_stdout(proc.stdout)
        ok = proc.returncode == 0 and parsed.get("ok", False)
        return parsed if ok else {
            "ok": False,
            "code": proc.returncode,
            "stderr": (proc.stderr or "")[:200],
            "parsed": parsed,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def run_halim_gold_pipeline(
    cfg: Optional[BotConfig] = None,
    *,
    trigger: str = "manual",
    prepare_sft: bool = False,
    package_colab: bool = False,
    min_sft_pairs: Optional[int] = None,
    include_learn_cache: bool = True,
) -> Dict[str, Any]:
    """
    Full Halim gold path — export all sources; optionally rebuild SFT + halim_sft.zip.
  """
    cfg = cfg or BotConfig()
    result: Dict[str, Any] = {"trigger": trigger, "steps": {}}

    result["steps"]["export"] = export_halim_gold(include_learn_cache=include_learn_cache)

    if prepare_sft or os.getenv("HALIM_AUTO_PREPARE_SFT", "false").lower() in ("1", "true", "yes"):
        min_p = min_sft_pairs
        if min_p is None:
            min_p = int(os.getenv("HALIM_TODDLER_MIN_PAIRS", "2500"))
        result["steps"]["prepare_sft"] = prepare_halim_sft(min_pairs=min_p)

    if package_colab or os.getenv("HALIM_AUTO_PACKAGE_COLAB", "false").lower() in ("1", "true", "yes"):
        result["steps"]["colab_package"] = package_halim_colab()

    try:
        from core.halim_identity import sync_identity_phase, write_halim_manifest
        sync_identity_phase(cfg)
        write_halim_manifest(cfg)
        result["steps"]["manifest"] = {"ok": True}
    except Exception as exc:
        result["steps"]["manifest"] = {"ok": False, "error": str(exc)[:80]}

    export_step = result["steps"].get("export") or {}
    result["ok"] = bool(export_step.get("ok", False))
    raw = export_step.get("raw_sources") or {}
    if raw:
        log.info(
            f"🧠 Halim gold ({trigger}): raw={raw.get('total_raw', 0)} "
            f"coev={raw.get('coevolution', 0)} dlg={raw.get('dialogue', 0)}"
        )
    return result
