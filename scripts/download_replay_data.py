#!/usr/bin/env python3
"""
Download daily OHLCV CSVs for replay training (no IB Gateway required).

Writes to data/replay/hanoon/{TICKER}.csv by default.
Set REPLAY_DATA_DIR to point backtest_engine at this folder.

Usage:
  python scripts/download_replay_data.py
  python scripts/download_replay_data.py --tickers SOFI,PLTR,MARA --years 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_TICKERS = (
    "SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB",
    "ASTS", "QS", "LCID", "RIVN",
    "ABBV", "ANET", "NVDA", "TSLA", "SPY", "QQQ",
)


def _fetch_yfinance(ticker: str, years: int) -> "pd.DataFrame":
    import yfinance as yf
    import pandas as pd

    period = f"{years}y" if years <= 10 else "max"
    df = yf.download(
        ticker,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    df = df.rename(columns={"adj close": "close"})
    df.index = pd.to_datetime(df.index)
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Download replay CSVs via yfinance")
    parser.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TICKERS),
        help="Comma-separated symbols",
    )
    parser.add_argument("--years", type=int, default=3, help="History length")
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "replay" / "hanoon"),
        help="Output directory",
    )
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok, fail = 0, 0
    for ticker in tickers:
        try:
            df = _fetch_yfinance(ticker, args.years)
            path = out_dir / f"{ticker}.csv"
            df.to_csv(path, index_label="Date")
            print(f"✅ {ticker}: {len(df)} daily bars → {path}")
            ok += 1
        except Exception as exc:
            print(f"❌ {ticker}: {exc}")
            fail += 1

    print(f"\nDone: {ok} ok, {fail} failed")
    print(f"Set: export REPLAY_DATA_DIR=\"{out_dir.parent}\"")
    print(f"     export REPLAY_STREAM=true REPLAY_TIME_DILATION_MS=50")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
