#!/usr/bin/env python3
"""Halim Smart Sprint — status + enable helpers (M2 8GB all-phase upgrade)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig


def sprint_enabled() -> bool:
    return os.getenv("HALIM_SMART_SPRINT", "true").lower() in ("1", "true", "yes")


def sprint_block_micro_fast(cfg: Optional[BotConfig] = None) -> bool:
    if not sprint_enabled():
        return False
    if os.getenv("HALIM_SPRINT_BLOCK_MICRO_FAST", "true").lower() not in ("1", "true", "yes"):
        return False
    try:
        from core.brain_maturity import compute_stage
        return compute_stage(cfg) in ("newborn", "infant", "toddler")
    except Exception:
        return True


def sprint_status(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Progress toward child + LM health — for scripts and logs."""
    cfg = cfg or BotConfig()
    root = Path(__file__).resolve().parents[1]
    target = int(os.getenv("BRAIN_CHILD_DATASET_TARGET", "200"))

    council_lines = 0
    council_path = root / "models" / "council_training_dataset.jsonl"
    if council_path.is_file():
        council_lines = sum(1 for ln in council_path.read_text(encoding="utf-8").splitlines() if ln.strip())

    json_gold = 0
    jg = root / "halim" / "data" / "training" / "json_entry_gold.jsonl"
    if jg.is_file():
        json_gold = sum(1 for ln in jg.read_text(encoding="utf-8").splitlines() if ln.strip())

    sft_pairs = 0
    mf = root / "halim" / "data" / "training" / "sft" / "manifest.json"
    if mf.is_file():
        try:
            sft_pairs = int(json.loads(mf.read_text(encoding="utf-8")).get("pairs_total", 0))
        except Exception:
            pass

    stage = "unknown"
    proxy_acc = None
    ai_sure = False
    try:
        from core.brain_maturity import compute_stage, maturity_ai_sure_entry, maturity_snapshot
        stage = compute_stage(cfg)
        snap = maturity_snapshot(cfg)
        proxy_acc = snap.get("metrics", {}).get("proxy_holdout_accuracy")
        ai_sure = maturity_ai_sure_entry(cfg)
    except Exception:
        pass

    ck = root / "halim" / "data" / "checkpoints" / "latest" / "adapters.safetensors"
    serve_ok = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8765/v1/status", timeout=2) as resp:
            serve_ok = json.loads(resp.read()).get("ok", False)
    except Exception:
        pass

    gap = max(0, target - council_lines)
    return {
        "sprint_enabled": sprint_enabled(),
        "brain_stage": stage,
        "child_target_pairs": target,
        "council_dataset_pairs": council_lines,
        "pairs_to_child": gap,
        "json_entry_gold": json_gold,
        "sft_pairs_total": sft_pairs,
        "proxy_holdout_accuracy": proxy_acc,
        "ai_sure_entry": ai_sure,
        "checkpoint_adapter": ck.is_file(),
        "halim_serve_ok": serve_ok,
        "block_micro_fast": sprint_block_micro_fast(cfg),
        "strict_profit_prob": os.getenv("SMART_STACK_STRICT_PROFIT_PROB", ""),
        "entry_await_sec": float(os.getenv("HALIM_ENTRY_AWAIT_SEC", "0") or 0),
    }


def print_sprint_status(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    from core.notify import log
    s = sprint_status(cfg)
    log.info(
        f"  🧠 Halim sprint: stage={s['brain_stage']} "
        f"council={s['council_dataset_pairs']}/{s['child_target_pairs']} "
        f"json_gold={s['json_entry_gold']} sft={s['sft_pairs_total']} "
        f"proxy={s.get('proxy_holdout_accuracy')} "
        f"serve={'ok' if s['halim_serve_ok'] else 'down'} "
        f"micro_fast={'blocked' if s['block_micro_fast'] else 'on'}"
    )
    return s
