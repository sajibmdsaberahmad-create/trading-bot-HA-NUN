#!/usr/bin/env python3
"""
core/halim_action_learn.py — Halim learns by doing.

Every guarded action (trade text, notify, read page, chart review, reasoning)
is journaled → exported as SFT gold for toddler+ training.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

ACTION_LOG = Path("halim/data/actions/action_log.jsonl")
ACTION_GOLD = Path("halim/data/training/action_gold.jsonl")
LEARN_CACHE = Path("halim/data/learn_cache")
HASH_PATH = Path("halim/data/training/action_gold_hashes.jsonl")


def _ensure_halim_pkg() -> None:
    import sys
    root = Path(__file__).resolve().parents[1] / "halim"
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))


def record_action(
    capability: str,
    action: str,
    *,
    input_text: str = "",
    output_text: str = "",
    outcome: str = "ok",
    source: str = "hanoon",
    meta: Optional[Dict[str, Any]] = None,
    cfg: Optional[BotConfig] = None,
) -> None:
    """Journal one Halim action — never raises, never blocks trading."""
    if os.getenv("HALIM_ACTION_LEARN", "true").lower() not in ("1", "true", "yes"):
        return

    try:
        from core.halim_identity import compute_halim_phase
        phase = compute_halim_phase(cfg)
    except Exception:
        phase = "newborn"

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capability": capability,
        "action": action,
        "phase": phase,
        "outcome": outcome,
        "source": source,
        "input_chars": len(input_text or ""),
        "output_chars": len(output_text or ""),
        "input_excerpt": (input_text or "")[:800],
        "output_excerpt": (output_text or "")[:1200],
        **(meta or {}),
    }
    try:
        ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ACTION_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        return

    _maybe_milestone(capability, phase)


def _action_counts() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not ACTION_LOG.is_file():
        return counts
    try:
        with open(ACTION_LOG, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    cap = json.loads(line).get("capability", "?")
                    counts[cap] = counts.get(cap, 0) + 1
                except Exception:
                    continue
    except Exception:
        pass
    return counts


def capability_maturity(capability: str, phase: str = "newborn") -> Dict[str, Any]:
    """0–100 progress from actions done + phase gate."""
    _ensure_halim_pkg()
    from halim.capabilities import CAPABILITIES, phase_index

    spec = CAPABILITIES.get(capability, {})
    need = int(spec.get("maturity_actions", 100))
    count = _action_counts().get(capability, 0)
    phase_ok = phase_index(phase) >= phase_index(str(spec.get("phase_min", "newborn")))
    action_pct = min(100, int(100 * count / max(1, need)))
    level = action_pct if phase_ok else min(action_pct, 40)
    return {
        "capability": capability,
        "label": spec.get("label", capability),
        "actions": count,
        "need_actions": need,
        "level_pct": level,
        "phase_unlocked": phase_ok,
        "backend": spec.get("backend", "hybrid"),
    }


def all_capabilities_status(phase: str = "newborn") -> Dict[str, Any]:
    _ensure_halim_pkg()
    from halim.capabilities import CAPABILITIES

    caps = {cid: capability_maturity(cid, phase) for cid in CAPABILITIES}
    return {"phase": phase, "capabilities": caps}


def _gold_hash(row: Dict[str, Any]) -> str:
    key = f"{row.get('capability')}|{row.get('instruction','')[:200]}|{row.get('output','')[:200]}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _known_hashes() -> set:
    out: set = set()
    if not HASH_PATH.is_file():
        return out
    try:
        with open(HASH_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.add(line)
    except Exception:
        pass
    return out


def _append_hash(h: str) -> None:
    try:
        HASH_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HASH_PATH, "a", encoding="utf-8") as fh:
            fh.write(h + "\n")
    except Exception:
        pass


def export_action_gold(
    *,
    max_records: int = 50_000,
    include_learn_cache: bool = True,
) -> Dict[str, Any]:
    """
    Merge action log + learn cache → instruction-tuning gold for Halim LM.
    Called off-hours and after evolution — never during hot trade path.
    """
    known = _known_hashes()
    added = 0
    skipped = 0

    ACTION_GOLD.parent.mkdir(parents=True, exist_ok=True)

    def _write(row: Dict[str, Any]) -> None:
        nonlocal added, skipped
        h = _gold_hash(row)
        if h in known:
            skipped += 1
            return
        known.add(h)
        with open(ACTION_GOLD, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
        _append_hash(h)
        added += 1

    if ACTION_LOG.is_file():
        lines: List[str] = []
        with open(ACTION_LOG, encoding="utf-8") as fh:
            lines = fh.readlines()
        for line in lines[-max_records:]:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("outcome") not in ("ok", "teacher", "success"):
                continue
            cap = ev.get("capability", "reasoning")
            inp = ev.get("input_excerpt") or ev.get("input", "")
            out = ev.get("output_excerpt") or ev.get("output", "")
            if not out or len(out) < 12:
                continue
            _write({
                "capability": cap,
                "instruction": _instruction_for(cap, ev.get("action", "")),
                "input": inp,
                "output": out,
                "phase": ev.get("phase", "newborn"),
                "source": ev.get("source", "action_log"),
                "timestamp": ev.get("timestamp"),
            })

    if include_learn_cache and LEARN_CACHE.is_dir():
        for cache_file in sorted(LEARN_CACHE.glob("*.json"))[:500]:
            try:
                doc = json.loads(cache_file.read_text())
            except Exception:
                continue
            text = doc.get("text") or doc.get("text_excerpt") or ""
            if len(text) < 80:
                continue
            topic = doc.get("topic") or doc.get("url") or cache_file.stem
            _write({
                "capability": "read_understand",
                "instruction": "Read this reference and retain trading-relevant facts.",
                "input": f"{topic}\n\n{text[:3000]}",
                "output": text[:1500],
                "phase": "newborn",
                "source": "learn_cache",
                "url": doc.get("url"),
            })

    if added:
        try:
            from core.halim_registry import append_registry
            append_registry("export_action_gold", {"added": added, "skipped": skipped})
        except Exception:
            pass
        log.info(f"🧠 Halim action gold +{added} pairs (skipped dup {skipped})")

    total = 0
    if ACTION_GOLD.is_file():
        with open(ACTION_GOLD, encoding="utf-8") as fh:
            total = sum(1 for _ in fh)

    result = {"ok": True, "added": added, "skipped": skipped, "total_gold": total}

    try:
        from core.halim_auto_lm import schedule_auto_retrain
        schedule_auto_retrain(result, trigger="export_action_gold")
    except Exception:
        pass

    return result


def _instruction_for(capability: str, action: str) -> str:
    table = {
        "text_compose": "Compose a Telegram briefing for the commander.",
        "decision_text": "Decide trade action with concise reasoning.",
        "read_understand": "Read and extract trading-relevant knowledge.",
        "reasoning": "Reason about session state and next steps.",
        "chart_read": "Describe chart setup, trend, and risk.",
        "enter_skip": "Enter or skip this setup.",
        "trade_reflex": "Execute reflex trade policy.",
    }
    return table.get(capability, action or "Complete the Halim task.")


_milestone_logged: set = set()


def _maybe_milestone(capability: str, phase: str) -> None:
    mat = capability_maturity(capability, phase)
    level = mat["level_pct"]
    for threshold in (25, 50, 75, 100):
        key = f"{capability}:{threshold}"
        if level >= threshold and key not in _milestone_logged:
            _milestone_logged.add(key)
            try:
                from core.halim_registry import append_capability_milestone
                append_capability_milestone(capability, threshold, phase)
            except Exception:
                pass
