#!/usr/bin/env python3
"""
core/fast_execution.py — AI-prioritized fast spike execution.

When AI_FAST_EXECUTION is on, the bot warms/streams top names first,
uses fewer bars on ALL priority tickers, and enters on strong spikes without
waiting for slow Ollama council deliberation.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, TYPE_CHECKING

from core.config import BotConfig

if TYPE_CHECKING:
    from core.scanner import ScanResult


def ai_fast_execution(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "AI_FAST_EXECUTION", True))


def stream_priority_count(cfg: BotConfig) -> int:
    if not ai_fast_execution(cfg):
        return 999
    return int(getattr(cfg, "AI_STREAM_PRIORITY_COUNT", 8))


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
        return float(getattr(cfg, "LOCK_BAR_WARM_BUDGET_SEC", 5.0))
    return float(getattr(cfg, "LOCK_BAR_WARM_BUDGET_SEC", 8.0))


def fast_monitor_interval(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "FAST_MONITOR_SEC", 0.15))
    return float(getattr(cfg, "FAST_MONITOR_SEC", 1.0))


def main_loop_sec(
    cfg: BotConfig,
    *,
    in_position: bool = False,
    have_targets: bool = False,
    in_profit: bool = False,
) -> float:
    """IB sleep between loop iterations — faster when locked or in profit."""
    if in_position:
        if in_profit and ai_fast_execution(cfg):
            return float(getattr(cfg, "POSITION_LOOP_IN_PROFIT_SEC", 0.1))
        return float(getattr(cfg, "POSITION_LOOP_SEC", 0.25))
    if have_targets and ai_fast_execution(cfg):
        return float(getattr(cfg, "FLAT_LOOP_LOCKED_SEC", 0.1))
    return float(getattr(cfg, "FLAT_LOOP_SEC", 0.25))


def council_max_wait_sec(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "AI_COUNCIL_MAX_WAIT_SEC", 4.0))
    return float(getattr(cfg, "AI_COUNCIL_MAX_WAIT_SEC", 15.0))


def entry_fill_poll_sec(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "ENTRY_FILL_WAIT_SEC", 0.25))
    return float(getattr(cfg, "ENTRY_FILL_WAIT_SEC", 1.0))


def ai_exit_check_sec(cfg: BotConfig, in_profit: bool = False) -> float:
    if in_profit and ai_fast_execution(cfg):
        return float(getattr(cfg, "AI_EXIT_CHECK_IN_PROFIT_SEC", 1.0))
    return float(getattr(cfg, "AI_EXIT_CHECK_SEC", 5.0))


def tick_spike_debounce_sec(cfg: BotConfig) -> float:
    return float(getattr(cfg, "TICK_SPIKE_DEBOUNCE_SEC", 0.08))


def tick_spike_monitor_enabled(cfg: BotConfig) -> bool:
    return bool(
        ai_fast_execution(cfg)
        and getattr(cfg, "TICK_SPIKE_MONITOR", True)
    )


def background_watch_sec(cfg: BotConfig) -> float:
    if ai_fast_execution(cfg):
        return float(getattr(cfg, "BACKGROUND_WATCH_SEC", 15.0))
    return float(getattr(cfg, "BACKGROUND_WATCH_SEC", 45.0))


def spike_entry_cooldown_sec(cfg: BotConfig) -> float:
    base = float(getattr(cfg, "SPIKE_ENTRY_ATTEMPT_COOLDOWN_SEC", 20.0))
    if ai_fast_execution(cfg):
        return min(base, float(getattr(cfg, "AI_SPIKE_COOLDOWN_FAST_SEC", 6.0)))
    return base


def stream_watch_cap(cfg: BotConfig) -> int:
    """Max locked names to monitor — match IB stream budget, not full scanner."""
    if ai_fast_execution(cfg):
        return int(getattr(cfg, "AI_STREAM_WATCH_CAP", 10))
    return int(getattr(cfg, "AI_MAX_LOCKED_TARGETS", 30))


def should_micro_fast_entry(
    cfg: BotConfig,
    spike_ratio: float,
    scan_score: float,
    micro: Optional[dict] = None,
) -> bool:
    """Enter without Ollama wait — strong scanner score + micro momentum."""
    if not ai_fast_execution(cfg):
        return False
    micro = micro or {}
    sl = float(micro.get("spike_likelihood", 0))
    va = float(micro.get("vol_accel", 1.0))
    if scan_score >= 75 and sl >= 0.40:
        return True
    if scan_score >= 70 and sl >= 0.45 and va >= 0.95:
        return True
    if scan_score >= 55 and sl >= 0.52 and (spike_ratio >= 1.05 or va >= 1.15):
        return True
    if spike_ratio >= 1.15 and scan_score >= float(getattr(cfg, "AI_SPIKE_FAST_MIN_SCORE", 15.0)):
        return True
    return False


def micro_confirms_spike(
    spike_ratio: float,
    micro: Optional[dict],
    *,
    min_likelihood: float = 0.55,
    min_vol_accel: float = 1.12,
) -> bool:
    """Micro forecast alone is not a spike — need volume confirmation."""
    if not micro:
        return False
    sl = float(micro.get("spike_likelihood", 0))
    va = float(micro.get("vol_accel", 1.0))
    if sl < min_likelihood:
        return False
    return spike_ratio >= 1.08 or va >= min_vol_accel


def apply_micro_spike_boost(
    is_spike: bool,
    spike_ratio: float,
    micro: Optional[dict],
) -> Tuple[bool, float]:
    """Only boost spike when micro + volume agree — prevents 0.8x false entries."""
    if micro_confirms_spike(spike_ratio, micro):
        return True, max(spike_ratio, float(micro.get("vol_accel", spike_ratio)))
    return is_spike, spike_ratio


def skip_historical_prefetch(cfg: BotConfig) -> bool:
    """Fast lock uses live stream bars — avoid HMDS 162 on OTC names."""
    return ai_fast_execution(cfg) and bool(getattr(cfg, "FAST_LOCK_SKIP_HISTORICAL", True))


def entry_pending_block_sec(cfg: BotConfig) -> float:
    base = float(getattr(cfg, "ENTRY_PENDING_BLOCK_SEC", 45.0))
    if ai_fast_execution(cfg):
        return min(base, float(getattr(cfg, "ENTRY_PENDING_BLOCK_FAST_SEC", 12.0)))
    return base


def tick_stream_count(cfg: BotConfig) -> int:
    """IB allows ~5 tick-by-tick subs — reserve headroom for open positions."""
    return int(getattr(cfg, "AI_TICK_STREAM_COUNT", 4))


def max_realtime_bar_streams(cfg: BotConfig) -> int:
    """IB allows ~5 concurrent 5-second real-time bar streams."""
    return int(getattr(cfg, "IB_MAX_REALTIME_BAR_STREAMS", 4))


def assign_stream_modes(
    wanted: List[str],
    cfg: BotConfig,
    held: Optional[set] = None,
    tick_denied: Optional[set] = None,
) -> dict:
    """
    Split priority list across IB limits: top names get tick, rest get 5s bars.
    Returns {ticker: 'tick'|'realtime'|'skip'}.
    """
    held_u = {str(t).upper() for t in (held or set())}
    denied = {str(t).upper() for t in (tick_denied or set())}
    tick_budget = tick_stream_count(cfg)
    rt_budget = max_realtime_bar_streams(cfg)
    modes: dict = {}
    tick_used = 0
    rt_used = 0
    for ticker in wanted:
        tu = ticker.upper()
        if tu in held_u:
            if tick_used < tick_budget and tu not in denied:
                modes[ticker] = "tick"
                tick_used += 1
            elif rt_used < rt_budget:
                modes[ticker] = "realtime"
                rt_used += 1
            else:
                modes[ticker] = "skip"
            continue
        if tick_used < tick_budget and tu not in denied:
            modes[ticker] = "tick"
            tick_used += 1
        elif rt_used < rt_budget:
            modes[ticker] = "realtime"
            rt_used += 1
        else:
            modes[ticker] = "skip"
    return modes


def priority_tick_streams(cfg: BotConfig) -> bool:
    """Deprecated alias — IB tick cap prevents all-tick; use assign_stream_modes."""
    return False


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
