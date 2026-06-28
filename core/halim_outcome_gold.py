#!/usr/bin/env python3
"""
core/halim_outcome_gold.py — Outcome-labeled exit (and entry) gold for Halim SFT.

Tracks Halim advisories during a trade, then at close labels whether the call was
right (good_cut, good_hold, held_too_long, gave_back_profits, …) and writes
correction pairs for the next Colab train.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

_REPO = Path(__file__).resolve().parents[1]
OUTCOME_EVENTS = _REPO / "halim/data/outcome/outcome_events.jsonl"
OUTCOME_GOLD = _REPO / "halim/data/training/outcome_gold.jsonl"
OUTCOME_HASHES = _REPO / "halim/data/training/outcome_gold_hashes.jsonl"

_lock = threading.Lock()
_advisories: Dict[str, Dict[str, Any]] = {}
_seen_hashes: Optional[set] = None


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    if os.getenv("HALIM_OUTCOME_GOLD", "true").lower() not in ("1", "true", "yes"):
        return False
    try:
        from core.halim_ppo_coevolution import _enabled as coev_on
        return coev_on(cfg)
    except Exception:
        return True


def _append(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def _load_hashes() -> set:
    global _seen_hashes
    if _seen_hashes is not None:
        return _seen_hashes
    _seen_hashes = set()
    if OUTCOME_HASHES.is_file():
        with open(OUTCOME_HASHES, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    _seen_hashes.add(line)
    return _seen_hashes


def _row_key(row: Dict[str, Any]) -> str:
    blob = "|".join(
        str(row.get(k, ""))[:300]
        for k in ("capability", "instruction", "input", "output", "outcome_label")
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def clear_advisories(ticker: str) -> None:
    with _lock:
        _advisories.pop((ticker or "").upper(), None)


def register_exit_advisory(
    ticker: str,
    *,
    halim_exit: bool,
    halim_conf: float,
    halim_reason: str = "",
    pnl_pct: float = 0.0,
    peak_pct: float = 0.0,
    ppo_exit: bool = False,
    ppo_conf: float = 0.5,
    task: str = "exit_decision",
    pipeline: str = "",
    cfg: Optional[BotConfig] = None,
) -> None:
    if not _enabled(cfg):
        return
    key = (ticker or "").upper()
    if not key:
        return
    snap = {
        "kind": "exit",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "halim_exit": bool(halim_exit),
        "halim_conf": round(float(halim_conf), 4),
        "halim_reason": str(halim_reason or "")[:120],
        "pnl_pct": round(float(pnl_pct), 3),
        "peak_pct": round(float(peak_pct), 3),
        "ppo_exit": bool(ppo_exit),
        "ppo_conf": round(float(ppo_conf), 4),
        "task": str(task or "exit_decision"),
        "pipeline": str(pipeline or "")[:120],
    }
    with _lock:
        slot = _advisories.setdefault(key, {"exit": [], "entry": None})
        exits: List[Dict[str, Any]] = slot["exit"]
        exits.append(snap)
        if len(exits) > 40:
            slot["exit"] = exits[-40:]


def register_entry_advisory(
    ticker: str,
    *,
    halim_enter: bool,
    halim_conf: float,
    halim_reason: str = "",
    spike_ratio: float = 0.0,
    scan_score: float = 0.0,
    ppo_buy: bool = False,
    ppo_conf: float = 0.5,
    pipeline: str = "",
    cfg: Optional[BotConfig] = None,
) -> None:
    if not _enabled(cfg):
        return
    key = (ticker or "").upper()
    if not key:
        return
    snap = {
        "kind": "entry",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "halim_enter": bool(halim_enter),
        "halim_conf": round(float(halim_conf), 4),
        "halim_reason": str(halim_reason or "")[:120],
        "spike_ratio": round(float(spike_ratio), 3),
        "scan_score": round(float(scan_score), 1),
        "ppo_buy": bool(ppo_buy),
        "ppo_conf": round(float(ppo_conf), 4),
        "pipeline": str(pipeline or "")[:120],
    }
    with _lock:
        slot = _advisories.setdefault(key, {"exit": [], "entry": None})
        slot["entry"] = snap


def _peak_pct_from_trade(trade_rec: Dict[str, Any]) -> float:
    if trade_rec.get("peak_pct") is not None:
        return float(trade_rec["peak_pct"])
    entry = float(trade_rec.get("entry_fill") or trade_rec.get("entry", 0) or 0)
    peak_px = float(trade_rec.get("peak", 0) or 0)
    if entry > 0 and peak_px > 0:
        return round(((peak_px / entry) - 1) * 100, 3)
    return 0.0


def classify_exit_outcome(
    *,
    halim_exit: Optional[bool],
    halim_conf: float,
    pnl_usd: float,
    pnl_pct: float,
    peak_pct: float,
    exit_reason: str = "",
    hold_sec: float = 0.0,
) -> Tuple[str, List[str]]:
    """Return (primary_label, all_labels)."""
    labels: List[str] = []
    conf_ok = halim_conf >= 0.55
    giveback = max(0.0, peak_pct - pnl_pct) if peak_pct > pnl_pct else 0.0

    if halim_exit is None:
        return "no_halim_advisory", ["no_halim_advisory"]

    if halim_exit and conf_ok:
        if pnl_usd >= 0:
            labels.append("good_cut")
        elif giveback > 0.25 or peak_pct > 0.15:
            labels.append("good_cut")
        else:
            labels.append("cut_attempt")
    elif not halim_exit and conf_ok:
        if pnl_usd >= 0:
            labels.append("good_hold")
        elif giveback >= 0.35 and peak_pct > 0.2:
            labels.append("held_too_long")
            labels.append("gave_back_profits")
        elif pnl_pct <= -0.15:
            labels.append("held_too_long")
        else:
            labels.append("bad_hold")

    if peak_pct > max(0.25, abs(pnl_pct) + 0.15) and pnl_pct < peak_pct * 0.45:
        if pnl_pct < 0.2 and "gave_back_profits" not in labels:
            labels.append("gave_back_profits")
        if not halim_exit and conf_ok and "held_too_long" not in labels:
            labels.append("held_too_long")

    if halim_exit and conf_ok and pnl_usd < 0 and peak_pct < 0.1:
        labels.append("early_cut")

    if not labels:
        labels.append("neutral")

    primary = labels[0]
    if "held_too_long" in labels:
        primary = "held_too_long"
    elif "good_cut" in labels:
        primary = "good_cut"
    elif "gave_back_profits" in labels:
        primary = "gave_back_profits"
    elif "good_hold" in labels:
        primary = "good_hold"

    return primary, labels


def classify_entry_outcome(
    *,
    halim_enter: Optional[bool],
    halim_conf: float,
    pnl_usd: float,
    pnl_pct: float,
) -> Tuple[str, List[str]]:
    if halim_enter is None:
        return "no_entry_advisory", ["no_entry_advisory"]
    labels: List[str] = []
    conf_ok = halim_conf >= 0.55
    if halim_enter and conf_ok:
        if pnl_usd >= 0:
            labels.append("good_entry_call")
        else:
            labels.append("bad_entry_call")
    elif not halim_enter and conf_ok:
        if pnl_usd < 0:
            labels.append("good_skip_call")
        else:
            labels.append("missed_entry")
    if not labels:
        labels.append("neutral_entry")
    primary = labels[0]
    if "bad_entry_call" in labels:
        primary = "bad_entry_call"
    elif "good_entry_call" in labels:
        primary = "good_entry_call"
    return primary, labels


def _format_advisory_phrase(snap: Dict[str, Any]) -> str:
    if snap.get("kind") == "entry":
        act = "ENTER" if snap.get("halim_enter") else "SKIP"
        return (
            f"Halim said {act} (conf {snap.get('halim_conf', 0):.0%}) "
            f"spike={snap.get('spike_ratio', 0):.2f}x scan={snap.get('scan_score', 0):.0f}"
        )
    act = "EXIT" if snap.get("halim_exit") else "HOLD"
    return (
        f"Halim said {act} at pnl {snap.get('pnl_pct', 0):+.2f}% "
        f"(peak {snap.get('peak_pct', 0):+.2f}%, conf {snap.get('halim_conf', 0):.0%})"
    )


def _build_exit_correction_row(
    *,
    ticker: str,
    trade_rec: Dict[str, Any],
    last_exit: Dict[str, Any],
    primary: str,
    labels: List[str],
) -> Optional[Dict[str, Any]]:
    pnl_usd = float(trade_rec.get("pnl_usd", 0))
    pnl_pct = float(trade_rec.get("pnl_pct", 0))
    peak_pct = _peak_pct_from_trade(trade_rec)
    exit_reason = str(trade_rec.get("exit_reason", ""))[:120]
    hold_sec = float(trade_rec.get("hold_sec", 0))

    halim_exit = bool(last_exit.get("halim_exit"))
    halim_conf = float(last_exit.get("halim_conf", 0))
    halim_reason = str(last_exit.get("halim_reason", ""))[:80]
    at_pnl = float(last_exit.get("pnl_pct", 0))
    at_peak = float(last_exit.get("peak_pct", peak_pct))

    user = (
        f"Ticker: {ticker}\n"
        f"Task: exit_outcome_review\n"
        f"{_format_advisory_phrase(last_exit)}\n"
        f"Reason then: {halim_reason}\n"
        f"Trade closed: pnl_usd={pnl_usd:+.2f} pnl_pct={pnl_pct:+.2f}% "
        f"peak_pct={peak_pct:+.2f}% hold_sec={hold_sec:.0f}\n"
        f"exit_reason={exit_reason}\n"
        f"Labels: {', '.join(labels)}"
    )

    if primary in ("held_too_long", "gave_back_profits", "bad_hold"):
        correct_exit = True
        correct_conf = min(0.92, max(0.72, halim_conf + 0.12))
        lesson = (
            f"Halim said HOLD at {at_pnl:+.2f}% (peak {at_peak:+.2f}%) → "
            f"closed at {pnl_pct:+.2f}% (${pnl_usd:+.2f}). Correction: should EXIT earlier."
        )
    elif primary == "good_cut":
        correct_exit = True
        correct_conf = min(0.95, max(0.75, halim_conf))
        lesson = (
            f"Halim said EXIT at {at_pnl:+.2f}% → closed at {pnl_pct:+.2f}%. "
            f"Reinforce: lock or cut when momentum fades."
        )
    elif primary == "good_hold":
        correct_exit = False
        correct_conf = min(0.92, max(0.68, halim_conf))
        lesson = (
            f"Halim said HOLD at {at_pnl:+.2f}% → closed at {pnl_pct:+.2f}%. "
            f"Reinforce: trail winner while structure holds."
        )
    else:
        correct_exit = halim_exit if pnl_usd >= 0 else True
        correct_conf = 0.65
        lesson = (
            f"Halim said {'EXIT' if halim_exit else 'HOLD'} at {at_pnl:+.2f}% → "
            f"closed at {pnl_pct:+.2f}%."
        )

    assistant = (
        f"{'EXIT' if correct_exit else 'HOLD'} | confidence={correct_conf:.2f} | "
        f"{lesson} | label={primary}"
    )

    return {
        "capability": "exit_decision",
        "instruction": (
            "Given Halim's prior exit advice and the actual trade close, "
            "state the correct exit call with brief reasoning."
        ),
        "input": user[:4000],
        "output": assistant[:2000],
        "source": "outcome_gold",
        "outcome_label": primary,
        "outcome_labels": labels,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 3),
        "peak_pct": round(peak_pct, 3),
        "exit_reason": exit_reason,
        "held_too_long": primary in ("held_too_long", "gave_back_profits"),
        "good_cut": primary == "good_cut",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
    }


def _build_entry_correction_row(
    *,
    ticker: str,
    trade_rec: Dict[str, Any],
    entry_snap: Dict[str, Any],
    primary: str,
    labels: List[str],
) -> Optional[Dict[str, Any]]:
    pnl_usd = float(trade_rec.get("pnl_usd", 0))
    pnl_pct = float(trade_rec.get("pnl_pct", 0))
    halim_enter = bool(entry_snap.get("halim_enter"))
    halim_conf = float(entry_snap.get("halim_conf", 0))

    user = (
        f"Ticker: {ticker}\n"
        f"Task: entry_outcome_review\n"
        f"{_format_advisory_phrase(entry_snap)}\n"
        f"Trade closed: pnl_usd={pnl_usd:+.2f} pnl_pct={pnl_pct:+.2f}%\n"
        f"Labels: {', '.join(labels)}"
    )

    if primary == "bad_entry_call":
        correct_enter = False
        lesson = f"Entry call was wrong — closed {pnl_pct:+.2f}%."
    elif primary == "good_entry_call":
        correct_enter = True
        lesson = f"Entry call worked — closed {pnl_pct:+.2f}%."
    else:
        correct_enter = halim_enter
        lesson = f"Closed {pnl_pct:+.2f}% after {'ENTER' if halim_enter else 'SKIP'} advice."

    assistant = (
        f"{'ENTER' if correct_enter else 'SKIP'} | confidence={halim_conf:.2f} | "
        f"{lesson} | label={primary}"
    )

    return {
        "capability": "enter_skip",
        "instruction": (
            "Given Halim's entry advice and trade outcome, state the correct enter/skip call."
        ),
        "input": user[:3000],
        "output": assistant[:1500],
        "source": "outcome_gold",
        "outcome_label": primary,
        "outcome_labels": labels,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 3),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
    }


def record_trade_outcome(trade_rec: Dict[str, Any], cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Label Halim advisories at trade close and append SFT correction gold."""
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return {"ok": False, "reason": "disabled"}

    ticker = str(trade_rec.get("ticker", "")).upper()
    if not ticker:
        return {"ok": False, "reason": "no_ticker"}

    pnl_usd = float(trade_rec.get("pnl_usd", 0))
    pnl_pct = float(trade_rec.get("pnl_pct", 0))
    peak_pct = _peak_pct_from_trade(trade_rec)
    exit_reason = str(trade_rec.get("exit_reason", ""))[:200]
    hold_sec = float(trade_rec.get("hold_sec", 0))
    win = trade_rec.get("result") == "win" or pnl_usd > 0

    with _lock:
        slot = _advisories.pop(ticker, {"exit": [], "entry": None})
    exit_snaps: List[Dict[str, Any]] = list(slot.get("exit") or [])
    entry_snap: Optional[Dict[str, Any]] = slot.get("entry")

    last_exit = exit_snaps[-1] if exit_snaps else None
    halim_exit = bool(last_exit["halim_exit"]) if last_exit else None
    halim_conf = float(last_exit.get("halim_conf", 0)) if last_exit else 0.0

    exit_primary, exit_labels = classify_exit_outcome(
        halim_exit=halim_exit,
        halim_conf=halim_conf,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        peak_pct=peak_pct,
        exit_reason=exit_reason,
        hold_sec=hold_sec,
    )

    entry_primary, entry_labels = ("no_entry_advisory", ["no_entry_advisory"])
    if entry_snap:
        entry_primary, entry_labels = classify_entry_outcome(
            halim_enter=bool(entry_snap.get("halim_enter")),
            halim_conf=float(entry_snap.get("halim_conf", 0)),
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
        )

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "trade_outcome_labeled",
        "ticker": ticker,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 3),
        "peak_pct": round(peak_pct, 3),
        "exit_reason": exit_reason,
        "hold_sec": round(hold_sec, 1),
        "win": win,
        "exit_label": exit_primary,
        "exit_labels": exit_labels,
        "entry_label": entry_primary,
        "entry_labels": entry_labels,
        "exit_advisory_count": len(exit_snaps),
        "last_exit_advisory": last_exit,
        "entry_advisory": entry_snap,
        "held_too_long": exit_primary in ("held_too_long", "gave_back_profits"),
        "good_cut": exit_primary == "good_cut",
    }
    _append(OUTCOME_EVENTS, event)

    added = 0
    skipped = 0
    hashes = _load_hashes()

    rows: List[Dict[str, Any]] = []
    if last_exit and exit_primary != "no_halim_advisory":
        row = _build_exit_correction_row(
            ticker=ticker,
            trade_rec=trade_rec,
            last_exit=last_exit,
            primary=exit_primary,
            labels=exit_labels,
        )
        if row:
            rows.append(row)

    if entry_snap and entry_primary not in ("no_entry_advisory", "neutral_entry"):
        row = _build_entry_correction_row(
            ticker=ticker,
            trade_rec=trade_rec,
            entry_snap=entry_snap,
            primary=entry_primary,
            labels=entry_labels,
        )
        if row:
            rows.append(row)

    for row in rows:
        h = _row_key(row)
        if h in hashes:
            skipped += 1
            continue
        hashes.add(h)
        _append(OUTCOME_HASHES, h)
        _append(OUTCOME_GOLD, row)
        added += 1

    if added:
        log.info(
            f"🎯 Halim outcome gold +{added} ({ticker} {exit_primary} "
            f"pnl={pnl_usd:+.2f} exit_reason={exit_reason[:40]})"
        )

    return {
        "ok": True,
        "ticker": ticker,
        "exit_label": exit_primary,
        "entry_label": entry_primary,
        "added": added,
        "skipped": skipped,
        "held_too_long": event["held_too_long"],
        "good_cut": event["good_cut"],
    }


def export_outcome_gold() -> Dict[str, Any]:
    """Idempotent export summary — rows are written at trade close."""
    total = 0
    if OUTCOME_GOLD.is_file():
        with open(OUTCOME_GOLD, encoding="utf-8") as fh:
            total = sum(1 for _ in fh)
    events = 0
    if OUTCOME_EVENTS.is_file():
        with open(OUTCOME_EVENTS, encoding="utf-8") as fh:
            events = sum(1 for _ in fh)
    return {"ok": True, "total_gold": total, "total_events": events, "path": str(OUTCOME_GOLD)}
