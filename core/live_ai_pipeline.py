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
from typing import Any, Callable, Dict, List, Optional

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


def stagnation_fingerprint(
    ticker: str, price: float, pnl_pct: float, stagnant_sec: float,
) -> str:
    """Bucket stagnant time so Ollama isn't re-run every sub-second tick."""
    bucket = int(stagnant_sec // 15) * 15
    return f"{ticker}|{price:.4f}|{pnl_pct:.1f}|stag{bucket}"


def scan_fingerprint(ticker: str, price: float, vol_ratio: float) -> str:
    return f"{ticker}|scan|{price:.4f}|{vol_ratio:.2f}"


def rank_scan_fingerprint(tickers: List[str]) -> str:
    return f"rank|{'|'.join(tickers[:15])}"[:160]


def pick_target_fingerprint(skipped: str, candidates: List[str]) -> str:
    return f"pick|{skipped}|{'|'.join(sorted(candidates)[:12])}"[:160]


def risk_signal_fingerprint(ticker: str, price: float, signal: str) -> str:
    return f"{ticker}|risk|{price:.4f}|{signal}"


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

    def consume_wait(
        self,
        ticker: str,
        task: str,
        fingerprint: str,
        timeout_sec: float = 10.0,
        poll_sec: float = 0.05,
    ) -> Dict[str, Any]:
        """Poll until Ollama answer is fresh or timeout — for real entry/exit decisions."""
        deadline = time.time() + max(0.5, float(timeout_sec))
        last: Dict[str, Any] = {"status": "missing", "parsed": {}}
        while time.time() < deadline:
            last = self.consume(ticker, task, fingerprint)
            if last.get("status") == "fresh":
                return last
            if last.get("status") in ("missing", "stale_context", "empty", "expired"):
                break
            time.sleep(poll_sec)
        return last

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
                "raw": slot.raw or "",
                "age_sec": age,
                "latency_ms": slot.latency_ms,
                "seq": slot.seq,
            }

    def peek(self, ticker: str, task: str) -> Dict[str, Any]:
        """Latest Ollama slot for audit — does not require fresh fingerprint."""
        key = self._key(ticker, task)
        with self._lock:
            slot = self._slots.get(key)
            if not slot:
                return {"status": "missing", "parsed": {}, "raw": ""}
            return {
                "status": "in_flight" if slot.in_flight else ("ready" if slot.parsed else "empty"),
                "parsed": dict(slot.parsed),
                "raw": slot.raw or "",
                "fingerprint": slot.fingerprint,
                "latency_ms": slot.latency_ms,
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
    scan_score: float = 0.0,
    spike_ratio: float = 1.0,
) -> Dict[str, Any]:
    """
    Collaborative Ollama + PPO council — non-blocking.
    Returns pending=True while Ollama is still thinking; never skips on PPO alone mid-deliberation.
    """
    ppo_buy = ppo_action == 1
    base: Dict[str, Any] = {
        "enter": False,
        "pending": False,
        "confidence": ppo_conf,
        "reason": ppo_reason or "PPO signal",
        "journal": "",
        "pipeline": ollama_status,
        "ppo_action": ppo_action,
        "ppo_conf": ppo_conf,
        "ollama_status": ollama_status,
    }

    waiting = ollama_status in ("in_flight", "missing", "stale_context", "empty")
    if waiting:
        base.update({
            "pending": True,
            "pipeline": f"council:{ollama_status}",
            "reason": (
                f"PPO conf={ppo_conf:.0%} ({ppo_reason or 'neutral'}) — "
                f"awaiting Ollama ({ollama_status})"
            )[:200],
        })
        return base

    if ollama_status == "timeout":
        # Council timed out — PPO + scanner context only (no block, no hard skip)
        blend = ppo_conf
        if ppo_buy and ppo_conf >= min_conf:
            base.update({
                "enter": True,
                "confidence": ppo_conf,
                "pipeline": "council:ppo_timeout_lead",
                "reason": f"PPO lead after council timeout: {ppo_reason}"[:200],
            })
        elif spike_ratio >= 1.4 and scan_score >= 40 and ppo_conf >= min_conf * 0.7:
            base.update({
                "enter": True,
                "confidence": max(ppo_conf, 0.55),
                "pipeline": "council:scanner_timeout",
                "reason": (
                    f"Scanner+spike after timeout: score={scan_score:.0f} "
                    f"vol={spike_ratio:.1f}x"
                )[:200],
            })
        else:
            base["pipeline"] = "council:timeout_pass"
            base["reason"] = "Council timeout — no aligned signal"
        return base

    if ollama_status == "scanner_fast":
        if spike_ratio >= 1.25 and scan_score >= 75:
            base.update({
                "enter": True,
                "pending": False,
                "confidence": max(ppo_conf, 0.58, min(scan_score / 100.0, 0.85)),
                "pipeline": "council:scanner_fast",
                "reason": (
                    f"Fast scanner lead (Ollama slow): score={scan_score:.0f} "
                    f"vol={spike_ratio:.1f}x | PPO {ppo_conf:.0%}"
                )[:200],
            })
        else:
            base["pipeline"] = "council:scanner_fast_pass"
            base["reason"] = "Scanner fast-path: signal not strong enough"
        return base

    if ollama_status == "fresh" and ollama:
        o_enter = bool(ollama.get("enter", ppo_buy))
        o_conf = float(ollama.get("confidence", ppo_conf) or ppo_conf)
        gut = float(ollama.get("gut_feel", 0.5) or 0.5)
        blend_conf = max(o_conf, ppo_conf) if (o_enter and ppo_buy) else (
            o_conf if o_enter else ppo_conf * 0.85
        )

        enter = False
        reason = ""
        if o_enter and ppo_buy:
            enter = True
            reason = f"council aligned: Ollama {o_conf:.0%} + PPO {ppo_conf:.0%}"
        elif o_enter and o_conf >= min_conf * 0.85:
            enter = True
            reason = f"Ollama pilot {o_conf:.0%} (PPO {'buy' if ppo_buy else 'hold'})"
        elif ppo_buy and ppo_conf >= min_conf and (o_enter or o_conf >= min_conf * 0.55):
            enter = True
            reason = f"PPO {ppo_conf:.0%} + Ollama assent {o_conf:.0%}"
        elif ppo_buy and ppo_conf >= min_conf and not ollama.get("enter") is False:
            enter = True
            reason = f"PPO lead {ppo_conf:.0%} | Ollama: {str(ollama.get('reason', ''))[:60]}"
        elif gut >= 0.72 and spike_ratio >= 1.3 and scan_score >= 35:
            enter = True
            blend_conf = max(blend_conf, gut)
            reason = f"gut+scanner: feel={gut:.0%} spike={spike_ratio:.1f}x"
        else:
            reason = (
                f"council pass: Ollama enter={o_enter} conf={o_conf:.0%} | "
                f"PPO buy={ppo_buy} conf={ppo_conf:.0%}"
            )[:200]

        base.update({
            "enter": enter,
            "pending": False,
            "confidence": blend_conf,
            "gut_feel": gut,
            "intuition": str(ollama.get("intuition", ""))[:120],
            "reason": reason or str(ollama.get("reason", ""))[:200],
            "journal": str(ollama.get("journal", ""))[:300],
            "pipeline": "council:ollama+ppo",
            "council_agreement": bool(o_enter and ppo_buy),
        })
        return base

    # Expired / other — reopen council next tick
    base.update({
        "pending": True,
        "pipeline": f"council:{ollama_status}",
        "reason": f"Ollama {ollama_status} — council still open",
    })
    return base


def _council_pending_base(
    ollama_status: str,
    ppo_label: str,
    ppo_conf: float,
    ppo_reason: str,
) -> Dict[str, Any]:
    return {
        "pending": True,
        "pipeline": f"council:{ollama_status}",
        "reason": (
            f"{ppo_label} conf={ppo_conf:.0%} ({ppo_reason or 'neutral'}) — "
            f"awaiting Ollama ({ollama_status})"
        )[:200],
    }


def merge_exit_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    ppo_exit: bool,
    ppo_conf: float,
    ppo_reason: str,
    min_conf: float,
    pnl_pct: float = 0.0,
) -> Dict[str, Any]:
    """Collaborative exit council — non-blocking."""
    base: Dict[str, Any] = {
        "exit": False,
        "pending": False,
        "confidence": ppo_conf,
        "reason": ppo_reason or "PPO exit check",
        "journal": "",
        "pipeline": ollama_status,
        "council_agreement": False,
    }
    waiting = ollama_status in ("in_flight", "missing", "stale_context", "empty")
    if waiting:
        base.update(_council_pending_base(ollama_status, "PPO exit", ppo_conf, ppo_reason))
        return base
    if ollama_status == "timeout":
        if ppo_exit and ppo_conf >= min_conf:
            base.update({
                "exit": True,
                "confidence": ppo_conf,
                "pipeline": "council:ppo_timeout_exit",
                "reason": f"PPO exit after council timeout: {ppo_reason}"[:200],
            })
        elif pnl_pct <= -1.0 and ppo_conf >= min_conf * 0.8:
            base.update({
                "exit": True,
                "confidence": max(ppo_conf, 0.55),
                "pipeline": "council:loss_timeout_exit",
                "reason": f"Loss cut after timeout P&L {pnl_pct:+.2f}%",
            })
        else:
            base.update({
                "pipeline": "council:timeout_hold",
                "reason": "Exit council timeout — hold",
            })
        return base
    if ollama_status == "fresh" and ollama:
        o_exit = bool(ollama.get("exit", ppo_exit))
        o_conf = float(ollama.get("confidence", ppo_conf) or ppo_conf)
        gut = float(ollama.get("gut_feel", 0.5) or 0.5)
        should_exit = False
        reason = ""
        if o_exit and ppo_exit:
            should_exit = True
            reason = f"council aligned exit: Ollama {o_conf:.0%} + PPO {ppo_conf:.0%}"
        elif o_exit and o_conf >= min_conf * 0.85:
            should_exit = True
            reason = f"Ollama exit {o_conf:.0%} (PPO {'exit' if ppo_exit else 'hold'})"
        elif ppo_exit and ppo_conf >= min_conf and (o_exit or o_conf >= min_conf * 0.55):
            should_exit = True
            reason = f"PPO exit {ppo_conf:.0%} + Ollama assent {o_conf:.0%}"
        elif gut <= 0.28 and pnl_pct < 0:
            should_exit = True
            reason = f"gut exit: feel={gut:.0%} P&L {pnl_pct:+.2f}%"
        else:
            reason = (
                f"council hold: Ollama exit={o_exit} conf={o_conf:.0%} | "
                f"PPO exit={ppo_exit} conf={ppo_conf:.0%}"
            )[:200]
        base.update({
            "exit": should_exit,
            "confidence": max(o_conf, ppo_conf) if should_exit else ppo_conf,
            "gut_feel": gut,
            "reason": reason or str(ollama.get("reason", ""))[:200],
            "journal": str(ollama.get("journal", ""))[:300],
            "pipeline": "council:ollama+ppo",
            "council_agreement": bool(o_exit and ppo_exit),
        })
        return base
    base.update(_council_pending_base(ollama_status, "PPO exit", ppo_conf, ppo_reason))
    return base


def merge_position_manage_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    ppo_exit: bool,
    ppo_conf: float,
    ppo_reason: str,
    min_conf: float,
    *,
    pnl_pct: float = 0.0,
    peak_pct: float = 0.0,
    current_stop: float = 0.0,
    current_target: float = 0.0,
    mechanical_stop: Optional[float] = None,
    mechanical_target: Optional[float] = None,
) -> Dict[str, Any]:
    """Trail stop / profit-take / hold — Ollama + PPO council."""
    base: Dict[str, Any] = {
        "action": "HOLD",
        "pending": False,
        "stop": current_stop,
        "target": current_target,
        "confidence": ppo_conf,
        "reason": ppo_reason or "PPO position manage",
        "journal": "",
        "pipeline": ollama_status,
        "council_agreement": False,
    }
    waiting = ollama_status in ("in_flight", "missing", "stale_context", "empty")
    if waiting:
        base.update(_council_pending_base(ollama_status, "PPO manage", ppo_conf, ppo_reason))
        return base
    if ollama_status == "timeout":
        if ppo_exit and ppo_conf >= min_conf:
            base.update({
                "action": "EXIT",
                "confidence": ppo_conf,
                "pipeline": "council:ppo_timeout_exit",
                "reason": f"PPO exit after manage timeout: {ppo_reason}"[:200],
            })
        elif mechanical_stop and mechanical_stop > current_stop + 0.0001:
            base.update({
                "action": "TIGHTEN_STOP",
                "stop": mechanical_stop,
                "confidence": ppo_conf,
                "pipeline": "council:mechanical_trail_timeout",
                "reason": f"Mechanical trail stop +{peak_pct:.2%}",
            })
        elif mechanical_target and mechanical_target > current_target + 0.0001:
            base.update({
                "action": "RAISE_TP",
                "target": mechanical_target,
                "confidence": ppo_conf,
                "pipeline": "council:mechanical_tp_timeout",
                "reason": "Mechanical TP extension after timeout",
            })
        else:
            base.update({
                "pipeline": "council:timeout_hold",
                "reason": "Manage council timeout — hold",
            })
        return base
    if ollama_status == "fresh" and ollama:
        o_action = str(ollama.get("action", "HOLD")).upper()
        if o_action not in ("HOLD", "WIDEN_STOP", "TIGHTEN_STOP", "RAISE_TP", "EXIT"):
            o_action = "HOLD"
        o_conf = float(ollama.get("confidence", ppo_conf) or ppo_conf)
        gut = float(ollama.get("gut_feel", 0.5) or 0.5)
        action = "HOLD"
        reason = ""
        if o_action == "EXIT" and ppo_exit:
            action = "EXIT"
            reason = f"council aligned EXIT: Ollama {o_conf:.0%} + PPO {ppo_conf:.0%}"
        elif o_action == "EXIT" and o_conf >= min_conf * 0.85:
            action = "EXIT"
            reason = f"Ollama EXIT {o_conf:.0%}"
        elif ppo_exit and ppo_conf >= min_conf:
            action = "EXIT"
            reason = f"PPO EXIT {ppo_conf:.0%} | Ollama: {o_action}"
        elif o_action == "TIGHTEN_STOP" or (
            mechanical_stop and mechanical_stop > current_stop + 0.0001 and ppo_conf >= min_conf * 0.7
        ):
            action = "TIGHTEN_STOP"
            reason = f"trail stop: Ollama {o_action} + peak +{peak_pct:.2%}"
        elif o_action == "RAISE_TP" or (
            mechanical_target and mechanical_target > current_target + 0.0001
        ):
            action = "RAISE_TP"
            reason = f"profit take: Ollama {o_action} P&L {pnl_pct:+.2f}%"
        elif o_action == "WIDEN_STOP":
            action = "WIDEN_STOP"
            reason = f"Ollama widen stop (noise cushion) conf={o_conf:.0%}"
        elif gut >= 0.7 and peak_pct > 0.01 and pnl_pct > 0:
            action = "RAISE_TP"
            reason = f"gut extend TP feel={gut:.0%}"
        else:
            reason = (
                f"council hold: Ollama {o_action} conf={o_conf:.0%} | "
                f"PPO exit={ppo_exit} conf={ppo_conf:.0%}"
            )[:200]
        base.update({
            "action": action,
            "confidence": max(o_conf, ppo_conf),
            "gut_feel": gut,
            "reason": reason or str(ollama.get("reason", ""))[:200],
            "journal": str(ollama.get("journal", ""))[:300],
            "pipeline": "council:ollama+ppo",
            "council_agreement": bool(o_action == "EXIT" and ppo_exit) or (
                o_action in ("TIGHTEN_STOP", "RAISE_TP") and not ppo_exit
            ),
        })
        return base
    base.update(_council_pending_base(ollama_status, "PPO manage", ppo_conf, ppo_reason))
    return base


def _parse_manage_price(val: Any, default: float) -> float:
    try:
        if val is None:
            return float(default)
        return float(val)
    except (TypeError, ValueError):
        return float(default)


def merge_stagnation_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    ppo_exit: bool,
    ppo_conf: float,
    ppo_reason: str,
    min_conf: float,
    stagnant_sec: float,
    stagnation_sec: float,
) -> Dict[str, Any]:
    """Collaborative stagnation / dead-trade council — non-blocking."""
    base: Dict[str, Any] = {
        "exit": False,
        "pending": False,
        "confidence": ppo_conf,
        "reason": ppo_reason or "PPO stagnation check",
        "journal": "",
        "force_snapshot": False,
        "pulse_verbose": stagnant_sec >= stagnation_sec * 0.5,
        "pipeline": ollama_status,
        "council_agreement": False,
    }

    waiting = ollama_status in ("in_flight", "missing", "stale_context", "empty")
    if waiting:
        base.update(_council_pending_base(ollama_status, "PPO stagnation", ppo_conf, ppo_reason))
        base["pulse_verbose"] = stagnant_sec >= stagnation_sec * 0.4
        return base

    if ollama_status == "timeout":
        if ppo_exit and ppo_conf >= min_conf:
            base.update({
                "exit": True,
                "confidence": ppo_conf,
                "pipeline": "council:ppo_stagnation_timeout",
                "reason": f"PPO stagnation exit after timeout: {ppo_reason}"[:200],
            })
        elif stagnant_sec >= stagnation_sec:
            base.update({
                "exit": True,
                "confidence": max(ppo_conf, 0.55),
                "pipeline": "council:stagnation_timeout",
                "reason": f"Stagnation timeout {stagnant_sec:.0f}s",
            })
        else:
            base.update({
                "pipeline": "council:timeout_hold",
                "reason": "Stagnation council timeout — hold",
            })
        return base

    if ollama_status == "fresh" and ollama:
        should_exit = bool(ollama.get("exit", ppo_exit))
        conf = float(ollama.get("confidence", ppo_conf) or ppo_conf)
        gut = float(ollama.get("gut_feel", 0.5) or 0.5)
        reason = ""
        if should_exit and ppo_exit:
            reason = f"council aligned stagnation exit: Ollama {conf:.0%} + PPO {ppo_conf:.0%}"
        elif should_exit and conf >= min_conf * 0.85:
            reason = f"Ollama dead-trade exit {conf:.0%}"
        elif ppo_exit and ppo_conf >= min_conf and (should_exit or conf >= min_conf * 0.55):
            should_exit = True
            reason = f"PPO stagnation {ppo_conf:.0%} + Ollama assent {conf:.0%}"
        elif gut < 0.35 and not should_exit and stagnant_sec >= stagnation_sec * 0.75:
            should_exit = True
            conf = max(conf, 0.55)
            reason = f"gut dead-trade: feel={gut:.0%} stagnant {stagnant_sec:.0f}s"
        elif gut > 0.65 and should_exit:
            conf = max(conf, gut)
            reason = f"gut confirms exit feel={gut:.0%}"
        else:
            should_exit = False
            reason = (
                f"council hold: Ollama exit={should_exit} conf={conf:.0%} | "
                f"PPO exit={ppo_exit} conf={ppo_conf:.0%}"
            )[:200]
        base.update({
            "exit": should_exit,
            "confidence": conf,
            "gut_feel": gut,
            "intuition": str(ollama.get("intuition", ""))[:120],
            "reason": reason or str(ollama.get("reason", ""))[:200],
            "journal": str(ollama.get("journal", ""))[:300],
            "force_snapshot": bool(ollama.get("force_snapshot", False)),
            "pulse_verbose": bool(ollama.get("pulse_verbose", base["pulse_verbose"])),
            "pipeline": "council:ollama+ppo",
            "council_agreement": bool(should_exit and ppo_exit),
        })
        return base

    base.update(_council_pending_base(ollama_status, "PPO stagnation", ppo_conf, ppo_reason))
    return base


def merge_scan_score_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    rule_score: float,
    rule_reason: str,
    ppo_bias: float = 0.5,
) -> Dict[str, Any]:
    """Scanner score council — rule+PPO hint + Ollama."""
    base: Dict[str, Any] = {
        "score": rule_score,
        "pending": False,
        "reasons": rule_reason,
        "pipeline": ollama_status,
        "confidence": ppo_bias,
    }
    waiting = ollama_status in ("in_flight", "missing", "stale_context", "empty")
    if waiting:
        base.update({
            "pending": True,
            "pipeline": f"council:{ollama_status}",
            "reasons": f"rule {rule_score:.0f} — awaiting Ollama ({ollama_status})",
        })
        return base
    if ollama_status == "timeout":
        base["pipeline"] = "council:rule_timeout"
        return base
    if ollama_status == "fresh" and ollama:
        o_score = float(ollama.get("score", rule_score) or rule_score)
        blend = max(o_score, rule_score * 0.85) if ollama.get("enter_bias") else (o_score + rule_score) / 2
        base.update({
            "score": round(blend, 1),
            "reasons": str(ollama.get("reasons", rule_reason))[:200],
            "confidence": float(ollama.get("confidence", ppo_bias) or ppo_bias),
            "pipeline": "council:ollama+scanner",
        })
        return base
    base["pending"] = True
    return base


def merge_rank_scan_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Re-rank scan pool — council deliberation."""
    base: Dict[str, Any] = {
        "pending": False,
        "ranked": [r["ticker"] for r in results],
        "pipeline": ollama_status,
    }
    waiting = ollama_status in ("in_flight", "missing", "stale_context", "empty")
    if waiting:
        base.update({
            "pending": True,
            "pipeline": f"council:{ollama_status}",
            "reason": "awaiting Ollama rank",
        })
        return base
    if ollama_status == "timeout":
        base["pipeline"] = "council:rule_rank_timeout"
        return base
    if ollama_status == "fresh" and ollama:
        ranked = ollama.get("ranked") or []
        if ranked:
            base["ranked"] = [str(t) for t in ranked]
            base["pipeline"] = "council:ollama+scanner"
            base["reason"] = str(ollama.get("reason", ""))[:200]
        return base
    base["pending"] = True
    return base


def merge_pick_target_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    tickers: List[str],
    scores: Dict[str, float],
    ppo_pick: str = "",
) -> Dict[str, Any]:
    """Pick next focus ticker after skip/reject."""
    fallback = ppo_pick or (max(tickers, key=lambda t: scores.get(t, 0)) if tickers else "")
    base: Dict[str, Any] = {
        "pending": False,
        "ticker": fallback,
        "pipeline": ollama_status,
    }
    waiting = ollama_status in ("in_flight", "missing", "stale_context", "empty")
    if waiting:
        base.update({
            "pending": True,
            "pipeline": f"council:{ollama_status}",
            "reason": f"PPO pick {fallback} — awaiting Ollama",
        })
        return base
    if ollama_status == "timeout":
        base["pipeline"] = "council:ppo_pick_timeout"
        return base
    if ollama_status == "fresh" and ollama:
        pick = str(ollama.get("ticker", "") or "")
        if pick in tickers:
            base["ticker"] = pick
            base["reason"] = str(ollama.get("reason", ""))[:200]
            base["pipeline"] = "council:ollama+ppo"
        return base
    base["pending"] = True
    return base


def merge_risk_signal_decision(
    ollama: Dict[str, Any],
    ollama_status: str,
    risk_signal: str,
    ppo_exit: bool,
    ppo_conf: float,
    ppo_reason: str,
    min_conf: float,
    pnl_pct: float = 0.0,
) -> Dict[str, Any]:
    """Risk-engine signal (trail profit/stop, early loss) via exit council."""
    merged = merge_exit_decision(
        ollama, ollama_status, ppo_exit or bool(risk_signal), ppo_conf,
        ppo_reason or risk_signal, min_conf, pnl_pct=pnl_pct,
    )
    if risk_signal and merged.get("exit"):
        merged["reason"] = f"risk:{risk_signal} | {merged.get('reason', '')}"[:200]
        merged["pipeline"] = f"council:risk+{merged.get('pipeline', 'exit')}"
    elif risk_signal and not merged.get("pending"):
        merged["pipeline"] = f"council:risk_hold:{risk_signal}"
    return merged
