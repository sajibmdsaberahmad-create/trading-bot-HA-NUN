#!/usr/bin/env python3
"""
core/replay_data.py — Load daily OHLCV CSVs from REPLAY_DATA_DIR (external or local).

Supports Yahoo-export format (extra header rows) and standard Date,Open,High,Low,Close,Volume.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import pandas as pd

from core.notify import log

DEFAULT_REPLAY_SUBDIRS = ("intraday", "hanoon", "SP500_Data_10Y", "archive", "")


def resolve_replay_dir(cfg=None) -> Optional[Path]:
    """First existing replay root: REPLAY_DATA_DIR env, then data/replay, then Downloads."""
    env = os.getenv("REPLAY_DATA_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p

    repo = Path(__file__).resolve().parents[1]
    local = repo / "data" / "replay"
    if local.is_dir():
        return local

    downloads = Path.home() / "Downloads"
    for name in ("Datasets for trading ", "Datasets for trading"):
        candidate = downloads / name
        if candidate.is_dir():
            return candidate
    return None


def _ticker_paths(root: Path, ticker: str) -> List[Path]:
    t = ticker.upper().strip()
    variants = [t, t.lower(), t.capitalize()]
    paths: List[Path] = []
    for sub in DEFAULT_REPLAY_SUBDIRS:
        base = root / sub if sub else root
        for v in variants:
            paths.append(base / f"{v}.csv")
    for v in variants:
        paths.append(root / f"{v}.csv")
    return paths


def find_replay_csv(root: Path, ticker: str) -> Optional[Path]:
    t = ticker.upper().strip()
    intraday = root / "intraday" / f"{t}_1min.csv"
    if intraday.is_file():
        return intraday
    for p in _ticker_paths(root, ticker):
        if p.is_file():
            return p
    return None


def _normalize_yahoo_export(raw: pd.DataFrame) -> pd.DataFrame:
    """Parse Yahoo-style CSV with Price/Ticker header rows."""
    if "Date" in raw.columns and raw["Date"].astype(str).str.match(r"\d{4}").any():
        df = raw.copy()
    else:
        # First column may be unnamed date column
        col0 = raw.columns[0]
        if col0 in ("Price", "Unnamed: 0"):
            df = raw.rename(columns={col0: "Date"})
        else:
            df = raw.copy()
        # Drop metadata rows
        mask = df["Date"].astype(str).str.match(r"^\d{4}", na=False)
        df = df.loc[mask].copy()

    rename = {}
    for c in df.columns:
        cl = str(c).lower()
        if cl in ("close", "adj close", "adj_close"):
            rename[c] = "close"
        elif cl == "open":
            rename[c] = "open"
        elif cl == "high":
            rename[c] = "high"
        elif cl == "low":
            rename[c] = "low"
        elif cl == "volume":
            rename[c] = "volume"
        elif cl == "date":
            rename[c] = "date"
    df = df.rename(columns=rename)
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(df.columns)):
        raise ValueError(f"Missing OHLCV columns after normalize: {list(df.columns)}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _apply_date_range(
    df: pd.DataFrame,
    start: Optional[str],
    end: Optional[str],
) -> pd.DataFrame:
    """Inclusive start/end date filters (end covers full calendar day)."""
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        df = df[df.index <= end_ts]
    return df


def load_replay_csv(
    ticker: str,
    *,
    root: Optional[Path] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Load and normalize one ticker CSV from replay directory."""
    root = root or resolve_replay_dir()
    if root is None:
        raise FileNotFoundError(
            "No REPLAY_DATA_DIR — set env or run scripts/download_replay_data.py"
        )
    path = find_replay_csv(root, ticker)
    if path is None:
        raise FileNotFoundError(f"No replay CSV for {ticker} under {root}")

    raw = pd.read_csv(path)
    df = _normalize_yahoo_export(raw)
    df = df.set_index("date")
    df.index.name = "date"

    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        end_ts = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        df = df[df.index <= end_ts]

    if len(df) < 20:
        raise ValueError(f"Insufficient replay rows for {ticker}: {len(df)}")
    log.debug(f"Replay loaded {ticker}: {len(df)} bars from {path}")
    return df


def list_replay_tickers(root: Optional[Path] = None) -> List[str]:
    root = root or resolve_replay_dir()
    if root is None:
        return []
    tickers: set = set()
    for sub in DEFAULT_REPLAY_SUBDIRS:
        base = root / sub if sub else root
        if not base.is_dir():
            continue
        for p in base.glob("*.csv"):
            if p.name.lower() in ("book1.csv", "commodity_futures.csv"):
                continue
            stem = p.stem.upper()
            if stem.endswith("_1MIN"):
                stem = stem[:-5]
            tickers.add(stem)
    return sorted(tickers)


def load_replay_intraday(
    ticker: str,
    *,
    root: Optional[Path] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Load 1-min CSV (datetime index) from intraday/ subfolder."""
    root = root or resolve_replay_dir()
    if root is None:
        raise FileNotFoundError("No REPLAY_DATA_DIR")
    path = root / "intraday" / f"{ticker.upper()}_1min.csv"
    if not path.is_file():
        return load_replay_csv(ticker, root=root, start=start, end=end)
    df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
    df.index = pd.to_datetime(df.index, utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_index()
    df = _apply_date_range(df, start, end)
    try:
        from core.replay_consumption import filter_unconsumed_bars, skip_consumed_enabled
        if skip_consumed_enabled():
            df, _skipped = filter_unconsumed_bars(df, ticker, path)
    except Exception as exc:
        log.debug(f"Replay consumption filter skip {ticker}: {exc}")
    if len(df) < 20:
        raise ValueError(f"Insufficient intraday replay rows for {ticker}: {len(df)}")
    log.debug(f"Intraday replay {ticker}: {len(df)} bars from {path}")
    return df


def iter_replay_bars(
    ticker: str,
    *,
    root: Optional[Path] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    intraday: bool = True,
) -> Iterator[dict]:
    """Yield one bar dict per row (source=replay for buffer tagging)."""
    if intraday:
        try:
            df = load_replay_intraday(ticker, root=root, start=start, end=end)
        except (FileNotFoundError, ValueError):
            df = load_replay_csv(ticker, root=root, start=start, end=end)
    else:
        df = load_replay_csv(ticker, root=root, start=start, end=end)
    for ts, row in df.iterrows():
        ts_et = pd.Timestamp(ts).tz_convert("America/New_York")
        yield {
            "datetime": ts,
            "date": ts_et.strftime("%Y-%m-%d"),
            "time": ts_et.strftime("%H:%M:%S"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
            "source": "replay_live",
        }
