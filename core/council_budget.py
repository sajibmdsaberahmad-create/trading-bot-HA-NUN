#!/usr/bin/env python3
"""
core/council_budget.py — Rate-limit cloud council API to hot-path decisions.

Reserves Groq/Gemini RPM for live entry/exit council. Telegram, runtime
observer, and ambient generative calls use structured fallbacks unless
explicitly enabled and within budget.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, FrozenSet, Optional, Tuple

from core.config import BotConfig

# Hot path — always allowed (unless both providers are in 429 cooldown)
PURPOSE_DECISION = "decision"

# Lower priority — budgeted / usually template-only
PURPOSE_NOTIFY = "notify"
PURPOSE_COPILOT = "copilot"
PURPOSE_RUNTIME = "runtime"
PURPOSE_JOURNAL = "journal"
PURPOSE_GENERATIVE = "generative"
PURPOSE_OFFHOURS = "offhours"
PURPOSE_DAILY_DIGEST = "daily_digest"

_lock = threading.Lock()
_minute_buckets: Dict[str, Deque[float]] = {}
_last_call_by_purpose: Dict[str, float] = {}
_last_daily_digest_day: Optional[str] = None

# Telegram events — structured fallback is enough (no API)
_NOTIFY_TEMPLATE_ONLY: FrozenSet[str] = frozenset({
    "watch_pulse", "system_status", "info", "git_push", "learning_checkpoint",
    "model_release", "startup", "targets_locked", "warning", "error",
    "help", "usage", "flat_positions", "exit_progress", "improve_progress",
    "daily_progress", "verify_locked", "verify_success", "verify_failed",
    "verify_prompt", "verify_required", "unknown_command", "vision_wait",
    "vision_unavailable", "image_download_fail", "runner_unavailable",
    "guide_stored", "exit_result", "exitall_result", "commander_exit",
    "improve_result", "account_snapshot",
})

# Optional API polish when budget allows (still off by default)
_NOTIFY_TRADE_EVENTS: FrozenSet[str] = frozenset({
    "trade_opened", "trade_closed", "early_exit", "profit_hunt", "hot_swap",
})

# Commander / interactive — may use API when COUNCIL_NOTIFY_API_COPILOT=true
_NOTIFY_COPILOT_EVENTS: FrozenSet[str] = frozenset({
    "commander_chat", "vision_analysis", "analyze_positions", "daily_report",
    "daily_brief", "daily_self_eval", "mood", "status", "positions", "risk",
    "system", "account_eval", "account_brief",
})

# EOD digest uses one API call in compose — Telegram gets template only
_NOTIFY_DIGEST_EVENTS: FrozenSet[str] = frozenset({
    "daily_self_eval", "daily_brief", "daily_report",
})


def budget_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "COUNCIL_BUDGET_ENABLED", True))


def _notify_api_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "COUNCIL_NOTIFY_API_ENABLED", False))


def _copilot_api_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "COUNCIL_NOTIFY_API_COPILOT", True))


def _trade_notify_api_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "COUNCIL_NOTIFY_API_TRADES", False))


def _runtime_llm_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "COUNCIL_RUNTIME_LLM_ENABLED", False))


def _generative_rth_enabled(cfg: BotConfig) -> bool:
    return bool(getattr(cfg, "COUNCIL_GENERATIVE_RTH_ENABLED", False))


def _max_per_minute(cfg: BotConfig, purpose: str) -> int:
    if purpose == PURPOSE_DECISION:
        return int(getattr(cfg, "COUNCIL_DECISION_MAX_PER_MIN", 28))
    if purpose in (PURPOSE_NOTIFY, PURPOSE_COPILOT):
        return int(getattr(cfg, "COUNCIL_NOTIFY_MAX_PER_MIN", 6))
    if purpose == PURPOSE_RUNTIME:
        return int(getattr(cfg, "COUNCIL_RUNTIME_MAX_PER_MIN", 3))
    return int(getattr(cfg, "COUNCIL_MISC_MAX_PER_MIN", 4))


def _min_gap_sec(cfg: BotConfig, purpose: str) -> float:
    if purpose == PURPOSE_DECISION:
        return float(getattr(cfg, "COUNCIL_MIN_CALL_INTERVAL_SEC", 0.5))
    if purpose in (PURPOSE_NOTIFY, PURPOSE_COPILOT):
        return float(getattr(cfg, "COUNCIL_NOTIFY_MIN_GAP_SEC", 20.0))
    if purpose == PURPOSE_RUNTIME:
        return float(getattr(cfg, "COUNCIL_RUNTIME_MIN_GAP_SEC", 45.0))
    return float(getattr(cfg, "COUNCIL_MISC_MIN_GAP_SEC", 30.0))


def _prune_bucket(bucket: Deque[float], window_sec: float = 60.0) -> None:
    cutoff = time.time() - window_sec
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def _bucket_count(purpose: str) -> int:
    bucket = _minute_buckets.get(purpose)
    if not bucket:
        return 0
    with _lock:
        _prune_bucket(bucket)
        return len(bucket)


def record_council_api_call(purpose: str) -> None:
    now = time.time()
    with _lock:
        bucket = _minute_buckets.setdefault(purpose, deque(maxlen=200))
        bucket.append(now)
        _last_call_by_purpose[purpose] = now


def council_budget_snapshot(cfg: BotConfig) -> Dict[str, int]:
    return {
        "decision_1m": _bucket_count(PURPOSE_DECISION),
        "notify_1m": _bucket_count(PURPOSE_NOTIFY) + _bucket_count(PURPOSE_COPILOT),
        "runtime_1m": _bucket_count(PURPOSE_RUNTIME),
    }


def is_market_hours_active(cfg: BotConfig) -> bool:
    """Pre-market + RTH — no ambient generative API."""
    try:
        from core.market_hours import get_market_state
        return get_market_state(cfg) in ("open", "pre_market")
    except Exception:
        return True


def is_daily_digest_window(cfg: BotConfig) -> bool:
    """After close — one digest API call per day."""
    try:
        from core.market_hours import get_market_state
        return get_market_state(cfg) in ("after_hours", "closed", "overnight")
    except Exception:
        return False


def claim_daily_digest_slot(cfg: BotConfig, day_str: str) -> Tuple[bool, str]:
    """Reserve the single daily API slot (idempotent per ET calendar day)."""
    global _last_daily_digest_day
    if not getattr(cfg, "COUNCIL_DAILY_DIGEST_ENABLED", True):
        return False, "daily_digest_disabled"
    if not is_daily_digest_window(cfg):
        return False, "not_after_close"
    with _lock:
        if _last_daily_digest_day == day_str:
            return False, "already_digest_today"
        _last_daily_digest_day = day_str
    return True, "ok"


def classify_notify_event(event_type: str, *, copilot: bool = False) -> str:
    et = str(event_type or "").lower()
    if copilot or et in _NOTIFY_COPILOT_EVENTS:
        return PURPOSE_COPILOT
    return PURPOSE_NOTIFY


def notify_event_wants_api(
    cfg: BotConfig,
    event_type: str,
    *,
    copilot: bool = False,
) -> bool:
    """Whether this Telegram event type is allowed to hit the API at all."""
    et = str(event_type or "").lower()
    if et in _NOTIFY_DIGEST_EVENTS:
        return False
    if et in _NOTIFY_TEMPLATE_ONLY:
        return False
    if copilot or et in _NOTIFY_COPILOT_EVENTS:
        return _copilot_api_enabled(cfg)
    if et in _NOTIFY_TRADE_EVENTS:
        return _trade_notify_api_enabled(cfg)
    if not _notify_api_enabled(cfg):
        return False
    # Unknown events — only if global notify API enabled
    return _notify_api_enabled(cfg)


def should_use_council_api(
    cfg: BotConfig,
    purpose: str,
    *,
    event_type: Optional[str] = None,
    copilot: bool = False,
    force: bool = False,
) -> Tuple[bool, str]:
    """
    Return (allowed, reason). Decision purpose is always allowed unless
    minute bucket is saturated (protects council during burst).
    """
    if not getattr(cfg, "COUNCIL_ENABLED", True):
        return False, "council_disabled"
    if not budget_enabled(cfg):
        return True, "budget_off"

    purpose = str(purpose or PURPOSE_NOTIFY).lower()

    if purpose == PURPOSE_DECISION:
        if force:
            return True, "forced_decision"
        max_rpm = _max_per_minute(cfg, purpose)
        if _bucket_count(purpose) >= max_rpm:
            return False, f"decision_rpm_{max_rpm}"
        return True, "ok"

    if purpose == PURPOSE_GENERATIVE:
        if force:
            return True, "forced_generative"
        return False, "generative_off_use_daily_digest"

    if purpose == PURPOSE_DAILY_DIGEST:
        if not getattr(cfg, "COUNCIL_DAILY_DIGEST_ENABLED", True):
            return False, "daily_digest_disabled"
        if not is_daily_digest_window(cfg) and not force:
            return False, "not_digest_window"
        max_rpm = 1
        if _bucket_count(purpose) >= max_rpm:
            return False, "daily_digest_cap"
        return True, "ok"

    if purpose == PURPOSE_RUNTIME:
        if not _runtime_llm_enabled(cfg):
            return False, "runtime_llm_disabled"
        if is_market_hours_active(cfg) and not getattr(cfg, "COUNCIL_RUNTIME_RTH_ENABLED", False):
            return False, "runtime_blocked_rth"

    if purpose in (PURPOSE_NOTIFY, PURPOSE_COPILOT):
        if event_type is not None and not notify_event_wants_api(cfg, event_type, copilot=copilot):
            return False, "notify_template_only"
        if not _notify_api_enabled(cfg) and purpose == PURPOSE_NOTIFY:
            if not (copilot and _copilot_api_enabled(cfg)):
                return False, "notify_api_disabled"

    if purpose in (PURPOSE_JOURNAL, PURPOSE_OFFHOURS):
        if getattr(cfg, "COUNCIL_OFFHOURS_ONLY_JOURNAL", True) and is_market_hours_active(cfg):
            return False, "journal_blocked_rth"

    if force:
        return True, "forced"

    max_rpm = _max_per_minute(cfg, purpose)
    if _bucket_count(purpose) >= max_rpm:
        return False, f"rpm_cap_{purpose}_{max_rpm}"

    min_gap = _min_gap_sec(cfg, purpose)
    with _lock:
        last = _last_call_by_purpose.get(purpose, 0.0)
    if last > 0 and (time.time() - last) < min_gap:
        return False, f"gap_{purpose}_{min_gap:.0f}s"

    return True, "ok"


def providers_rate_limited(cfg: BotConfig) -> bool:
    """True when council client has both backends in 429 cooldown."""
    try:
        from core.council_client import get_council_client
        client = get_council_client(cfg)
        now = time.time()
        groq = bool(client.groq_key()) and now < getattr(client, "_groq_429_until", 0)
        gem = bool(client.gemini_key()) and now < getattr(client, "_gemini_429_until", 0)
        has_groq = bool(client.groq_key())
        has_gem = bool(client.gemini_key())
        if has_groq and has_gem:
            return groq and gem
        if has_groq:
            return groq
        if has_gem:
            return gem
    except Exception:
        pass
    return False
