#!/usr/bin/env python3
"""
core/market_data_learning.py — Learn from IB market-data failures.

Errors 162 (no HMDS data), 420 (no permissions), etc. are recorded so the bot
stops wasting streams on untradeable names and focuses profit hunting on clean
tickers with live data.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

DENYLIST_PATH = Path("models/market_data_denylist.json")
LEDGER_PATH = Path("models/market_data_failures.jsonl")

# IB codes that mean "this ticker is not profit-huntable right now"
MARKET_DATA_ERROR_CODES = frozenset({
    162,   # HMDS query returned no data
    200,   # No security definition
    354,   # No market data permissions
    420,   # Invalid real-time query / no permissions (ARCAEDGE, PINK, etc.)
    10314, # End date/time invalid for contract
})

_lock = threading.Lock()
_handlers: List[Callable[[str, int, str, Dict[str, Any]], None]] = []


def register_market_data_handler(
    fn: Callable[[str, int, str, Dict[str, Any]], None],
) -> None:
    _handlers.append(fn)


def _load_denylist() -> Dict[str, Dict[str, Any]]:
    if not DENYLIST_PATH.exists():
        return {}
    try:
        return json.loads(DENYLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_denylist(data: Dict[str, Dict[str, Any]]) -> None:
    DENYLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        DENYLIST_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _append_ledger(row: Dict[str, Any]) -> None:
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def extract_ticker_from_error(
    contract: Any,
    error_string: str,
) -> str:
    if contract is not None:
        sym = getattr(contract, "symbol", None) or getattr(contract, "localSymbol", None)
        if sym:
            return str(sym).upper()
    if error_string:
        m = re.search(r"([A-Z][A-Z0-9.-]{0,9})@(?:SMART|NASDAQ|NYSE|ARCA|BATS|PINK)", error_string)
        if m:
            return m.group(1).upper()
        m = re.search(r"no data:\s*([A-Z][A-Z0-9.-]{0,9})@", error_string, re.I)
        if m:
            return m.group(1).upper()
        m = re.search(r"symbol='([A-Z][A-Z0-9.-]{0,9})'", error_string, re.I)
        if m:
            return m.group(1).upper()
    return ""


def is_ib_scanner_cancel_162(message: str) -> bool:
    return "scanner subscription cancelled" in (message or "").lower()


def is_hmds_transient_message(message: str) -> bool:
    """HMDS inactive, cancelled, or timeout — not 'this ticker has no data'."""
    msg = (message or "").lower()
    if "scanner subscription cancelled" in msg:
        return True
    if "historical data query cancelled" in msg:
        return True
    if "reqhistoricaldata" in msg and "timeout" in msg:
        return True
    if "timed out" in msg or "not connected" in msg:
        return True
    if "hmds" in msg and any(
        k in msg for k in ("cancel", "inactive", "unavailable", "busy", "timeout")
    ):
        return True
    if "api historical data" in msg and "cancel" in msg:
        return True
    return False


def classify_failure(code: int, message: str) -> str:
    msg = (message or "").lower()
    if code == 420 or "arcaedge" in msg or "no market data permissions" in msg:
        return "no_md_permission"
    if code == 162 or "hmds" in msg or "no data" in msg:
        return "no_historical_data"
    if code == 200 or "security definition" in msg:
        return "no_contract"
    if "pink" in msg or "otc" in msg:
        return "otc_limited"
    return f"ib_{code}"


def cooldown_sec(cfg: BotConfig, failures: int) -> float:
    base = float(getattr(cfg, "MARKET_DATA_SKIP_COOLDOWN_SEC", 300.0))
    if failures <= 1:
        return base
    if failures == 2:
        return base * 3
    if failures <= 4:
        return base * 12
    return base * 48


def is_market_data_blocked(
    cfg: BotConfig,
    ticker: str,
) -> Tuple[bool, str]:
    """True when ticker is on cooldown from repeated MD failures."""
    if not ticker:
        return False, ""
    if not getattr(cfg, "MARKET_DATA_LEARN_ENABLED", True):
        return False, ""
    entry = _load_denylist().get(ticker.upper())
    if not entry:
        return False, ""
    skip_until = float(entry.get("skip_until", 0) or 0)
    if skip_until > time.time():
        reason = str(entry.get("last_reason", entry.get("pattern", "md_failure")))[:120]
        return True, reason
    return False, ""


def clear_competing_session_blocks() -> int:
    """Remove 10197 denylist entries so live MD can retry after force-live."""
    denylist = _load_denylist()
    removed = 0
    for ticker in list(denylist.keys()):
        entry = denylist[ticker]
        if int(entry.get("last_code", 0)) == 10197 or entry.get("pattern") == "ib_10197":
            denylist.pop(ticker, None)
            removed += 1
    if removed:
        _save_denylist(denylist)
        log.info(f"  🔓 Cleared {removed} ticker(s) blocked by IB 10197 (competing session)")
    return removed


def filter_tradeable_tickers(cfg: BotConfig, tickers: List[str]) -> List[str]:
    out = []
    for t in tickers:
        blocked, reason = is_market_data_blocked(cfg, t)
        if blocked:
            log.debug(f"  ⏭ MD skip {t}: {reason[:80]}")
            continue
        out.append(t)
    return out


def record_market_data_failure(
    cfg: BotConfig,
    *,
    ticker: str,
    code: int,
    message: str,
    exchange: str = "",
    primary_exchange: str = "",
    source: str = "ib_error",
) -> Dict[str, Any]:
    """
    Record IB market-data failure → denylist + experience buffer + ledger.
    Returns the updated denylist entry.
    """
    ticker = (ticker or "").upper()
    if not ticker or not getattr(cfg, "MARKET_DATA_LEARN_ENABLED", True):
        return {}

    pattern = classify_failure(code, message)
    now = time.time()
    denylist = _load_denylist()
    prev = denylist.get(ticker, {})
    failures = int(prev.get("failures", 0)) + 1
    cd = cooldown_sec(cfg, failures)
    state = ""
    transient = False
    try:
        from core.market_hours import get_market_state
        state = get_market_state(cfg)
        from core.rth_session import is_transient_md_failure
        transient = is_transient_md_failure(
            cfg, code=code, pattern=pattern, state=state, message=message,
        )
        if transient:
            cd = min(cd, float(getattr(cfg, "MD_TRANSIENT_COOLDOWN_SEC", 90.0)))
    except Exception:
        pass
    skip_until = now + cd

    entry = {
        "ticker": ticker,
        "failures": failures,
        "last_code": code,
        "last_reason": (message or "")[:300],
        "pattern": pattern,
        "exchange": exchange or prev.get("exchange", ""),
        "primary_exchange": primary_exchange or prev.get("primary_exchange", ""),
        "skip_until": skip_until,
        "skip_until_iso": datetime.fromtimestamp(skip_until, tz=timezone.utc).isoformat(),
        "first_seen": prev.get("first_seen") or datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "market_state": state,
        "transient": transient,
    }
    denylist[ticker] = entry
    _save_denylist(denylist)

    row = {**entry, "event": "market_data_failure", "code": code}
    _append_ledger(row)

    try:
        from core.ai_learning_policy import record_failure_for_learning
        record_failure_for_learning(
            cfg,
            ticker=ticker,
            reason=f"MD {code}: {message[:200]}",
            event="market_data_failure",
            extra={
                "ib_code": code,
                "pattern": pattern,
                "failures": failures,
                "skip_sec": cd,
                "exchange": exchange,
                "primary_exchange": primary_exchange,
            },
        )
    except Exception:
        pass

    log.info(
        f"  📚 MD LEARN {ticker}: IB {code} ({pattern}) — "
        f"skip {cd / 60:.0f}m | failures={failures}"
        + (" | transient" if transient else "")
    )

    for h in _handlers:
        try:
            h(ticker, code, message, entry)
        except Exception:
            pass

    return entry


def handle_ib_market_data_error(
    cfg: BotConfig,
    req_id: int,
    error_code: int,
    error_string: str,
    contract: Any = None,
) -> Optional[Dict[str, Any]]:
    """Called from IBConnector._on_error for market-data failure codes."""
    if error_code not in MARKET_DATA_ERROR_CODES:
        return None
    if error_code == 162 and is_ib_scanner_cancel_162(error_string):
        return None
    if error_code == 162 and is_hmds_transient_message(error_string):
        if getattr(cfg, "MD_SOFT_FAIL_HMDS", True):
            ticker = extract_ticker_from_error(contract, error_string)
            if ticker:
                log.debug(
                    f"HMDS transient 162 {ticker}: {str(error_string)[:100]} "
                    "(live streams only — not denylisting)"
                )
            return None
    ticker = extract_ticker_from_error(contract, error_string)
    if not ticker:
        return None
    exchange = getattr(contract, "exchange", "") if contract else ""
    primary = getattr(contract, "primaryExchange", "") if contract else ""
    return record_market_data_failure(
        cfg,
        ticker=ticker,
        code=error_code,
        message=error_string,
        exchange=str(exchange or ""),
        primary_exchange=str(primary or ""),
        source="ib_error",
    )


def record_fetch_failure(
    cfg: BotConfig,
    ticker: str,
    exc: Exception,
    *,
    bar_size: str = "",
) -> Optional[Dict[str, Any]]:
    """Record when fetch_historical raises or returns empty — same learning path."""
    msg = str(exc)
    if getattr(cfg, "MD_SOFT_FAIL_HMDS", True) and (
        is_hmds_transient_message(msg)
        or "timed out" in msg.lower()
        or "not connected" in msg.lower()
    ):
        log.debug(f"HMDS prefetch transient for {ticker}: {msg[:120]}")
        return None
    code = 162 if "no historical" in msg.lower() or "no data" in msg.lower() else 200
    return record_market_data_failure(
        cfg,
        ticker=ticker,
        code=code,
        message=msg[:300],
        source=f"fetch:{bar_size or 'historical'}",
    )


def clear_hmds_transient_blocks() -> int:
    """Remove denylist entries from flaky HMDS 162 (cancelled/timeout), not bad tickers."""
    return clear_reconnect_transient_blocks()


def clear_reconnect_transient_blocks() -> int:
    """Clear 162 skips caused by disconnect/reclaim/timeouts — not structurally bad tickers."""
    denylist = _load_denylist()
    removed = 0
    for ticker in list(denylist.keys()):
        entry = denylist[ticker]
        if int(entry.get("last_code", 0)) != 162:
            continue
        failures = int(entry.get("failures", 0))
        if failures > 3:
            continue
        pattern = str(entry.get("pattern", ""))
        if pattern != "no_historical_data":
            continue
        reason = str(entry.get("last_reason", ""))
        source = str(entry.get("source", ""))
        transient = (
            entry.get("transient")
            or is_hmds_transient_message(reason)
            or (
                source.startswith("fetch:")
                and (
                    "possible causes" in reason.lower()
                    or "timeout" in reason.lower()
                    or "cancel" in reason.lower()
                    or "not connected" in reason.lower()
                )
            )
        )
        if transient:
            denylist.pop(ticker, None)
            removed += 1
    if removed:
        _save_denylist(denylist)
        log.info(f"  🔓 Cleared {removed} ticker(s) blocked by transient HMDS 162")
    return removed


def clear_transient_md_blocks(cfg: BotConfig) -> list:
    """
    Clear short-lived HMDS denylist entries — call at RTH open so pre-market
  timeouts do not block liquid names for the rest of the day.
    """
    if not getattr(cfg, "MARKET_DATA_LEARN_ENABLED", True):
        return []
    denylist = _load_denylist()
    cleared: list = []
    for ticker, entry in list(denylist.items()):
        pattern = str(entry.get("pattern", ""))
        failures = int(entry.get("failures", 0))
        if pattern == "no_historical_data" and failures <= 4:
            cleared.append(ticker)
            denylist.pop(ticker, None)
    if cleared:
        _save_denylist(denylist)
        log.info(
            f"  🔓 RTH open — cleared transient MD skips: {', '.join(cleared[:12])}"
            + ("…" if len(cleared) > 12 else "")
        )
    return cleared


def denylist_stats() -> Dict[str, Any]:
    data = _load_denylist()
    now = time.time()
    active = sum(1 for e in data.values() if float(e.get("skip_until", 0) or 0) > now)
    patterns: Dict[str, int] = {}
    for e in data.values():
        p = str(e.get("pattern", "unknown"))
        patterns[p] = patterns.get(p, 0) + 1
    return {
        "total": len(data),
        "active_skips": active,
        "patterns": patterns,
    }


def prompt_block(cfg: BotConfig) -> str:
    stats = denylist_stats()
    active = [
        t for t, e in _load_denylist().items()
        if float(e.get("skip_until", 0) or 0) > time.time()
    ]
    sample = ",".join(active[:8]) if active else "none"
    return (
        f"Market-data learning: {stats['active_skips']} tickers skipped (no clean IB data). "
        f"Avoid for profit hunting: [{sample}]. "
        f"Patterns: {stats.get('patterns', {})}. "
        f"Only hunt names with live bars + permissions."
    )
