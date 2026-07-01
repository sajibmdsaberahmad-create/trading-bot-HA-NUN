#!/usr/bin/env python3
"""
core/green_wave_entry.py — Institutional algo-wave entry + remaining-edge clock.

Early footprint entry (relax green_bar when impulse detected) and fused
wave_edge for hold/exit — plugs into green_trade_doctrine.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import BotConfig


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def green_wave_entry_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    """Wave footprint can substitute for strict green_bar when impulse is strong."""
    if not _env_bool("GREEN_WAVE_ENTRY", "true"):
        return False
    try:
        from core.green_trade_doctrine import green_entry_mandatory
        return green_entry_mandatory(cfg)
    except Exception:
        return True


def institutional_signal_dict(sig: Any) -> Dict[str, Any]:
    """Serialize InstitutionalSignal for green assess / logging."""
    try:
        return asdict(sig)
    except Exception:
        return {
            "detected": bool(getattr(sig, "detected", False)),
            "direction": str(getattr(sig, "direction", "neutral")),
            "strength": float(getattr(sig, "strength", 0) or 0),
            "confidence": float(getattr(sig, "confidence", 0) or 0),
            "block_trade_detected": bool(getattr(sig, "block_trade_detected", False)),
            "volume_cluster_detected": bool(getattr(sig, "volume_cluster_detected", False)),
            "bid_ask_imbalance": float(getattr(sig, "bid_ask_imbalance", 0) or 0),
            "cumulative_delta_z": float(getattr(sig, "cumulative_delta_z", 0) or 0),
            "large_print_ratio": float(getattr(sig, "large_print_ratio", 0) or 0),
            "tick_velocity": float(getattr(sig, "tick_velocity", 0) or 0),
            "relative_volume": float(getattr(sig, "relative_volume", 0) or 0),
        }


def scan_institutional_from_market(
    df: Any,
    dm: Any = None,
    *,
    bar_lookback: int = 20,
    tick_lookback: int = 80,
) -> Dict[str, Any]:
    """Ephemeral institutional scan from recent bars + optional tick buffer."""
    from core.institutional import InstitutionalDetector

    det = InstitutionalDetector()
    if df is not None and len(df) >= 5:
        vols = df["volume"].values
        closes = df["close"].values
        n = min(bar_lookback, len(df) - 1)
        for i in range(-n, 0):
            det.feed_bar(float(vols[i]), float(closes[i]))
    if dm is not None:
        ticks = list(getattr(dm, "_tick_buffer", []) or [])
        for t in ticks[-tick_lookback:]:
            px = float(t.get("price", 0) or 0)
            sz = float(t.get("size", 0) or 0)
            if px > 0 and sz > 0:
                det.feed_tick(px, sz, str(t.get("side", "unknown")))
    return institutional_signal_dict(det.scan())


def institutional_entry_veto(
    inst: Optional[Dict[str, Any]],
    micro: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Hard veto before wave/ green entry — distribution or heavy fade."""
    inst = inst or {}
    micro = micro or {}
    if inst.get("direction") == "distributing" and float(inst.get("strength", 0) or 0) >= _env_float(
        "GREEN_WAVE_VETO_DIST_STRENGTH", 0.55,
    ):
        return f"wave_veto:distributing str={float(inst.get('strength', 0)):.2f}"
    if float(inst.get("cumulative_delta_z", 0) or 0) <= _env_float("GREEN_WAVE_VETO_DELTA_Z", -1.8):
        return f"wave_veto:delta_z={float(inst.get('cumulative_delta_z', 0)):.1f}"
    fade = float(micro.get("fade_risk", 0) or 0)
    if fade >= _env_float("GREEN_WAVE_ENTRY_MAX_FADE", 0.62):
        return f"wave_veto:fade_risk={fade:.2f}"
    if float(micro.get("dir", 0) or 0) < 0 and fade >= _env_float("GREEN_WAVE_ENTRY_MAX_FADE", 0.62) * 0.85:
        return "wave_veto:pred_down+fade"
    return None


def detect_institutional_impulse(
    inst: Optional[Dict[str, Any]],
    micro: Optional[Dict[str, Any]],
    *,
    spike_ratio: float = 1.0,
) -> Dict[str, Any]:
    """
    Early algo-wave footprint — block prints, vol cluster, delta, micro accel.
    Used to relax green_bar when GREEN_WAVE_ENTRY is on.
    """
    inst = inst or {}
    micro = micro or {}
    min_strength = _env_float("GREEN_WAVE_MIN_INST_STRENGTH", 0.42)
    min_vol_accel = _env_float("GREEN_WAVE_MIN_VOL_ACCEL", 1.15)
    min_spike_lik = _env_float("GREEN_WAVE_MIN_SPIKE_LIKELIHOOD", 0.40)
    min_delta_z = _env_float("GREEN_WAVE_MIN_CUM_DELTA_Z", 0.65)
    min_rel_vol = _env_float("GREEN_WAVE_MIN_REL_VOLUME", 1.35)
    min_spike_ratio = _env_float("GREEN_WAVE_MIN_SPIKE_RATIO", 1.20)

    vol_accel = float(micro.get("vol_accel", 1) or 1)
    spike_lik = float(micro.get("spike_likelihood", 0) or 0)
    strength = float(inst.get("strength", 0) or 0)
    conf = float(inst.get("confidence", 0) or 0)
    delta_z = float(inst.get("cumulative_delta_z", 0) or 0)
    rel_vol = float(inst.get("relative_volume", 0) or 0)
    direction = str(inst.get("direction", "neutral"))
    block = bool(inst.get("block_trade_detected"))
    cluster = bool(inst.get("volume_cluster_detected"))
    tick_vel = float(inst.get("tick_velocity", 0) or 0)

    score = 0.0
    reasons = []
    if direction == "accumulating" and strength >= min_strength:
        score += 0.28
        reasons.append(f"inst_acc={strength:.2f}")
    if block:
        score += 0.18
        reasons.append("block_prints")
    if cluster:
        score += 0.14
        reasons.append("vol_cluster")
    if delta_z >= min_delta_z:
        score += min(0.20, delta_z * 0.08)
        reasons.append(f"delta_z={delta_z:.1f}")
    if vol_accel >= min_vol_accel:
        score += min(0.22, (vol_accel - 1.0) * 0.18)
        reasons.append(f"vol_accel={vol_accel:.2f}")
    if spike_lik >= min_spike_lik:
        score += spike_lik * 0.20
        reasons.append(f"spike_lik={spike_lik:.2f}")
    if rel_vol >= min_rel_vol:
        score += 0.10
        reasons.append(f"rel_vol={rel_vol:.1f}x")
    if spike_ratio >= min_spike_ratio:
        score += min(0.22, 0.08 + (spike_ratio - min_spike_ratio) * 0.04)
        reasons.append(f"spike={spike_ratio:.2f}x")
    # Cold micro forecast: huge live vol spike still counts as accel footprint
    vol_footprint = (
        vol_accel >= min_vol_accel
        or spike_lik >= min_spike_lik
        or spike_ratio >= max(min_spike_ratio * 2.5, 2.0)
    )
    inst_footprint = (
        strength >= min_strength * 0.85
        or block
        or cluster
        or (spike_ratio >= min_spike_ratio * 2.0 and direction != "distributing")
    )
    if tick_vel > 0.0008:
        score += min(0.12, tick_vel * 40.0)
        reasons.append("tick_vel+")

    score = min(1.0, score * (0.85 + conf * 0.15))
    impulse_ok = (
        direction != "distributing"
        and score >= _env_float("GREEN_WAVE_IMPULSE_MIN_SCORE", 0.48)
        and vol_footprint
        and inst_footprint
    )
    return {
        "impulse_ok": impulse_ok,
        "impulse_score": round(score, 3),
        "reasons": reasons,
        "institutional": inst,
    }


def assess_wave_remaining_edge(
    micro: Optional[Dict[str, Any]],
    inst: Optional[Dict[str, Any]],
    *,
    current_px: float,
    pnl_pct: float = 0.0,
    peak_pct: float = 0.0,
) -> Dict[str, Any]:
    """
    Fused wave clock: how much edge remains in the institutional impulse.
    High → hold/ride; low → book profit (algos leaving).
    """
    micro = micro or {}
    inst = inst or {}
    if current_px <= 0:
        return {"wave_edge": 0.0, "should_hold": False, "should_exit_now": False}

    profit_run = float(micro.get("profit_run", 0) or 0)
    fade = float(micro.get("fade_risk", 0) or 0)
    mom = float(micro.get("momentum", 0) or 0)
    vol_accel = float(micro.get("vol_accel", 1) or 1)
    pred_3 = float(micro.get("pred_3bar") or current_px)
    pred_1 = float(micro.get("pred_1bar") or current_px)
    inst_str = float(inst.get("strength", 0) or 0) if inst.get("direction") == "accumulating" else 0.0
    tick_vel = float(inst.get("tick_velocity", 0) or 0)
    delta_z = float(inst.get("cumulative_delta_z", 0) or 0)

    upside_3 = (pred_3 / current_px - 1.0) if current_px > 0 else 0.0
    upside_1 = (pred_1 / current_px - 1.0) if current_px > 0 else 0.0
    giveback = max(0.0, peak_pct - pnl_pct)

    edge = (
        profit_run * 0.30
        + min(max(upside_3, upside_1 * 0.7), 0.02) * 8.0
        + min(max(vol_accel - 1.0, 0), 0.8) * 0.22
        + inst_str * 0.18
        + min(max(delta_z, 0), 2.5) * 0.06
        + min(max(tick_vel * 50, 0), 0.15)
        + min(max(mom, 0), 1.0) * 0.12
        - fade * 0.45
        - giveback * 2.5
    )
    if inst.get("direction") == "distributing":
        edge -= float(inst.get("strength", 0) or 0) * 0.35
    if vol_accel < 0.82 and mom > 0.04:
        edge -= 0.15  # price up, volume dying — classic algo exit

    edge = float(max(0.0, min(1.0, edge)))
    hold_thr = _env_float("GREEN_WAVE_MIN_EDGE_HOLD", 0.34)
    exit_thr = _env_float("GREEN_WAVE_EXIT_EDGE", 0.22)
    return {
        "wave_edge": round(edge, 3),
        "should_hold": edge >= hold_thr and pnl_pct > 0,
        "should_exit_now": edge <= exit_thr and pnl_pct > 0,
        "upside_3bar_pct": round(upside_3, 5),
        "fade_risk": fade,
        "profit_run": profit_run,
        "giveback": giveback,
    }
