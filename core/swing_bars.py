#!/usr/bin/env python3
"""Historical bar fetch for swing intel — per-ticker, IB-safe."""
from __future__ import annotations

from typing import Any, List, Optional, TYPE_CHECKING

import pandas as pd

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def bars_to_closes(bars: Any) -> List[float]:
    """Extract positive closes from DataFrame, dict rows, or bar objects."""
    if bars is None:
        return []
    if isinstance(bars, pd.DataFrame):
        if bars.empty or "close" not in bars.columns:
            return []
        return [float(x) for x in bars["close"].astype(float).tolist() if float(x) > 0]
    out: List[float] = []
    for b in bars or []:
        if isinstance(b, dict):
            c = float(b.get("close", 0) or 0)
        else:
            c = float(getattr(b, "close", 0) or 0)
        if c > 0:
            out.append(c)
    return out


def bars_len(bars: Any) -> int:
    if bars is None:
        return 0
    if isinstance(bars, pd.DataFrame):
        return len(bars)
    try:
        return len(bars)
    except TypeError:
        return 0


def fetch_swing_bars(
    runner: Any,
    sym: str,
    bar_size: str,
    duration: str,
) -> pd.DataFrame:
    """
    Multi-TF bars for swing analysis. Uses per-ticker stream cache when present,
    else one-shot IB historical on the main thread.
    """
    sym = (sym or "").upper()
    if not sym:
        return _empty_df()

    dm = (getattr(runner, "_target_monitors", None) or {}).get(sym)
    if dm is None:
        base = getattr(runner, "data", None)
        if base is not None and str(getattr(getattr(base, "cfg", None), "TICKER", "")).upper() == sym:
            dm = base

    if dm is None:
        conn = getattr(runner, "conn", None)
        ib = getattr(runner, "ib", None)
        if conn is None or ib is None:
            return _empty_df()
        try:
            from core.config import BotConfig
            from core.data import DataManager

            dm = DataManager(conn, BotConfig(TICKER=sym))
        except Exception as exc:
            log.debug(f"swing bars dm {sym}: {exc}")
            return _empty_df()

    try:
        from core.ib_sync import ib_blocking_calls_safe

        ib = getattr(runner, "ib", None)
        if not ib_blocking_calls_safe(ib):
            fast = dm.get_bar_dataframe() if hasattr(dm, "get_bar_dataframe") else None
            if fast is not None and len(fast) >= 10:
                return fast
            return _empty_df()
        return dm.fetch_historical(
            duration=duration,
            bar_size=bar_size,
            use_rth=True,
            quiet=True,
        )
    except Exception as exc:
        log.debug(f"swing bars {sym} {bar_size}: {exc}")
        return _empty_df()
