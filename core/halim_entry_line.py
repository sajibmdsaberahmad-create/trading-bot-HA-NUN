#!/usr/bin/env python3
"""
core/halim_entry_line.py — Non-blocking Halim LM entry advisory (learn by doing).

Fires short JSON-only inference via halim serve (MLX). Never blocks IB/replay loop.
Participates in entry blend when fresh; always records action gold for training.
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


def halim_entry_lm_enabled(cfg: Optional[BotConfig] = None) -> bool:
    if os.getenv("HALIM_ENTRY_LM_ENABLED", "true").lower() not in ("1", "true", "yes"):
        return False
    try:
        from core.halim_unlock import is_usable
        if not is_usable("decision_text", cfg):
            return False
    except Exception:
        pass
    return True


def _entry_timeout_sec() -> float:
    return float(os.getenv("HALIM_ENTRY_LM_TIMEOUT_SEC", "6"))


def _max_age_sec(cfg: BotConfig) -> float:
    return float(os.getenv("HALIM_ENTRY_LM_MAX_AGE_SEC", "5"))


def _min_ring_sec(cfg: BotConfig) -> float:
    return float(os.getenv("HALIM_ENTRY_LM_MIN_RING_SEC", "2.0"))


def _build_entry_prompt(
    *,
    ticker: str,
    price: float,
    spike: float,
    scan: float,
    ppo_buy: bool,
    ppo_conf: float,
    ppo_reason: str = "",
    loss_context: str = "",
) -> str:
    loss_line = f"\n{loss_context}\n" if loss_context else ""
    return (
        "You are M. A. Halim — owned trading mind. Reply JSON only, no markdown.\n"
        '{"enter":true|false,"confidence":0.0-1.0,"reason":"max 10 words"}\n'
        f"TASK: entry_decision {ticker.upper()}\n"
        f"price={price:.4f} vol_spike={spike:.2f}x scan_score={scan:.0f}\n"
        f"ppo_buy={ppo_buy} ppo_conf={ppo_conf:.2f} ppo_note={ppo_reason[:60]}\n"
        f"{loss_line}"
        "enter=true only on clean momentum scalp; false on chop/fakeout. "
        "If session loss memory present, skip unless setup clearly changed."
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


class HalimEntryLine:
    """One async Halim LM slot per ticker — serialized to protect 8GB RAM."""

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._slots: Dict[str, _HalimSlot] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._stats = {"rung": 0, "fresh": 0, "stale": 0}

    def _halim_complete(self, prompt: str) -> str:
        try:
            from halim.client import complete
            out = complete(
                prompt,
                purpose="entry_decision",
                timeout=_entry_timeout_sec(),
            )
            if out and out.get("ok") and out.get("text"):
                return str(out["text"]).strip()
        except Exception as exc:
            log.debug(f"Halim entry LM: {exc}")
        return ""

    def _run(self, key: str, seq: int, prompt: str) -> None:
        raw = self._halim_complete(prompt)
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
                    "entry_decision",
                    input_text=prompt[:800],
                    output_text=raw[:400],
                    outcome="enter" if parsed.get("enter") else "skip",
                    source="halim_entry_lm",
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
        spike: float,
        scan: float,
        ppo_buy: bool,
        ppo_conf: float,
        ppo_reason: str = "",
    ) -> None:
        if not halim_entry_lm_enabled(self.cfg):
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
        loss_ctx = ""
        try:
            from core.live_trade_guard import loss_context_for_prompt
            loss_ctx = loss_context_for_prompt(key)
        except Exception:
            pass
        prompt = _build_entry_prompt(
            ticker=key,
            price=price,
            spike=spike,
            scan=scan,
            ppo_buy=ppo_buy,
            ppo_conf=ppo_conf,
            ppo_reason=ppo_reason,
            loss_context=loss_ctx,
        )
        threading.Thread(
            target=self._run,
            args=(key, seq, prompt),
            name=f"halim-entry-{key}",
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

    def peek(self, ticker: str) -> Dict[str, Any]:
        key = ticker.upper()
        with self._lock:
            slot = self._slots.get(key)
            if not slot:
                return {"status": "missing", "parsed": {}, "raw": ""}
            return {
                "status": "in_flight" if slot.in_flight else "ready",
                "parsed": dict(slot.parsed),
                "raw": slot.raw,
                "fingerprint": slot.fingerprint,
            }


def merge_halim_entry_advisory(
    base: Dict[str, Any],
    halim_live: Dict[str, Any],
    *,
    ticker: str = "",
    ppo_buy: bool,
    ppo_conf: float,
    min_conf: float,
    cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    """Blend fresh Halim JSON advisory into council/PPO decision — never hard-veto by default."""
    cfg = cfg or BotConfig()
    out = dict(base)
    if halim_live.get("status") != "fresh":
        return out
    parsed = halim_live.get("parsed") or {}
    if not parsed:
        return out

    h_enter = bool(parsed.get("enter", False))
    h_conf = float(parsed.get("confidence", 0.5) or 0.5)
    h_reason = str(parsed.get("reason", ""))[:80]
    blend_w = float(os.getenv("HALIM_ENTRY_BLEND_WEIGHT", "0.30"))
    soft_veto = os.getenv("HALIM_ENTRY_SOFT_VETO", "true").lower() in ("1", "true", "yes")
    veto_conf = float(os.getenv("HALIM_ENTRY_VETO_MIN_CONF", "0.85"))
    try:
        from core.live_trade_guard import session_loss_count
        losses = session_loss_count(ticker)
        if losses >= 2:
            veto_conf = min(
                veto_conf,
                float(os.getenv("HALIM_ENTRY_REPEAT_LOSER_VETO", "0.72")),
            )
        if losses >= 4:
            veto_conf = min(veto_conf, 0.65)
    except Exception:
        pass

    cur_conf = float(out.get("confidence", ppo_conf) or ppo_conf)
    agree = h_enter == ppo_buy

    if agree and h_enter:
        cur_conf = min(0.99, cur_conf + blend_w * h_conf * 0.2)
        note = f"Halim agrees {h_conf:.0%}"
    elif not agree and h_conf >= 0.65:
        cur_conf = max(0.0, cur_conf - blend_w * h_conf * 0.15)
        note = f"Halim caution {h_conf:.0%}: {h_reason}"
    else:
        note = f"Halim {h_conf:.0%}"

    if soft_veto and not h_enter and h_conf >= veto_conf and ppo_buy and out.get("enter"):
        out["enter"] = False
        out["reason"] = f"Halim soft skip {h_conf:.0%}: {h_reason}"[:200]
        out["pipeline"] = f"{out.get('pipeline', 'council')}:halim_veto"
    else:
        prev = str(out.get("reason", ""))[:140]
        out["reason"] = f"{note} | {prev}"[:200] if prev else note
        pipe = str(out.get("pipeline", ""))
        out["pipeline"] = f"{pipe}+halim" if pipe else "halim:advisory"

    out["confidence"] = cur_conf
    out["halim_enter"] = h_enter
    out["halim_conf"] = round(h_conf, 4)
    out["halim_agree"] = agree
    out["halim_reason"] = h_reason
    return out
