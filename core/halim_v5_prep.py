#!/usr/bin/env python3
"""
core/halim_v5_prep.py — Off-hours v5 training pack: read-only web + API enrichment → Colab zip.

Phases:
  1. Guardrails + operator settings (web read-only, API allowlisted)
  2. Learn browse cycles (wiki, RSS, Google AI snippets → action gold)
  3. JSON entry gold (council/outcome/experience + API teacher + web drills)
  4. Export all gold + prepare SFT + Colab package meta
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log


def v5_prep_active() -> bool:
    return os.getenv("HALIM_V5_PREP", "false").lower() in ("1", "true", "yes")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _apply_v5_env_defaults() -> None:
    """Raise caps for one-shot v5 pack — still read-only external."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    os.environ["HALIM_V5_PREP"] = "true"
    os.environ["HALIM_WEB_LEARN"] = "true"
    os.environ["HALIM_OPERATOR_SETTINGS"] = "true"
    os.environ["HALIM_GOOGLE_AI_SEARCH"] = "true"
    os.environ["HALIM_LEARN_GOOGLE_SNIPPETS"] = "true"
    os.environ["HALIM_JSON_ENTRY_API"] = "true"
    os.environ["HALIM_LEARN_UNCAPPED_DATE"] = today
    os.environ["HALIM_LEARN_BATCH_MAX"] = os.getenv("HALIM_LEARN_BATCH_MAX", "12")
    os.environ["HALIM_LEARN_GOOGLE_MAX"] = os.getenv("HALIM_LEARN_GOOGLE_MAX", "8")
    os.environ["HALIM_LEARN_BATCH_PAUSE_SEC"] = os.getenv("HALIM_LEARN_BATCH_PAUSE_SEC", "1.5")
    os.environ["HALIM_JSON_ENTRY_API_MAX"] = os.getenv("HALIM_JSON_ENTRY_API_MAX", "500")
    os.environ["HALIM_V5_WEB_DRILL_MAX"] = os.getenv("HALIM_V5_WEB_DRILL_MAX", "80")
    os.environ["HALIM_V5_MAX_FETCHES"] = os.getenv("HALIM_V5_MAX_FETCHES", "2500")
    os.environ["HALIM_V5_LEARN_CYCLES"] = os.getenv("HALIM_V5_LEARN_CYCLES", "12")
    os.environ["HALIM_GOOGLE_AI_DAILY_CAP"] = os.getenv("HALIM_GOOGLE_AI_DAILY_CAP", "400")
    os.environ["HALIM_LEARN_UNCAPPED_MAX_GOLD"] = os.getenv("HALIM_LEARN_UNCAPPED_MAX_GOLD", "300")
    os.environ["HALIM_LEARN_UNCAPPED_MAX_FETCHES"] = os.getenv("HALIM_LEARN_UNCAPPED_MAX_FETCHES", "2500")
    os.environ["HALIM_V5_API_DAILY_CAP"] = os.getenv("HALIM_V5_API_DAILY_CAP", "2000")


def _phase_guardrails(cfg: BotConfig) -> Dict[str, Any]:
    from core.halim_guardrails import apply_operator_frontier_settings, guardrail_status

    apply_operator_frontier_settings(cfg)
    return guardrail_status(cfg)


def _phase_learn_browse(cfg: BotConfig, *, cycles: int, force: bool) -> Dict[str, Any]:
    from core.halim_learn_browse import run_learn_browse_cycle

    cycles = max(0, cycles)
    if cycles <= 0:
        return {"ok": True, "skipped": True, "cycles": 0}

    totals = {"pages_ok": 0, "gold_added": 0, "google_ok": 0, "cycles_run": 0}
    cycle_results: List[Dict[str, Any]] = []

    log.info(f"📚 v5 prep — learn browse ×{cycles} (read-only web + Google snippets)")
    for i in range(cycles):
        r = run_learn_browse_cycle(cfg, export_gold=True, force=force)
        cycle_results.append(
            {
                "cycle": i + 1,
                "ok": r.get("ok"),
                "pages_ok": r.get("pages_ok", 0),
                "reason": r.get("reason"),
            }
        )
        totals["cycles_run"] += 1
        totals["pages_ok"] += int(r.get("pages_ok", 0) or 0)
        totals["google_ok"] += int(r.get("google_ok", 0) or 0)
        totals["gold_added"] += int((r.get("export_gold") or {}).get("added", 0) or 0)
        if r.get("reason") == "learn_fetch_daily_cap":
            log.warning("📚 v5 prep learn — daily fetch cap hit, stopping browse phase")
            break
        if not r.get("ok") and r.get("reason") == "trading_active":
            log.warning("📚 v5 prep learn — trading active, stopping browse phase")
            break
        pause = float(os.getenv("HALIM_LEARN_BATCH_PAUSE_SEC", "1.5"))
        if pause > 0 and i + 1 < cycles:
            time.sleep(pause)

    return {"ok": totals["pages_ok"] > 0 or totals["gold_added"] > 0, **totals, "cycles": cycle_results}


def _phase_json_gold(cfg: BotConfig) -> Dict[str, Any]:
    from core.halim_json_entry_gold import export_json_entry_gold, export_web_json_drills

    api_max = int(os.getenv("HALIM_JSON_ENTRY_API_MAX", "500"))
    web_max = int(os.getenv("HALIM_V5_WEB_DRILL_MAX", "80"))

    log.info(f"🧠 v5 prep — JSON entry gold (API max={api_max}, web drills max={web_max})")
    entry = export_json_entry_gold(root=_repo_root(), use_api=True, api_max=api_max, cfg=cfg)
    web = export_web_json_drills(root=_repo_root(), cfg=cfg, api_max=web_max)
    return {"json_entry": entry, "web_drills": web}


def _phase_export_gold() -> Dict[str, Any]:
    root = _repo_root()
    script = root / "halim/scripts/export_training_gold.py"
    log.info("📦 v5 prep — export all training gold")
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(root),
        env={**os.environ, "HALIM_REPO_ROOT": str(root)},
        capture_output=True,
        text=True,
    )
    out: Dict[str, Any] = {"ok": proc.returncode == 0, "returncode": proc.returncode}
    if proc.stdout.strip():
        try:
            out["result"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out["stdout"] = proc.stdout[-2000:]
    if proc.stderr.strip():
        out["stderr"] = proc.stderr[-1500:]
    return out


def _phase_prepare_sft() -> Dict[str, Any]:
    root = _repo_root()
    script = root / "halim/scripts/prepare_sft.py"
    min_pairs = os.getenv("HALIM_TODDLER_MIN_PAIRS", "2500")
    env = {**os.environ, "HALIM_AUTO_PACKAGE_COLAB": "true"}
    log.info("📦 v5 prep — prepare SFT + Colab zip")
    proc = subprocess.run(
        [sys.executable, str(script), "--min-pairs", str(min_pairs)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
    )
    out: Dict[str, Any] = {"ok": proc.returncode == 0, "returncode": proc.returncode}
    if proc.stdout.strip():
        try:
            out["result"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out["stdout"] = proc.stdout[-2000:]
    if proc.stderr.strip():
        out["stderr"] = proc.stderr[-1500:]
    return out


def _phase_readiness() -> Dict[str, Any]:
    root = _repo_root()
    script = root / "scripts/halim_readiness.sh"
    if not script.is_file():
        return {"ok": False, "reason": "readiness_script_missing"}
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-3000:] if proc.stdout else "",
    }


def run_v5_prep(
    cfg: Optional[BotConfig] = None,
    *,
    learn_cycles: Optional[int] = None,
    skip_learn: bool = False,
    force_learn: bool = True,
) -> Dict[str, Any]:
    """
    Full v5 training pack. Best off-hours; set HALIM_LEARN_DURING_TRADING=true to run beside live bot.
    """
    _apply_v5_env_defaults()
    cfg = cfg or BotConfig()

    if skip_learn:
        cycles = 0
    else:
        cycles = learn_cycles
        if cycles is None:
            cycles = int(os.getenv("HALIM_V5_LEARN_CYCLES", "12"))

    report: Dict[str, Any] = {
        "ok": False,
        "v5_prep": True,
        "phases": {},
    }

    log.info("=" * 60)
    log.info("  HALIM v5 PREP — read-only web + API → Colab training pack")
    log.info("=" * 60)

    report["phases"]["guardrails"] = _phase_guardrails(cfg)
    report["phases"]["learn_browse"] = _phase_learn_browse(cfg, cycles=cycles, force=force_learn)
    report["phases"]["json_gold"] = _phase_json_gold(cfg)
    report["phases"]["export_gold"] = _phase_export_gold()
    report["phases"]["prepare_sft"] = _phase_prepare_sft()
    report["phases"]["readiness"] = _phase_readiness()

    sft = report["phases"]["prepare_sft"]
    export = report["phases"]["export_gold"]
    json_gold = report["phases"]["json_gold"].get("json_entry") or {}
    report["ok"] = bool(sft.get("ok")) and bool(export.get("ok"))
    report["summary"] = {
        "learn_pages": report["phases"]["learn_browse"].get("pages_ok", 0),
        "json_entry_added": json_gold.get("added", 0),
        "json_entry_total": json_gold.get("total_gold", 0),
        "sft_pairs": (sft.get("result") or {}).get("pairs_total"),
        "zip": str(_repo_root() / "halim_sft.zip"),
    }

    log.info(
        f"✅ Halim v5 prep done — ok={report['ok']} "
        f"json_entry={json_gold.get('total_gold', 0)} "
        f"learn_pages={report['summary']['learn_pages']}"
    )
    try:
        from core.halim_registry import append_registry

        append_registry("halim_v5_prep", report.get("summary", {}))
    except Exception:
        pass
    return report


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Halim v5 prep — web browse + API → Colab pack")
    parser.add_argument("--skip-learn", action="store_true", help="Skip web browse phase")
    parser.add_argument("--learn-cycles", type=int, default=None)
    parser.add_argument("--no-force-learn", action="store_true", help="Pause learn if trading active")
    args = parser.parse_args(argv)

    result = run_v5_prep(
        skip_learn=args.skip_learn,
        learn_cycles=args.learn_cycles,
        force_learn=not args.no_force_learn,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
