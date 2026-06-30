#!/usr/bin/env python3
"""
core/replay_consumption.py — Forward-only replay: never re-train on the same bars.

After each replay session:
  1. Record walked timestamp ranges per ticker (ledger in models/)
  2. Trim consumed rows from intraday CSVs (free disk)
  3. Next session loads only unconsumed bars

Learning artifacts (models/, halim/data/training/) are kept.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import pandas as pd

from core.notify import log
from core.replay_data import resolve_replay_dir

if TYPE_CHECKING:
    from core.replay_market_hub import ReplayMarketHub

LEDGER_PATH = Path("models/replay_consumption.jsonl")
MIN_BARS = 20


def skip_consumed_enabled() -> bool:
    return os.getenv("REPLAY_SKIP_CONSUMED", "true").lower() in ("1", "true", "yes")


def trim_on_stop_enabled() -> bool:
    return os.getenv("REPLAY_TRIM_CONSUMED_ON_STOP", "true").lower() in ("1", "true", "yes")


def purge_all_on_stop() -> bool:
    return os.getenv("REPLAY_PURGE_ALL_ON_STOP", "false").lower() in ("1", "true", "yes")


def _utc(ts: Any) -> pd.Timestamp:
    return pd.Timestamp(ts).tz_convert("UTC")


def file_fingerprint(path: Path) -> str:
    """Content-aware fingerprint — invalidates ledger when CSV is re-downloaded."""
    if not path.is_file():
        return ""
    try:
        st = path.stat()
        head = ""
        tail = ""
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.strip() and not line.startswith("datetime"):
                    head = line.strip()
                    break
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode("utf-8", errors="ignore").strip().splitlines()[-1]
        raw = f"{path.name}:{st.st_size}:{st.st_mtime_ns}:{head}:{tail}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(str(path).encode()).hexdigest()[:16]


def _append_ledger(row: Dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")


def load_ledger() -> List[Dict[str, Any]]:
    if not LEDGER_PATH.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(LEDGER_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return rows


def _merge_intervals(
    intervals: List[Tuple[pd.Timestamp, pd.Timestamp]],
) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    if not intervals:
        return []
    sorted_iv = sorted((_utc(a), _utc(b)) for a, b in intervals)
    merged: List[Tuple[pd.Timestamp, pd.Timestamp]] = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + pd.Timedelta(minutes=1):
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def consumed_intervals(
    ticker: str,
    *,
    file_fp: str,
) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    sym = ticker.upper()
    iv: List[Tuple[pd.Timestamp, pd.Timestamp]] = []
    for row in load_ledger():
        if row.get("ticker", "").upper() != sym:
            continue
        if file_fp and row.get("file_fp") and row.get("file_fp") != file_fp:
            continue
        try:
            iv.append((_utc(row["start"]), _utc(row["end"])))
        except Exception:
            continue
    return _merge_intervals(iv)


def filter_unconsumed_bars(
    df: pd.DataFrame,
    ticker: str,
    path: Path,
) -> Tuple[pd.DataFrame, int]:
    """Drop bars already trained in prior replay sessions."""
    if df is None or df.empty or not skip_consumed_enabled():
        return df, 0
    fp = file_fingerprint(path)
    intervals = consumed_intervals(ticker, file_fp=fp)
    if not intervals:
        return df, 0
    mask = pd.Series(True, index=df.index)
    for start, end in intervals:
        mask &= ~((df.index >= start) & (df.index <= end))
    skipped = int((~mask).sum())
    out = df.loc[mask]
    if skipped > 0:
        log.info(
            f"  ⏭ {ticker.upper()}: skipping {skipped:,} already-trained bars "
            f"({len(out):,} fresh remaining)"
        )
    return out, skipped


def _min_steps_for_trim() -> int:
    return max(1, int(os.getenv("REPLAY_MIN_STEPS_FOR_TRIM", "50")))


def walked_ticker_ranges(hub: "ReplayMarketHub") -> Dict[str, Tuple[pd.Timestamp, pd.Timestamp]]:
    """Per-ticker min/max timestamps actually walked this session."""
    end_i = max(0, hub._idx)
    out: Dict[str, Tuple[pd.Timestamp, pd.Timestamp]] = {}
    for ticker in hub.tickers:
        times: List[pd.Timestamp] = []
        for i in range(end_i):
            ts, group = hub._timeline[i]
            if ticker in group:
                times.append(_utc(ts))
        if times:
            out[ticker] = (min(times), max(times))
    return out


def record_replay_session_consumed(
    hub: "ReplayMarketHub",
    *,
    trigger: str = "replay_teardown",
) -> Dict[str, Any]:
    """Append walked ranges to consumption ledger."""
    root = hub.root or resolve_replay_dir()
    ranges = walked_ticker_ranges(hub)
    if not ranges:
        return {"ok": False, "reason": "no_walked_bars"}

    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recorded: List[str] = []
    for ticker, (start, end) in ranges.items():
        path = (root / "intraday" / f"{ticker.upper()}_1min.csv") if root else None
        fp = file_fingerprint(path) if path and path.is_file() else ""
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "trigger": trigger,
            "ticker": ticker.upper(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "file_fp": fp,
            "complete": bool(getattr(hub, "timeline_complete", hub.finished)),
            "steps": hub._idx,
        }
        _append_ledger(row)
        recorded.append(ticker.upper())

    result = {
        "ok": True,
        "session_id": session_id,
        "tickers": recorded,
        "complete": bool(getattr(hub, "timeline_complete", hub.finished)),
        "steps_walked": hub._idx,
        "trigger": trigger,
    }
    complete = bool(getattr(hub, "timeline_complete", hub.finished))
    log.info(
        f"📒 Replay consumption recorded — {len(recorded)} tickers "
        f"({hub._idx:,} steps · {'full timeline' if complete else 'partial'})"
    )
    return result


def trim_consumed_replay_bars(
    root: Optional[Path] = None,
    *,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Physically remove already-trained bars from intraday CSVs."""
    if not trim_on_stop_enabled():
        return {"ok": True, "skipped": True, "reason": "trim_disabled"}

    root = root or resolve_replay_dir()
    if root is None:
        return {"ok": False, "error": "no_replay_dir"}

    intraday = root / "intraday"
    if not intraday.is_dir():
        return {"ok": True, "files": 0, "bars_removed": 0}

    result: Dict[str, Any] = {
        "ok": True,
        "root": str(root),
        "trimmed": [],
        "deleted_empty": [],
        "bars_removed": 0,
        "bytes_freed": 0,
    }

    if verbose:
        print(f"✂️  Trimming already-trained bars from {intraday}…", flush=True)

    for path in sorted(intraday.glob("*_1min.csv")):
        sym = path.stem.replace("_1min", "").upper()
        try:
            before_size = path.stat().st_size
            df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
            df.index = pd.to_datetime(df.index, utc=True)
            before = len(df)
            fp = file_fingerprint(path)
            intervals = consumed_intervals(sym, file_fp=fp)
            if not intervals:
                continue
            mask = pd.Series(True, index=df.index)
            for start, end in intervals:
                mask &= ~((df.index >= start) & (df.index <= end))
            df = df.loc[mask].sort_index()
            removed = before - len(df)
            if removed <= 0:
                continue
            if len(df) < MIN_BARS:
                result["bytes_freed"] += before_size
                path.unlink()
                result["deleted_empty"].append(sym)
                result["bars_removed"] += removed
                if verbose:
                    print(f"  🗑  {sym}: removed all {before:,} trained bars (file deleted)", flush=True)
                continue
            df.to_csv(path, index_label="datetime")
            after_size = path.stat().st_size
            result["bytes_freed"] += max(0, before_size - after_size)
            result["bars_removed"] += removed
            result["trimmed"].append({"ticker": sym, "removed": removed, "remaining": len(df)})
            if verbose:
                print(
                    f"  ✂️  {sym}: removed {removed:,} trained bars · {len(df):,} fresh left",
                    flush=True,
                )
        except Exception as exc:
            log.debug(f"trim {path}: {exc}")

    if verbose:
        mb = result.get("bytes_freed", 0) / (1024 * 1024)
        print(
            f"✂️  Trim done — {result['bars_removed']:,} bars removed · "
            f"~{mb:.1f} MB freed · {len(result['deleted_empty'])} empty files deleted",
            flush=True,
        )
    return result


def farm_unconsumed_stats(root: Optional[Path] = None) -> Dict[str, Any]:
    """Bars still available for fresh replay training."""
    root = root or resolve_replay_dir()
    if root is None:
        return {"ok": False, "tickers": 0, "unconsumed_bars": 0}
    intraday = root / "intraday"
    per_ticker: Dict[str, int] = {}
    total = 0
    if intraday.is_dir():
        for path in sorted(intraday.glob("*_1min.csv")):
            sym = path.stem.replace("_1min", "").upper()
            try:
                df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
                df.index = pd.to_datetime(df.index, utc=True)
                fresh, _ = filter_unconsumed_bars(df, sym, path)
                n = len(fresh)
            except Exception:
                n = 0
            if n >= MIN_BARS:
                per_ticker[sym] = n
                total += n
    vals = list(per_ticker.values()) or [0]
    return {
        "ok": True,
        "root": str(root),
        "tickers": len(per_ticker),
        "unconsumed_bars": total,
        "min_bars": min(vals),
        "max_bars": max(vals),
        "per_ticker": per_ticker,
    }


def farm_has_unconsumed_data(
    root: Optional[Path] = None,
    *,
    min_total_bars: int = 500,
) -> bool:
    st = farm_unconsumed_stats(root)
    return bool(st.get("ok")) and int(st.get("unconsumed_bars", 0)) >= min_total_bars


def farm_fully_consumed(root: Optional[Path] = None) -> bool:
    st = farm_unconsumed_stats(root)
    return bool(st.get("ok")) and int(st.get("unconsumed_bars", 0)) < MIN_BARS


def finalize_replay_session(
    hub: Optional["ReplayMarketHub"] = None,
    *,
    trigger: str = "replay_teardown",
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    End-of-session cleanup: record → trim consumed → optional full purge.
  Called from replay teardown and stop scripts.
    """
    result: Dict[str, Any] = {"trigger": trigger, "steps": {}}

    steps_walked = hub._idx if hub is not None else 0
    min_trim = _min_steps_for_trim()
    skip_trim = hub is not None and steps_walked < min_trim and not getattr(
        hub, "timeline_complete", hub.finished
    )

    if hub is not None and not skip_trim:
        try:
            result["steps"]["record"] = record_replay_session_consumed(hub, trigger=trigger)
        except Exception as exc:
            result["steps"]["record"] = {"ok": False, "error": str(exc)[:120]}
    elif skip_trim:
        result["steps"]["record"] = {
            "ok": False,
            "skipped": True,
            "reason": f"only {steps_walked} steps (< {min_trim}) — CSVs kept for retry",
        }
        if verbose:
            print(
                f"⏭  Replay too short ({steps_walked} steps) — skipping trim/purge "
                f"(need ≥{min_trim} or full timeline)",
                flush=True,
            )

    if skip_trim:
        result["steps"]["trim"] = {"ok": True, "skipped": True, "reason": "session_too_short"}
    else:
        try:
            result["steps"]["trim"] = trim_consumed_replay_bars(verbose=verbose)
        except Exception as exc:
            result["steps"]["trim"] = {"ok": False, "error": str(exc)[:120]}

    result["steps"]["unconsumed"] = farm_unconsumed_stats()

    if not skip_trim and (purge_all_on_stop() or farm_fully_consumed()):
        try:
            from core.replay_data_housekeeping import purge_replay_farm
            result["steps"]["purge"] = purge_replay_farm(verbose=verbose, force=True)
        except Exception as exc:
            result["steps"]["purge"] = {"ok": False, "error": str(exc)[:120]}

    return result
