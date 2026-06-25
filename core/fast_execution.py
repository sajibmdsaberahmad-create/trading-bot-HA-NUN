#!/usr/bin/env python3
"""
core/fast_execution.py — AI-prioritized fast spike execution.

When AI_FAST_EXECUTION is on, the bot warms/streams top names first,
uses fewer bars on ALL priority tickers, and enters on strong spikes without
waiting for slow Ollama council deliberation.
"""

from __future__ import annotations

from typing import Any, List, Optional, TYPE_CHECKING

from core.config import BotConfig

if TYPE_CHECKING:
    from core.scanner import ScanResult


def ai_fast_execution(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "AI_FAST_EXECUTION", True))


def stream_priority_count(cfg: BotConfig) -> int:
    if not ai_fast_execution(cfg):
        return 999
    return int(getattr(cfg, "AI_STREAM_PRIORITY_COUNT", 8))


def tick_stream_max_count(cfg: BotConfig) -> int:
    """IB caps concurrent tick-by-tick subscriptions (error 10190 above limit)."""
    return int(getattr(cfg, "AI_TICK_STREAM_MAX", 5))


def stream_mode_for_rank(cfg: BotConfig, rank_index: int, in_position: bool = False) -> str:
    """Top ranks get tick-by-tick; rest get 5s realtime bars (same pool, no IB 10190)."""
    if in_position:
        return "tick"
    if rank_index < tick_stream_max_count(cfg):
        return "tick"
    return "realtime"


def warm_priority_count(cfg: BotConfig) -> int:
    return int(getattr(cfg, "AI_WARM_PRIORITY_COUNT", 10))


def min_bars_for_ticker(
    cfg: BotConfig,
    ticker: str,
    focus: Optional[str] = None,
    priority_names: Optional[List[str]] = None,
) -> int:
    """Priority tickers need fewer bars; others need more."""
    if not ai_fast_execution(cfg):
        return 20
    names = {n.upper() for n in (priority_names or [])}
    if focus:
        names.add(focus.upper())
    if names and ticker.upper() in names:
        return int(getattr(cfg, "AI_MIN_BARS_FOCUS", 6))
    return int(getattr(cfg, "AI_MIN_BARS_SCAN", 10))


def focus_rotation_enabled(cfg: BotConfig) -> bool:
    """Single-ticker rotation is OFF during fast execution — all priority names watched."""
    if ai_fast_execution(cfg):
        return False
    return float(getattr(cfg, "LOCK_FOCUS_ROTATE_SEC", 0)) > 0


def council_fast_sec(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "COUNCIL_SCANNER_FAST_SEC", 3.0))
    return float(getattr(cfg, "COUNCIL_SCANNER_FAST_SEC", 8.0))


def council_fast_min_score(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "COUNCIL_SCANNER_FAST_MIN_SCORE", 20.0))
    return float(getattr(cfg, "COUNCIL_SCANNER_FAST_MIN_SCORE", 78.0))


def council_fast_min_spike(cfg: BotConfig) -> float:
    return float(getattr(cfg, "COUNCIL_SCANNER_FAST_MIN_SPIKE", 1.15))


def should_spike_fast_entry(
    cfg: BotConfig,
    spike_ratio: float,
    scan_score: float,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
) -> bool:
    """Instant entry on strong spike — skip council wait."""
    if not getattr(cfg, "AI_SPIKE_FAST_ENTRY", True):
        return False
    min_spike = float(getattr(cfg, "AI_SPIKE_FAST_MIN_RATIO", 1.15))
    min_score = float(getattr(cfg, "AI_SPIKE_FAST_MIN_SCORE", 15.0))
    min_conf = float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.55)) * 0.75
    if spike_ratio >= min_spike and scan_score >= min_score:
        return True
    if spike_ratio >= min_spike * 1.1 and ppo_action == 1 and ppo_conf >= min_conf:
        return True
    if spike_ratio >= 1.3:
        return True
    return False


def prioritize_locked_targets(
    targets: List["ScanResult"],
    cfg: BotConfig,
    focus: Optional[str] = None,
) -> List["ScanResult"]:
    """AI/score order — best spike candidates warmed and streamed first."""
    if not targets:
        return []
    ranked = sorted(targets, key=lambda t: float(t.rank_score), reverse=True)
    if ai_fast_execution(cfg) or not focus:
        return ranked
    focus_u = focus.upper()
    head = [t for t in ranked if t.ticker.upper() == focus_u]
    tail = [t for t in ranked if t.ticker.upper() != focus_u]
    return head + tail


def stream_ticker_list(
    targets: List["ScanResult"],
    cfg: BotConfig,
    focus: Optional[str] = None,
) -> List[str]:
    """Tickers that get live streams — top N only when fast execution on."""
    ordered = prioritize_locked_targets(targets, cfg, focus if not ai_fast_execution(cfg) else None)
    n = stream_priority_count(cfg)
    return [t.ticker for t in ordered[:n]]


def warm_ticker_list(
    targets: List["ScanResult"],
    cfg: BotConfig,
    focus: Optional[str] = None,
) -> List[str]:
    ordered = prioritize_locked_targets(targets, cfg, focus if not ai_fast_execution(cfg) else None)
    n = warm_priority_count(cfg)
    return [t.ticker for t in ordered[:n]]


def is_ib_scanner_cancel_162(message: str) -> bool:
    return "scanner subscription cancelled" in (message or "").lower()


def prefetch_per_loop(cfg: BotConfig) -> int:
    if ai_fast_execution(cfg):
        return int(getattr(cfg, "SCAN_BAR_PREFETCH_PER_LOOP", 8))
    return int(getattr(cfg, "SCAN_BAR_PREFETCH_PER_LOOP", 2))


def warm_budget_sec(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "LOCK_BAR_WARM_BUDGET_SEC", 28.0))
    return float(getattr(cfg, "LOCK_BAR_WARM_BUDGET_SEC", 12.0))


def fast_monitor_interval(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "FAST_MONITOR_SEC", 0.25))
    return float(getattr(cfg, "FAST_MONITOR_SEC", 1.0))


def priority_tick_streams(cfg: BotConfig) -> bool:
    """All priority stream names get tick-by-tick — not just one focus."""
    if not ai_fast_execution(cfg):
        return False
    return bool(getattr(cfg, "AI_PRIORITY_TICK_STREAMS", True))


def max_spike_attempts_per_cycle(cfg: BotConfig) -> int:
    if ai_fast_execution(cfg):
        return int(getattr(cfg, "AI_SPIKE_ATTEMPTS_PER_CYCLE", 3))
    return 1


def monitor_ticker_list(
    targets: List["ScanResult"],
    cfg: BotConfig,
    focus: Optional[str] = None,
) -> List[str]:
    """Union of stream + warm priority — all monitored simultaneously."""
    f = None if ai_fast_execution(cfg) else focus
    stream = stream_ticker_list(targets, cfg, f)
    warm = warm_ticker_list(targets, cfg, f)
    seen: set[str] = set()
    out: List[str] = []
    for name in stream + warm:
        u = name.upper()
        if u not in seen:
            seen.add(u)
            out.append(name)
    return out


def is_priority_ticker(
    ticker: str,
    targets: List["ScanResult"],
    cfg: BotConfig,
    focus: Optional[str] = None,
) -> bool:
    f = None if ai_fast_execution(cfg) else focus
    return ticker.upper() in {t.upper() for t in monitor_ticker_list(targets, cfg, f)}
