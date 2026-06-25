#!/usr/bin/env python3
"""
core/architecture_epoch.py — Clean-slate mood & metrics after pipeline upgrades.

Pre-upgrade trades (inverted stops, LLM brackets) are archived so mood, shadow
circuit, and win-rate metrics reflect only the deterministic hybrid pipeline.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

EPOCH_PATH = Path("models/architecture_epoch.json")
DEFAULT_VERSION = "hybrid_atr_v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_epoch() -> Dict[str, Any]:
    if not EPOCH_PATH.exists():
        return {}
    try:
        return json.loads(EPOCH_PATH.read_text())
    except Exception:
        return {}


def save_epoch(data: Dict[str, Any]) -> None:
    EPOCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now_iso()
    EPOCH_PATH.write_text(json.dumps(data, indent=2))


def current_version(cfg: BotConfig) -> str:
    return str(getattr(cfg, "ARCHITECTURE_VERSION", DEFAULT_VERSION))


def epoch_active(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "ARCHITECTURE_EPOCH_MOOD_FILTER", True))


def ensure_epoch(cfg: BotConfig, *, force_reset: bool = False) -> Dict[str, Any]:
    """
    On startup: bump epoch if ARCHITECTURE_VERSION changed or force_reset.
    Returns epoch record.
    """
    ver = current_version(cfg)
    prev = load_epoch()
    bumped = force_reset or (prev.get("version") and prev.get("version") != ver)
    if not prev or bumped:
        data = {
            "version": ver,
            "epoch_ts": time.time(),
            "epoch_iso": _now_iso(),
            "note": (
                "Mood/metrics use only trades after this timestamp. "
                "Legacy pre-hybrid pipeline scars archived."
            ),
            "previous_version": prev.get("version"),
        }
        save_epoch(data)
        log.info(
            f"  📐 Architecture epoch {'RESET' if bumped else 'INIT'}: {ver} @ {data['epoch_iso']}"
        )
        return data
    return prev


def stamp_trade(trade: Dict[str, Any], cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Tag trade records with epoch metadata for filtered metrics."""
    out = dict(trade)
    ep = load_epoch()
    out.setdefault("observed_at", time.time())
    out["architecture_version"] = ep.get("version", current_version(cfg) if cfg else DEFAULT_VERSION)
    out["post_epoch"] = is_post_epoch_trade(out, ep)
    return out


def is_post_epoch_trade(trade: Dict[str, Any], epoch: Optional[Dict[str, Any]] = None) -> bool:
    epoch = epoch or load_epoch()
    if not epoch.get("epoch_ts"):
        return True
    ts = float(trade.get("observed_at", trade.get("timestamp", time.time())) or 0)
    if isinstance(trade.get("timestamp"), str):
        try:
            ts = datetime.fromisoformat(
                trade["timestamp"].replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            ts = time.time()
    return ts >= float(epoch["epoch_ts"])


def mood_pnls_from_history(
    win_history: List[float],
    mood_epoch_start_index: int,
) -> List[float]:
    """PnL list for mood — only post-epoch slice."""
    if mood_epoch_start_index > 0:
        return win_history[mood_epoch_start_index:]
    return list(win_history)


def apply_consciousness_epoch_reset(consciousness: Any, cfg: BotConfig) -> None:
    """Archive legacy mood inputs; reset streak counters for clean evaluation."""
    if not consciousness or not hasattr(consciousness, "state"):
        return
    st = consciousness.state
    wh = st.get("win_history", [])
    archived = len(wh)
    st["mood_epoch_start_index"] = archived
    st["pre_epoch_trade_count"] = archived
    st["pre_epoch_total_pnl"] = round(sum(wh), 2) if wh else 0.0
    st["consecutive_losses"] = 0
    st["consecutive_wins"] = 0
    st["architecture_version"] = current_version(cfg)
    st["mood"] = "learning"
    consciousness._save_state()
    log.info(
        f"  🧠 Consciousness mood reset: {archived} legacy trades archived | "
        f"mood=learning until {int(getattr(cfg, 'ARCHITECTURE_EPOCH_MIN_TRADES', 5))} "
        f"post-epoch trades"
    )


def apply_cognitive_epoch_reset(autopilot: Any, cfg: BotConfig) -> None:
    if not autopilot or not getattr(autopilot, "core", None):
        return
    core = autopilot.core
    n = len(core._trade_outcomes)
    core.state.mood_epoch_start_index = n
    core.state.mood = "learning"
    core.state.confidence = max(float(core.state.confidence), 0.55)
    core._persist_state(push_git=False)
    log.info(f"  🧠 Cognitive mood reset: {n} legacy outcomes archived")


def apply_shadow_epoch_reset(shadow_circuit: Any) -> None:
    if not shadow_circuit:
        return
    shadow_circuit.consecutive_losses = 0
    shadow_circuit.daily_pnl_usd = 0.0
    shadow_circuit._save()


def apply_full_epoch_reset(
    cfg: BotConfig,
    *,
    consciousness: Any = None,
    autopilot: Any = None,
    shadow_circuit: Any = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Called on startup when version bumps or ARCHITECTURE_EPOCH_RESET=true."""
    ep = ensure_epoch(cfg, force_reset=force)
    if not getattr(cfg, "ARCHITECTURE_EPOCH_MOOD_FILTER", True):
        return ep
    need_reset = bool(force or ep.get("previous_version"))
    if consciousness and hasattr(consciousness, "state"):
        if "mood_epoch_start_index" not in consciousness.state:
            need_reset = True
    if need_reset:
        apply_consciousness_epoch_reset(consciousness, cfg)
        apply_cognitive_epoch_reset(autopilot, cfg)
        apply_shadow_epoch_reset(shadow_circuit)
    return ep


def mood_trade_count(win_history: List[float], mood_epoch_start_index: int) -> int:
    return len(mood_pnls_from_history(win_history, mood_epoch_start_index))


def effective_mood_from_pnls(
    pnls: List[float],
    cfg: BotConfig,
    *,
    consecutive_losses: int = 0,
    consecutive_wins: int = 0,
    total_pnl: float = 0.0,
) -> str:
    """Same thresholds as consciousness but on filtered PnL list."""
    min_trades = int(getattr(cfg, "ARCHITECTURE_EPOCH_MIN_TRADES", 5))
    if len(pnls) < min_trades:
        return "learning"
    recent = pnls[-20:]
    wins = sum(1 for w in recent if w > 0)
    wr = wins / len(recent) if recent else 0.5
    if wr >= 0.8 and consecutive_wins >= 5 and total_pnl > 0:
        return "euphoric"
    if wr >= 0.65 and total_pnl > 0:
        return "confident"
    if wr >= 0.45:
        return "stable"
    if wr >= 0.30:
        return "cautious"
    return "anxious"
