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
    return float(os.getenv("HALIM_ENTRY_LM_MIN_RING_SEC", "1.0"))


def halim_entry_await_sec(cfg: Optional[BotConfig] = None) -> float:
    """Seconds to wait for async Halim entry LM before fast paths (replay + live)."""
    cfg = cfg or BotConfig()
    if os.getenv("HALIM_ENTRY_AWAIT_ENABLED", "true").lower() not in ("1", "true", "yes"):
        return 0.0
    try:
        sec = float(os.getenv("HALIM_ENTRY_AWAIT_SEC", "0"))
    except (TypeError, ValueError):
        sec = 0.0
    if sec <= 0:
        return 0.0
    replay = os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
    if replay and os.getenv("HALIM_ENTRY_AWAIT_REPLAY", "true").lower() in ("1", "true", "yes"):
        return sec
    if not replay and os.getenv("HALIM_ENTRY_AWAIT_LIVE", "true").lower() in ("1", "true", "yes"):
        return sec
    return 0.0


def _safe_confidence_value(raw: str) -> Optional[float]:
    """Parse confidence token; tolerate toddler trailing punctuation (e.g. 0.54.)."""
    import re

    s = (raw or "").strip().rstrip(".,;:%)]}")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if not m:
            return None
        try:
            v = float(m.group(1))
        except ValueError:
            return None
    return v / 100.0 if v > 1.0 else v


def _extract_echo_confidence(text: str) -> Optional[float]:
    import re
    for pat in (
        r"ppo_conf\s*=\s*(\d+(?:\.\d+)?)",
        r"(?:^|[\s{])conf\s*=\s*(\d+(?:\.\d+)?)",
        r"ppo\s*=\s*(\d+(?:\.\d+)?)",
        r"ppo\s+(\d+(?:\.\d+)?)\s*%",
        r"PPO\s+(\d+(?:\.\d+)?)\s*%",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return _safe_confidence_value(m.group(1))
    return None


def _parse_training_echo_entry(text: str) -> Optional[Dict[str, Any]]:
    """
    Toddler model often regurgitates PPO/council training lines instead of JSON.
    Treat as weak Halim advisory so await/blend/coevolution can participate.
    """
    import re

    low = text.lower()
    markers = (
        "ppo-led micro-fast",
        "ppo_led",
        "atr r:r",
        "ollama logging async",
        "entry_decision:",
        "entry_decision=",
        "ppo=hold",
        "ppo_conf=",
        "ppo_note=",
    )
    if not any(m in low for m in markers):
        return None

    conf = _extract_echo_confidence(text)
    enter: Optional[bool] = None

    if re.search(r"ppo_buy\s*=\s*true", text, re.I):
        enter = True
    elif re.search(r"ppo_buy\s*=\s*false", text, re.I) or re.search(
        r"ppo\s*=\s*hold", low
    ):
        enter = False
    elif re.search(r"\benter\s*=\s*false\b", low):
        enter = False
    elif re.search(r"\benter\s*=\s*true\b", low) and not re.search(
        r"enter\s*=\s*true\s*\|", low
    ):
        enter = True
    elif "false on chop" in low or "fakeout" in low:
        enter = False
    elif "ppo-led micro-fast" in low or "atr r:r" in low:
        # Gold copy of executed PPO path — defer (skip), not independent enter
        enter = False

    if enter is None:
        return None
    if conf is None:
        conf = 0.55 if enter else 0.48
    return {
        "enter": enter,
        "confidence": conf,
        "reason": "training echo",
    }


def _parse_spike_score_echo(text: str) -> Optional[Dict[str, Any]]:
    """
    Toddler echo lines from gold SFT, e.g.:
    COIN: sofi score=92 ppo=0.50 vol=1.00x x=18.16x
    COIN entry_decision=conf=0.54 note=ppo=hold score=84
    PPO-led micro-fast: score=82 vol=1.0x ppo=hold conf=0.54
    """
    import re

    low = text.lower()
    m = re.search(
        r"(?:^|\s)[A-Z]{1,5}:\s*([a-z]{1,5})\s+score=(\d+(?:\.\d+)?)\s+ppo=([\d.]+)",
        text,
        re.I,
    )
    if m:
        score = float(m.group(2))
        ppo = float(m.group(3))
        if ppo > 1.0:
            ppo /= 100.0
        enter = score >= 75.0 and ppo >= 0.48
        conf = min(0.95, max(0.40, score / 100.0))
        return {
            "enter": enter,
            "confidence": conf,
            "reason": f"score echo {score:.0f}",
        }

    m = re.search(
        r"entry_decision=.*?conf=([\d.]+).*?score=(\d+(?:\.\d+)?)",
        text,
        re.I,
    )
    if m:
        conf = float(m.group(1))
        score = float(m.group(2))
        if conf > 1.0:
            conf /= 100.0
        ppo_hold = "ppo=hold" in low or "ppo= hold" in low
        enter = score >= 78.0 and conf >= 0.52 and not ppo_hold
        return {
            "enter": enter,
            "confidence": conf,
            "reason": f"entry_decision echo score={score:.0f}",
        }

    if "ppo-led micro-fast" in low or "ppo not required" in low:
        score_m = re.search(r"score=(\d+(?:\.\d+)?)", text, re.I)
        if score_m:
            score = float(score_m.group(1))
            conf = _extract_echo_confidence(text) or min(0.90, score / 100.0)
            ppo_hold = "ppo=hold" in low
            enter = score >= 80.0 and conf >= 0.50 and not ppo_hold
            return {
                "enter": enter,
                "confidence": conf,
                "reason": f"micro-fast echo score={score:.0f}",
            }
    return None


def _parse_entry_lm_response(raw: str) -> Dict[str, Any]:
    """Parse Halim entry JSON; fallback heuristics for toddler-model ramble."""
    import json
    import re

    text = (raw or "").strip()
    if not text:
        return {}

    parsed = _parse_json_response(text)
    if parsed.get("enter") is not None:
        return _normalize_entry_parsed(parsed)

    # Embedded JSON object in prose
    for m in re.finditer(r"\{[^{}]*\"enter\"\s*:\s*(true|false)[^{}]*\}", text, re.I):
        try:
            blob = json.loads(m.group(0))
            if blob.get("enter") is not None:
                return _normalize_entry_parsed(blob)
        except Exception:
            continue

    spike_echo = _parse_spike_score_echo(text)
    if spike_echo:
        return _normalize_entry_parsed(spike_echo)

    echo = _parse_training_echo_entry(text)
    if echo:
        return _normalize_entry_parsed(echo)

    low = text.lower()
    # Instruction-echo (model repeats prompt rules instead of answering)
    if "entry_decision is not a signal" in low:
        return {"enter": False, "confidence": 0.4, "reason": "template echo skip"}
    if "entry_decision" in low and "{" not in text:
        enter = None
        conf = 0.42
        if re.search(r"\bfalse on\b", low) or "fakeout" in low or "chop" in low:
            enter = False
        elif re.search(r"\bclean momentum\b", low) or re.search(r"\bmomentum scalp\b", low):
            if not re.search(r"\bfalse\b", low):
                enter = True
                conf = 0.58
        if enter is not None:
            return {"enter": enter, "confidence": conf, "reason": text[:80]}

    enter = None
    for line in text.splitlines():
        line = line.strip()
        if not line.lower().startswith("enter="):
            continue
        if "|" in line:
            continue
        val = line.split("=", 1)[-1].strip().lower()
        if val == "true":
            enter = True
            break
        if val == "false":
            enter = False
            break
    if enter is None:
        if re.search(r'"enter"\s*:\s*true\b', text, re.I):
            enter = True
        elif re.search(r'"enter"\s*:\s*false\b', text, re.I):
            enter = False
    conf = None
    m = re.search(r'confidence["\s:=]+(\d+(?:\.\d+)?)', text, re.I)
    if m:
        conf = _safe_confidence_value(m.group(1))
    if enter is None and conf is not None:
        enter = conf >= 0.55
    if enter is None:
        return {}
    return _normalize_entry_parsed({
        "enter": enter,
        "confidence": conf if conf is not None else (0.65 if enter else 0.35),
        "reason": text[:80],
    })


def halim_advisory_is_echo(parsed: Optional[Dict[str, Any]]) -> bool:
    """Toddler LM regurgitation — not an independent Halim skip."""
    if not parsed:
        return False
    if str(parsed.get("advisory_kind", "")).lower() == "echo":
        return True
    reason = str(parsed.get("reason", "")).lower()
    return reason.startswith(("training echo", "score echo", "entry_decision echo", "micro-fast echo"))


def _normalize_entry_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(parsed)
    out["enter"] = bool(parsed.get("enter", False))
    conf = float(parsed.get("confidence", 0) or 0)
    if conf <= 0:
        conf = 0.55 if out["enter"] else 0.45
    elif conf > 1.0:
        conf /= 100.0
    out["confidence"] = round(min(0.99, max(0.0, conf)), 4)
    out["reason"] = str(parsed.get("reason", ""))[:80]
    reason_l = out["reason"].lower()
    if reason_l.startswith(("training echo", "score echo", "entry_decision echo", "micro-fast echo")):
        out["advisory_kind"] = "echo"
    return out


def halim_entry_ib_context_enabled() -> bool:
    return os.getenv("HALIM_ENTRY_IB_CONTEXT", "true").lower() in ("1", "true", "yes")


def build_halim_entry_ib_context(
    cfg: Optional[BotConfig] = None,
    *,
    ticker: str = "",
    price: float = 0.0,
) -> str:
    """
    Compact IB + war sizing line for Halim entry prompts.
    Uses cached ib_truth snapshot — no extra IB round-trips on the spike path.
    """
    if not halim_entry_ib_context_enabled():
        return ""
    cfg = cfg or BotConfig()
    parts: list[str] = []
    nav = 0.0
    try:
        from core.ib_truth import get_snapshot, ib_truth_enabled

        if ib_truth_enabled(cfg):
            snap = get_snapshot()
            if snap.refreshed_at > 0:
                acct = snap.account
                nav = float(acct.net_liquidation or 0)
                parts.append(
                    f"ib nav={nav:.0f} buying_power={acct.buying_power:.0f} "
                    f"avail={acct.available_funds:.0f} session_pnl={acct.realized_pnl:+.0f}"
                )
                if float(acct.excess_liquidity or 0) > 0:
                    parts.append(f"excess_liq={acct.excess_liquidity:.0f}")
                parts.append(f"open_positions={len(snap.long_positions())}")
    except Exception:
        pass

    try:
        from core.war_account import war_account_context, war_account_enabled

        if war_account_enabled(cfg):
            war = war_account_context(cfg)
            if war:
                parts.append(
                    f"war settled={float(war.get('war_settled_cash', 0)):.0f} "
                    f"bullets_left={int(war.get('war_bullets_remaining', 0))} "
                    f"deploy_cap={float(war.get('war_deploy_cap_usd', 0)):.0f}"
                )
    except Exception:
        pass

    if price > 0:
        try:
            from core.pilot_mode import get_trade_risk_usd

            risk_usd = get_trade_risk_usd(
                cfg, nav or float(getattr(cfg, "INITIAL_CASH", 1000))
            )
            stop_pct = float(getattr(cfg, "SCALP_MIN_STOP_PCT", 0.004))
            stop_dist = max(price * stop_pct, price * 0.003)
            shares_hint = max(1, int(risk_usd / stop_dist)) if stop_dist > 0 else 0
            deploy_cap = 0.0
            try:
                from core.war_account import war_account_context, war_account_enabled

                if war_account_enabled(cfg):
                    deploy_cap = float(war_account_context(cfg).get("war_deploy_cap_usd", 0) or 0)
            except Exception:
                pass
            if deploy_cap > 0:
                shares_cap = max(1, int(deploy_cap / price))
                shares_hint = min(shares_hint, shares_cap) if shares_hint else shares_cap
            notional = shares_hint * price
            parts.append(
                f"sizing risk_usd={risk_usd:.0f} shares_hint={shares_hint} "
                f"notional={notional:.0f} ask={price:.4f}"
            )
        except Exception:
            parts.append(f"ask={price:.4f}")

    return " ".join(parts)


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
    macro_context: str = "",
    ib_context: str = "",
    profit_prob: float = 0.0,
    enter_ok: bool = True,
    fakeout_risk: float = 0.0,
    setup_type: str = "",
) -> str:
    loss_line = f"{loss_context.strip()}\n" if loss_context else ""
    macro_line = f"{macro_context.strip()}\n" if macro_context else ""
    ib_line = f"{ib_context.strip()}\n" if ib_context else ""
    ppo_side = "buy" if ppo_buy else "hold"
    quality_line = ""
    if profit_prob > 0:
        quality_line = (
            f"quality profit_prob={profit_prob:.2f} enter_ok={str(enter_ok).lower()} "
            f"fakeout={fakeout_risk:.2f} setup={setup_type or 'mixed'}\n"
        )
    math_line = ""
    if ib_context:
        math_line = (
            "Use ib+sizing numbers for conviction; code executes exact shares.\n"
            'Optional: "size_intent":"full_bullet"|"half"|"skip" in reason.\n'
        )
    return (
        f"ENTRY {ticker.upper()} price={price:.4f} spike={spike:.2f}x score={scan:.0f}\n"
        f"ppo={ppo_side} conf={ppo_conf:.2f} note={ppo_reason[:50]}\n"
        f"{quality_line}{ib_line}{macro_line}{loss_line}{math_line}"
        'Reply ONE json object only. No other text.\n'
        '{"enter":true,"confidence":0.72,"reason":"calculated lottery full_bullet"}\n'
        '{"enter":false,"confidence":0.55,"reason":"chop fakeout skip"}\n'
        "enter=true when profit_prob high and momentum clean; false on chop/fakeout."
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

    def _halim_complete(self, prompt: str) -> tuple[str, str]:
        """Returns (text, failure_reason). failure_reason empty on success."""
        try:
            from halim.client import complete
            out = complete(
                prompt,
                purpose="entry_decision",
                timeout=_entry_timeout_sec(),
            )
            if out and out.get("ok") and out.get("text"):
                return str(out["text"]).strip(), ""
            if out and not out.get("ok"):
                reason = str(out.get("reason") or out.get("message") or "serve_not_ok")[:120]
                return "", reason
        except Exception as exc:
            log.debug(f"Halim entry LM: {exc}")
            return "", f"client_error:{exc}"[:120]
        return "", "serve_no_text"

    def _run(self, key: str, seq: int, prompt: str) -> None:
        t0 = time.time()
        fail_reason = ""
        try:
            raw, fail_reason = self._halim_complete(prompt)
            parsed = _parse_entry_lm_response(raw)
        except Exception as exc:
            log.warning(f"  🧠 Halim entry LM error {key}: {exc}")
            raw = ""
            fail_reason = f"run_error:{exc}"[:120]
            parsed = {}
        elapsed_ms = (time.time() - t0) * 1000
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
            tag = "echo" if str(parsed.get("reason", "")).startswith("training echo") else "json"
            log.info(
                f"  🧠 Halim entry LM ready {key} "
                f"enter={bool(parsed.get('enter'))} conf={float(parsed.get('confidence', 0) or 0):.0%} "
                f"({elapsed_ms:.0f}ms, {tag})"
            )
        elif raw:
            log.info(
                f"  🧠 Halim entry LM unparseable {key} ({elapsed_ms:.0f}ms): {raw[:100]!r}"
            )
        else:
            detail = fail_reason or "serve no text"
            log.info(f"  🧠 Halim entry LM empty {key} ({elapsed_ms:.0f}ms, {detail})")
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
        profit_prob: float = 0.0,
        enter_ok: bool = True,
        fakeout_risk: float = 0.0,
        setup_type: str = "",
    ) -> None:
        if not halim_entry_lm_enabled(self.cfg):
            return
        key = ticker.upper()
        now = time.time()
        with self._lock:
            prev = self._slots.get(key)
            if prev:
                if prev.in_flight:
                    if prev.fingerprint == fingerprint:
                        return
                    log.debug(
                        f"  🧠 Halim entry supersede {key} "
                        f"(new spike while LM in flight)"
                    )
                elif prev.fingerprint == fingerprint and (now - prev.submitted_at) < _min_ring_sec(self.cfg):
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
        macro_ctx = ""
        try:
            from core.live_trade_guard import loss_context_for_prompt
            loss_ctx = loss_context_for_prompt(key)
        except Exception:
            pass
        try:
            from core.market_context import macro_context_line, macro_ticker_hint
            macro_ctx = macro_context_line()
            hint = macro_ticker_hint(key)
            if hint:
                macro_ctx = f"{macro_ctx}\n{hint}" if macro_ctx else hint
        except Exception:
            pass
        ib_ctx = ""
        try:
            ib_ctx = build_halim_entry_ib_context(
                self.cfg, ticker=key, price=price,
            )
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
            macro_context=macro_ctx,
            ib_context=ib_ctx,
            profit_prob=profit_prob,
            enter_ok=enter_ok,
            fakeout_risk=fakeout_risk,
            setup_type=setup_type,
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

    def wait_for_completion(
        self,
        ticker: str,
        fingerprint: str,
        timeout_sec: float,
    ) -> str:
        """
        Poll until Halim entry slot finishes or timeout.
        Returns: ready, in_flight, missing, wrong_fp, timeout.
        """
        if timeout_sec <= 0:
            return "timeout"
        key = ticker.upper()
        deadline = time.time() + timeout_sec
        poll = max(0.03, float(os.getenv("HALIM_ENTRY_AWAIT_POLL_SEC", "0.05")))
        while time.time() < deadline:
            with self._lock:
                slot = self._slots.get(key)
                if not slot:
                    return "missing"
                if slot.fingerprint != fingerprint:
                    if slot.in_flight:
                        st = "in_flight"
                    else:
                        return "wrong_fp"
                elif slot.in_flight:
                    st = "in_flight"
                elif slot.parsed:
                    st = "ready"
                else:
                    st = "empty"
            if st != "in_flight":
                return st
            time.sleep(poll)
        return "timeout"


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
    if h_conf <= 0:
        h_conf = 0.55 if h_enter else 0.45
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
    complement = os.getenv("HALIM_PPO_COMPLEMENT", "true").lower() in ("1", "true", "yes")

    # PPO HOLD + Halim enter — mind complements reflex (quality-led override)
    if (
        complement
        and not ppo_buy
        and h_enter
        and h_conf >= min_conf * 0.80
        and not out.get("enter")
    ):
        out["enter"] = True
        out["reason"] = (
            f"Halim complements PPO HOLD {h_conf:.0%}: {h_reason}"
        )[:200]
        out["pipeline"] = f"{out.get('pipeline', 'council')}:halim_complement"
        out["confidence"] = max(cur_conf, h_conf, min_conf * 0.78)
        out["halim_enter"] = h_enter
        out["halim_conf"] = round(h_conf, 4)
        out["halim_agree"] = agree
        out["halim_reason"] = h_reason
        return out

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
