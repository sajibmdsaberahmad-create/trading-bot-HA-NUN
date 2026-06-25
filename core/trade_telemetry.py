#!/usr/bin/env python3
"""
core/trade_telemetry.py — Post-mortem audit trail for every gate in the pipeline.

Logs regime context, slippage, raw Ollama JSON vs final ATR bracket, and rejection
reasons so mood/anxiety can be replaced by objective diagnostics.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

MODELS_DIR = Path("models")
AUDIT_PATH = MODELS_DIR / "post_mortem_audit.jsonl"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(record: Dict[str, Any]) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    record.setdefault("timestamp", _now())
    line = json.dumps(record, default=str, separators=(",", ":"))
    with _lock:
        with open(AUDIT_PATH, "a") as f:
            f.write(line + "\n")


def regime_tag(
    regime_result: Any = None,
    *,
    spike_ratio: float = 1.0,
    vol_ratio: float = 1.0,
) -> str:
    """Human-readable regime bucket for telemetry."""
    if regime_result is not None:
        label = str(getattr(getattr(regime_result, "regime", None), "value", regime_result))
        if "high_vol" in label or "breakout" in label:
            base = "high_vol_spike"
        elif "sideways" in label or "accumulation" in label:
            base = "choppy_consolidation"
        elif "bull" in label or "breakout" in label:
            base = "trend_grind"
        else:
            base = label
    elif spike_ratio >= 2.5:
        base = "high_vol_spike"
    elif spike_ratio >= 1.3:
        base = "momentum_spike"
    else:
        base = "slow_grind"
    if vol_ratio >= 3.0 and spike_ratio >= 2.0:
        return f"{base}|late_chase_risk"
    return base


def log_bracket_reject(
    cfg: BotConfig,
    *,
    ticker: str,
    reason: str,
    entry: float,
    stop: float,
    target: float,
    shares: int,
    council_decision: Optional[Dict[str, Any]] = None,
    ollama_raw: str = "",
    ollama_parsed: Optional[Dict[str, Any]] = None,
    regime: str = "",
    spike_ratio: float = 0.0,
    pipeline: str = "",
) -> None:
    """Risk Officer rejected math — log split between strategist and execution."""
    rec = {
        "event": "bracket_reject",
        "ticker": ticker,
        "reason": reason,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "shares": shares,
        "reward_risk_raw": _raw_rr(entry, stop, target),
        "regime_tag": regime,
        "spike_ratio": round(spike_ratio, 2),
        "pipeline": pipeline,
        "council": _trim(council_decision),
        "ollama_raw": (ollama_raw or "")[:2000],
        "ollama_parsed": _trim(ollama_parsed),
        "ollama_had_prices": _ollama_had_numeric_prices(ollama_parsed),
    }
    _append(rec)
    log.info(
        f"  📋 POST-MORTEM bracket_reject {ticker}: {reason[:80]} | "
        f"regime={regime} | ollama_prices={rec['ollama_had_prices']}"
    )


def log_post_fill_adapt(
    *,
    ticker: str,
    planned_entry: float,
    fill_px: float,
    old_stop: float,
    old_target: float,
    new_stop: float,
    new_target: float,
    shares: int,
    slippage_pct: float,
    adjusted: bool,
    aborted: bool,
    reason: str,
) -> None:
    rec = {
        "event": "post_fill_abort" if aborted else ("post_fill_reanchor" if adjusted else "post_fill_ok"),
        "ticker": ticker,
        "planned_entry": round(planned_entry, 4),
        "fill_px": round(fill_px, 4),
        "slippage_pct": round(slippage_pct, 6),
        "old_stop": round(old_stop, 4),
        "old_target": round(old_target, 4),
        "new_stop": round(new_stop, 4),
        "new_target": round(new_target, 4),
        "shares": shares,
        "old_rr": _raw_rr(planned_entry, old_stop, old_target),
        "new_rr": _raw_rr(fill_px, new_stop, new_target),
        "reason": reason[:300],
    }
    _append(rec)
    if aborted:
        log.warning(f"  🛑 POST-FILL ABORT {ticker}: {reason[:120]}")
    elif adjusted:
        log.info(
            f"  🔧 POST-FILL re-anchor {ticker}: fill ${fill_px:.4f} | "
            f"stop ${old_stop:.4f}→${new_stop:.4f} tp ${old_target:.4f}→${new_target:.4f}"
        )


def log_entry_execution(
    *,
    ticker: str,
    limit_px: Optional[float],
    fill_px: float,
    entry_mode: str,
    shares: int,
    stop: float,
    target: float,
    regime: str = "",
    spike_ratio: float = 0.0,
    council_decision: Optional[Dict[str, Any]] = None,
    ollama_raw: str = "",
    ollama_parsed: Optional[Dict[str, Any]] = None,
    shadow: bool = False,
) -> None:
    slippage = 0.0
    slippage_pct = 0.0
    if limit_px and limit_px > 0 and fill_px > 0:
        slippage = round(fill_px - limit_px, 4)
        slippage_pct = round(slippage / limit_px, 6)
    rec = {
        "event": "entry_fill" if not shadow else "shadow_entry",
        "ticker": ticker,
        "limit_px": limit_px,
        "fill_px": round(fill_px, 4),
        "slippage": slippage,
        "slippage_pct": slippage_pct,
        "entry_mode": entry_mode,
        "shares": shares,
        "stop": round(stop, 4),
        "target": round(target, 4),
        "reward_risk": _raw_rr(fill_px, stop, target),
        "regime_tag": regime,
        "spike_ratio": round(spike_ratio, 2),
        "council": _trim(council_decision),
        "ollama_raw": (ollama_raw or "")[:2000],
        "ollama_parsed": _trim(ollama_parsed),
        "shadow": shadow,
    }
    _append(rec)


def log_round_trip_fills(
    *,
    ticker: str,
    entry_fill: float,
    exit_fill: float,
    quote_entry: float,
    quote_exit: float,
    shares: float,
    pnl_usd: float,
    pnl_pct: float,
    result: str,
    exit_reason: str = "",
    entry_slippage_pct: float = 0.0,
    exit_slippage_pct: float = 0.0,
    regime: str = "",
    hold_sec: float = 0.0,
    entry_mode: str = "",
    limit_px: Optional[float] = None,
) -> None:
    rec = {
        "event": "round_trip_fill",
        "ticker": ticker,
        "entry_fill": round(entry_fill, 4),
        "exit_fill": round(exit_fill, 4),
        "quote_entry": round(quote_entry, 4),
        "quote_exit": round(quote_exit, 4),
        "shares": shares,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
        "result": result,
        "exit_reason": exit_reason[:200],
        "entry_slippage_pct": entry_slippage_pct,
        "exit_slippage_pct": exit_slippage_pct,
        "regime_tag": regime,
        "hold_sec": round(hold_sec, 1),
        "entry_mode": entry_mode,
        "limit_px": limit_px,
    }
    _append(rec)
    log.info(
        f"  💰 FILL LEDGER {ticker}: in ${entry_fill:.4f} → out ${exit_fill:.4f} | "
        f"P&L ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) | {result}"
    )


def log_exit_postmortem(
    *,
    ticker: str,
    entry: float,
    exit_px: float,
    shares: float,
    pnl_usd: float,
    pnl_pct: float,
    result: str,
    regime: str = "",
    hold_sec: float = 0.0,
    exit_reason: str = "",
    entry_slippage_pct: float = 0.0,
    shadow: bool = False,
) -> None:
    rec = {
        "event": "exit_postmortem" if not shadow else "shadow_exit",
        "ticker": ticker,
        "entry": round(entry, 4),
        "exit": round(exit_px, 4),
        "shares": shares,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
        "result": result,
        "regime_tag": regime,
        "hold_sec": round(hold_sec, 1),
        "exit_reason": exit_reason[:200],
        "entry_slippage_pct": entry_slippage_pct,
        "shadow": shadow,
    }
    _append(rec)
    log.info(
        f"  📋 POST-MORTEM exit {ticker}: {result} ${pnl_usd:+.2f} | "
        f"regime={regime} | hold={hold_sec:.0f}s"
    )


REGIME_ATR_STATS_PATH = MODELS_DIR / "regime_atr_efficiency.jsonl"


def log_regime_atr_outcome(
    *,
    ticker: str,
    regime: str,
    exit_type: str,
    entry: float,
    exit_px: float,
    stop: float,
    target: float,
    atr: float,
    hold_sec: float,
    pnl_usd: float,
    planned_rr: Optional[float] = None,
    noise_stop: bool = False,
) -> None:
    """
    Track whether ATR brackets are noise-choked per regime (stop before expansion).
    """
    stop_dist = round(entry - stop, 4) if entry > stop else 0.0
    target_dist = round(target - entry, 4) if target > entry else 0.0
    rec = {
        "event": "regime_atr_outcome",
        "ticker": ticker,
        "regime_tag": regime,
        "exit_type": exit_type,
        "entry": round(entry, 4),
        "exit": round(exit_px, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "atr": round(atr, 4),
        "stop_dist": stop_dist,
        "target_dist": target_dist,
        "stop_atr_ratio": round(stop_dist / atr, 2) if atr > 0 else None,
        "hold_sec": round(hold_sec, 1),
        "pnl_usd": round(pnl_usd, 2),
        "planned_rr": planned_rr,
        "noise_stop": noise_stop,
    }
    _append(rec)
    REGIME_ATR_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(REGIME_ATR_STATS_PATH, "a") as f:
            f.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
    if noise_stop:
        log.info(
            f"  📊 REGIME/ATR noise-stop {ticker} [{regime}]: "
            f"stopped in {hold_sec:.0f}s | stop/ATR={rec.get('stop_atr_ratio')}"
        )


def _raw_rr(entry: float, stop: float, target: float) -> Optional[float]:
    risk = entry - stop
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 4)


def _ollama_had_numeric_prices(parsed: Optional[Dict[str, Any]]) -> bool:
    if not parsed:
        return False
    return any(parsed.get(k) is not None for k in ("stop", "target", "shares", "price"))


def _trim(d: Optional[Dict[str, Any]], max_keys: int = 24) -> Dict[str, Any]:
    if not d:
        return {}
    out = {}
    for i, (k, v) in enumerate(d.items()):
        if i >= max_keys:
            break
        if isinstance(v, str) and len(v) > 300:
            out[k] = v[:300]
        else:
            out[k] = v
    return out
