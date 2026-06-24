#!/usr/bin/env python3
"""
core/live_ai_pipeline.py — Dedicated async hotline for Ollama + PPO.

Ollama ALWAYS participates in live decisions but NEVER blocks the IB loop.
Like an open phone line: both brains stay in sync; latest context wins;
stale answers are discarded (no TTL cache of old decisions).

Flow:
  1. ring()  — fire Ollama async for current market fingerprint (non-blocking)
  2. consume() — read result ONLY if fingerprint matches and answer is fresh
  3. PPO runs synchronously every tick; Ollama catches up in parallel
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from core.config import BotConfig
from core.notify import log


def _parse_json_response(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        import json
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {}


def entry_fingerprint(ticker: str, price: float, spike: float, scan: float) -> str:
    return f"{ticker}|{price:.4f}|{spike:.2f}|{int(scan)}"


def position_fingerprint(ticker: str, price: float, pnl_pct: float, stop: float, target: float) -> str:
    return f"{ticker}|{price:.4f}|{pnl_pct:.1f}|{stop:.4f}|{target:.4f}"


def exit_fingerprint(ticker: str, price: float, pnl_pct: float) -> str:
    return f"{ticker}|{price:.4f}|{pnl_pct:.1f}"


def scan_fingerprint(ticker: str, price: float, vol_ratio: float) -> str:
    return f"{ticker}|scan|{price:.4f}|{vol_ratio:.2f}"


@dataclass
class LiveSlot:
    ticker: str
    task: str
    fingerprint: str
    seq: int
    submitted_at: float
    completed_at: float = 0.0
    parsed: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    in_flight: bool = False
    latency_ms: float = 0.0


class LiveAILine:
    """
    Per-ticker async Ollama hotline — always ringing, never blocking.
    """

    def __init__(self, cfg: BotConfig, decide_fn: Callable[[str], str]):
        self.cfg = cfg
        self._decide_fn = decide_fn
        self._slots: Dict[str, LiveSlot] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._stats = {"rung": 0, "completed": 0, "stale_drops": 0, "fresh_hits": 0}

    def _key(self, ticker: str, task: str) -> str:
        return f"{ticker}:{task}"

    def _max_age(self) -> float:
        return float(getattr(self.cfg, "LIVE_AI_MAX_AGE_SEC", 4.0))

    def _min_ring_interval(self) -> float:
        return float(getattr(self.cfg, "LIVE_AI_MIN_RING_SEC", 0.8))

    def _should_ring(self, key: str, fingerprint: str) -> bool:
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                return True
            if slot.fingerprint != fingerprint:
                return True
            if slot.in_flight:
                return False
            if slot.completed_at and (time.time() - slot.completed_at) < self._min_ring_interval():
                return False
            return True

    def ring(self, ticker: str, task: str, full_prompt: str, fingerprint: str) -> bool:
        """Start async Ollama call — returns immediately."""
        if not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return False
        key = self._key(ticker, task)
        if not self._should_ring(key, fingerprint):
            return False

        with self._lock:
            self._seq += 1
            seq = self._seq
            slot = LiveSlot(
                ticker=ticker,
                task=task,
                fingerprint=fingerprint,
                seq=seq,
                submitted_at=time.time(),
                in_flight=True,
            )
            self._slots[key] = slot
            self._stats["rung"] += 1

        def _worker():
            start = time.time()
            try:
                raw = (self._decide_fn(full_prompt) or "").strip()
            except Exception as exc:
                log.debug(f"LiveAI ring failed {ticker}/{task}: {exc}")
                raw = ""
            parsed = _parse_json_response(raw)
            elapsed_ms = (time.time() - start) * 1000
            with self._lock:
                current = self._slots.get(key)
                if current is None or current.seq != seq:
                    self._stats["stale_drops"] += 1
                    return
                current.completed_at = time.time()
                current.parsed = parsed
                current.raw = raw
                current.in_flight = False
                current.latency_ms = elapsed_ms
                self._stats["completed"] += 1
                if parsed:
                    log.debug(
                        f"📞 LiveAI {ticker}/{task} ready {elapsed_ms:.0f}ms | "
                        f"fp={fingerprint[:40]}"
                    )

        try:
            from core.async_utils import get_background_worker
            get_background_worker()._executor.submit(_worker)
        except Exception as exc:
            log.debug(f"LiveAI submit: {exc}")
            with self._lock:
                s = self._slots.get(key)
                if s and s.seq == seq:
                    s.in_flight = False
            return False
        return True

    def consume(self, ticker: str, task: str, fingerprint: str) -> Dict[str, Any]:
        """
        Non-blocking read. Returns parsed JSON only when:
        - fingerprint matches current market context
        - Ollama finished
        - answer is younger than LIVE_AI_MAX_AGE_SEC
        """
        key = self._key(ticker, task)
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                return {"status": "missing", "parsed": {}}
            if slot.fingerprint != fingerprint:
                return {"status": "stale_context", "parsed": {}, "in_flight": slot.in_flight}
            if slot.in_flight:
                age = time.time() - slot.submitted_at
                return {"status": "in_flight", "parsed": {}, "age_sec": age, "seq": slot.seq}
            if not slot.parsed:
                return {"status": "empty", "parsed": {}}
            age = time.time() - slot.completed_at
            if age > self._max_age():
                return {"status": "expired", "parsed": {}, "age_sec": age}
            self._stats["fresh_hits"] += 1
            return {
                "status": "fresh",
                "parsed": dict(slot.parsed),
                "age_sec": age,
                "latency_ms": slot.latency_ms,
                "seq": slot.seq,
            }

    def status(self, ticker: str, task: str) -> Dict[str, Any]:
        key = self._key(ticker, task)
        with self._lock:
            slot = self._slots.get(key)
            if not slot:
                return {"active": False}
            return {
                "active": True,
                "in_flight": slot.in_flight,
                "fingerprint": slot.fingerprint,
                "age_sec": time.time() - (slot.completed_at or slot.submitted_at),
                "has_result": bool(slot.parsed),
            }

    def stats(self) -> Dict[str, Any]:
        return dict(self._stats)


def merge_entry_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    ppo_action: int,
    ppo_conf: float,
    ppo_reason: str,
    min_conf: float,
) -> Dict[str, Any]:
    """Blend live Ollama + PPO — both always contribute when fresh."""
    base: Dict[str, Any] = {
        "enter": False,
        "confidence": ppo_conf,
        "reason": ppo_reason or "PPO signal",
        "journal": "",
        "pipeline": ollama_status,
    }

    if ollama_status == "fresh" and ollama:
        enter = bool(ollama.get("enter", ppo_action == 1))
        conf = float(ollama.get("confidence", ppo_conf) or ppo_conf)
        base.update({
            "enter": enter,
            "confidence": conf,
            "gut_feel": float(ollama.get("gut_feel", 0.5) or 0.5),
            "intuition": str(ollama.get("intuition", ""))[:120],
            "reason": str(ollama.get("reason", ""))[:200] or base["reason"],
            "journal": str(ollama.get("journal", ""))[:300],
            "shares": ollama.get("shares"),
            "stop": ollama.get("stop"),
            "target": ollama.get("target"),
            "pipeline": "ollama+ppo",
        })
        if ppo_action == 1 and ppo_conf >= min_conf and not enter:
            base["enter"] = True
            base["confidence"] = max(conf, ppo_conf)
            base["reason"] = f"PPO+Ollama ensemble: {ppo_reason} | {base['reason']}"[:220]
        elif enter and ppo_action == 1:
            base["confidence"] = max(conf, ppo_conf)
            base["reason"] = f"Ollama+PPO aligned: {base['reason']}"[:220]
        return base

    # Ollama in-flight or not ready — PPO leads, hotline still open
    if ppo_action == 1 and ppo_conf >= min_conf:
        base["enter"] = True
        base["confidence"] = ppo_conf
        tag = "PPO lead (Ollama in-flight)" if ollama_status == "in_flight" else "PPO lead (Ollama catching up)"
        base["reason"] = f"{tag}: {ppo_reason}"[:200]
        base["pipeline"] = f"ppo_lead:{ollama_status}"
    else:
        base["pipeline"] = f"ppo_only:{ollama_status}"
    return base
