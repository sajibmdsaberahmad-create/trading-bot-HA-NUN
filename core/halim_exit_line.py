#!/usr/bin/env python3
"""
core/halim_exit_line.py — Non-blocking Halim LM exit advisory (learn by doing).

Short JSON via halim serve (MLX). Throttled per open position — never blocks ticks.
Records action gold + feeds coevolution when blended in AICommander.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.live_ai_pipeline import _parse_json_response
from core.notify import log


def halim_exit_lm_enabled(cfg: Optional[BotConfig] = None) -> bool:
    if os.getenv("HALIM_EXIT_LM_ENABLED", "true").lower() not in ("1", "true", "yes"):
        return False
    try:
        from core.halim_unlock import is_usable
        if not is_usable("decision_text", cfg):
            return False
    except Exception:
        pass
    return True


def _exit_timeout_sec() -> float:
    return float(os.getenv("HALIM_EXIT_LM_TIMEOUT_SEC", "6"))


def _max_age_sec(cfg: BotConfig) -> float:
    return float(os.getenv("HALIM_EXIT_LM_MAX_AGE_SEC", "8"))


def _min_ring_sec(cfg: BotConfig) -> float:
    return float(os.getenv("HALIM_EXIT_LM_MIN_RING_SEC", "30"))


def _build_exit_prompt(
    *,
    ticker: str,
    price: float,
    pnl_pct: float,
    peak_pct: float,
    stop: float,
    target: float,
    ppo_exit: bool,
    ppo_conf: float,
    ppo_reason: str = "",
    task: str = "exit_decision",
) -> str:
    return (
        "You are M. A. Halim — owned trading mind. Reply JSON only, no markdown.\n"
        '{"exit":true|false,"confidence":0.0-1.0,"reason":"max 10 words"}\n'
        f"TASK: {task} {ticker.upper()}\n"
        f"price={price:.4f} pnl_pct={pnl_pct:+.3f}% peak_pct={peak_pct:+.3f}% "
        f"stop={stop:.4f} target={target:.4f}\n"
        f"ppo_exit={ppo_exit} ppo_conf={ppo_conf:.2f} ppo_note={ppo_reason[:60]}\n"
        "exit=true to lock profit or cut loser; false to hold/trail while momentum lives."
    )


@dataclass
class _HalimSlot:
    ticker: str
    fingerprint: str
    seq: int
    submitted_at: float = 0.0
    completed_at: float = 0.0
    in_flight: bool = False
    parsed: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    source: str = ""


class HalimExitLine:
    """One async Halim LM slot per ticker — throttled for open-position manage."""

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._slots: Dict[str, _HalimSlot] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._stats = {"rung": 0, "fresh": 0, "stale": 0}

    def _halim_complete(self, prompt: str, *, task: str) -> str:
        try:
            from halim.client import complete
            out = complete(
                prompt,
                purpose="exit_decision",
                timeout=_exit_timeout_sec(),
            )
            if out and out.get("ok") and out.get("text"):
                return str(out["text"]).strip()
        except Exception as exc:
            log.debug(f"Halim exit LM: {exc}")
        return ""

    def _run(self, key: str, seq: int, prompt: str, *, task: str) -> None:
        raw = self._halim_complete(prompt, task=task)
        parsed = _parse_json_response(raw)
        with self._lock:
            slot = self._slots.get(key)
            if not slot or slot.seq != seq:
                return
            slot.in_flight = False
            slot.completed_at = time.time()
            slot.raw = raw[:500]
            slot.parsed = parsed
            slot.source = "halim_lm" if parsed else "halim_lm_empty"
        if parsed:
            try:
                from core.halim_action_learn import record_action
                record_action(
                    "decision_text",
                    task,
                    input_text=prompt[:800],
                    output_text=raw[:400],
                    outcome="exit" if parsed.get("exit") else "hold",
                    source="halim_exit_lm",
                    cfg=self.cfg,
                )
            except Exception:
                pass

    def ring(
        self,
        ticker: str,
        fingerprint: str,
        *,
        price: float,
        pnl_pct: float,
        peak_pct: float = 0.0,
        stop: float = 0.0,
        target: float = 0.0,
        ppo_exit: bool = False,
        ppo_conf: float = 0.5,
        ppo_reason: str = "",
        task: str = "exit_decision",
    ) -> None:
        if not halim_exit_lm_enabled(self.cfg):
            return
        key = ticker.upper()
        now = time.time()
        with self._lock:
            prev = self._slots.get(key)
            if prev:
                if prev.in_flight:
                    return
                if prev.fingerprint == fingerprint and (now - prev.submitted_at) < _min_ring_sec(self.cfg):
                    return
            self._seq += 1
            seq = self._seq
            self._slots[key] = _HalimSlot(
                ticker=key,
                fingerprint=fingerprint,
                seq=seq,
                submitted_at=now,
                in_flight=True,
            )
            self._stats["rung"] += 1
        prompt = _build_exit_prompt(
            ticker=key,
            price=price,
            pnl_pct=pnl_pct,
            peak_pct=peak_pct if peak_pct else pnl_pct,
            stop=stop,
            target=target,
            ppo_exit=ppo_exit,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            task=task,
        )
        threading.Thread(
            target=self._run,
            args=(key, seq, prompt),
            kwargs={"task": task},
            name=f"halim-exit-{key}",
            daemon=True,
        ).start()

    def consume(self, ticker: str, fingerprint: str) -> Dict[str, Any]:
        key = ticker.upper()
        with self._lock:
            slot = self._slots.get(key)
            if not slot:
                return {"status": "missing", "parsed": {}, "raw": ""}
            if slot.fingerprint != fingerprint:
                self._stats["stale"] += 1
                return {"status": "stale_context", "parsed": {}, "raw": ""}
            if slot.in_flight:
                return {"status": "in_flight", "parsed": {}, "raw": ""}
            age = time.time() - slot.completed_at if slot.completed_at else 999
            if age > _max_age_sec(self.cfg):
                return {"status": "stale", "parsed": {}, "raw": slot.raw}
            if not slot.parsed:
                return {"status": "empty", "parsed": {}, "raw": slot.raw}
            self._stats["fresh"] += 1
            return {
                "status": "fresh",
                "parsed": dict(slot.parsed),
                "raw": slot.raw,
                "source": slot.source,
                "latency_ms": (slot.completed_at - slot.submitted_at) * 1000 if slot.completed_at else 0,
            }


def merge_halim_exit_advisory(
    base: Dict[str, Any],
    halim_live: Dict[str, Any],
    *,
    ppo_exit: bool,
    ppo_conf: float,
    min_conf: float,
    cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    """Blend fresh Halim exit JSON into council/PPO exit decision."""
    cfg = cfg or BotConfig()
    out = dict(base)
    if halim_live.get("status") != "fresh":
        return out
    parsed = halim_live.get("parsed") or {}
    if not parsed:
        return out

    h_exit = bool(parsed.get("exit", False))
    h_conf = float(parsed.get("confidence", 0.5) or 0.5)
    h_reason = str(parsed.get("reason", ""))[:80]
    blend_w = float(os.getenv("HALIM_EXIT_BLEND_WEIGHT", "0.30"))
    soft_veto = os.getenv("HALIM_EXIT_SOFT_VETO", "true").lower() in ("1", "true", "yes")
    veto_conf = float(os.getenv("HALIM_EXIT_VETO_MIN_CONF", "0.85"))

    cur_exit = bool(out.get("exit", False))
    cur_conf = float(out.get("confidence", ppo_conf) or ppo_conf)
    agree = h_exit == cur_exit

    if agree and h_exit:
        cur_conf = min(0.99, cur_conf + blend_w * h_conf * 0.2)
        note = f"Halim exit agree {h_conf:.0%}"
    elif not agree and h_conf >= 0.65:
        cur_conf = max(0.0, cur_conf - blend_w * h_conf * 0.15)
        note = f"Halim exit caution {h_conf:.0%}: {h_reason}"
    else:
        note = f"Halim exit {h_conf:.0%}"

    if soft_veto and not h_exit and h_conf >= veto_conf and cur_exit and ppo_exit:
        out["exit"] = False
        out["reason"] = f"Halim soft hold {h_conf:.0%}: {h_reason}"[:200]
        out["pipeline"] = f"{out.get('pipeline', 'council')}:halim_exit_hold"
    elif soft_veto and h_exit and h_conf >= veto_conf and not cur_exit:
        out["exit"] = True
        out["reason"] = f"Halim exit push {h_conf:.0%}: {h_reason}"[:200]
        out["pipeline"] = f"{out.get('pipeline', 'council')}:halim_exit_push"
    else:
        prev = str(out.get("reason", ""))[:140]
        out["reason"] = f"{note} | {prev}"[:200] if prev else note
        pipe = str(out.get("pipeline", ""))
        out["pipeline"] = f"{pipe}+halim_exit" if pipe else "halim:exit_advisory"

    out["confidence"] = cur_conf
    out["halim_exit"] = h_exit
    out["halim_conf"] = round(h_conf, 4)
    out["halim_agree"] = agree
    out["halim_reason"] = h_reason
    return out
