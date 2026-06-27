#!/usr/bin/env python3
"""
Download 1-minute OHLCV CSVs from IB Gateway for replay-live training.

Writes: data/replay/intraday/{TICKER}_1min.csv
Does NOT touch live scalper code or models.

Usage:
  PYTHONPATH=. python scripts/download_ib_replay_data.py
  PYTHONPATH=. python scripts/download_ib_replay_data.py --tickers SOFI,PLTR --days 30

Uses CLIENT_ID / IB_CLIENT_ID env (default 1) — same as live HANOON. Disconnects when done.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_TICKERS = (
    "SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB",
    "ASTS", "QS", "LCID", "RIVN", "NVDA", "TSLA", "SPY", "QQQ",
)

# IB HMDS: 1-min bars max ~1 week per request
CHUNK_DAYS = 5


def _progress(msg: str) -> None:
    print(msg, flush=True)


def _fetch_chunks(
    dm,
    days: int,
    use_rth: bool,
    *,
    ticker: str = "",
    on_chunk=None,
) -> pd.DataFrame:
    """Pull 1-min bars in 5-day chunks going backward from now."""
    frames: list[pd.DataFrame] = []
    end = datetime.now(timezone.utc)
    remaining = days
    total_chunks = max(1, (days + CHUNK_DAYS - 1) // CHUNK_DAYS)
    chunk_i = 0
    while remaining > 0:
        chunk_i += 1
        chunk = min(CHUNK_DAYS, remaining)
        duration = f"{chunk} D"
        end_str = end.strftime("%Y%m%d-%H:%M:%S")
        if on_chunk:
            on_chunk(chunk_i, total_chunks, end_str)
        contract = dm._get_contract()
        bars = dm.ib.reqHistoricalData(
            contract,
            endDateTime=end_str,
            durationStr=duration,
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=use_rth,
            formatDate=1,
            keepUpToDate=False,
            timeout=int(getattr(dm.cfg, "HMDS_FETCH_TIMEOUT_SEC", 20)),
        )
        if not bars:
            break
        df = pd.DataFrame(
            [
                {
                    "datetime": b.date,
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": int(b.volume),
                }
                for b in bars
            ]
        )
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        frames.append(df)
        oldest = df["datetime"].min()
        end = oldest.to_pydatetime().replace(tzinfo=None) - timedelta(minutes=1)
        remaining -= chunk
        if bars:
            _progress(
                f"    └ chunk {chunk_i}/{total_chunks}: +{len(bars)} bars "
                f"(total {sum(len(f) for f in frames):,} so far)"
            )
        time.sleep(0.5)  # pacing for IB rate limits

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["datetime"]).sort_values("datetime")
    out = out.set_index("datetime")
    return out[["open", "high", "low", "close", "volume"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download 1-min replay CSVs via IB Gateway")
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--days", type=int, default=60, help="Calendar days of 1-min history")
    _os = __import__("os")
    _default_cid = int(
        _os.getenv("CLIENT_ID") or _os.getenv("IB_CLIENT_ID") or "1"
    )
    parser.add_argument("--port", type=int, default=int(_os.getenv("IB_PORT", "4002")))
    parser.add_argument("--client-id", type=int, default=_default_cid, dest="client_id")
    parser.add_argument("--use-rth", action="store_true", default=True)
    parser.add_argument("--extended", action="store_true", help="Include extended hours")
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "data" / "replay" / "intraday"),
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        default=True,
        help="Merge new bars into existing CSVs (default: true)",
    )
    parser.add_argument(
        "--no-merge",
        action="store_false",
        dest="merge",
        help="Replace CSV instead of merging with existing",
    )
    parser.add_argument(
        "--refresh-partial",
        action="store_true",
        help="Re-download tickers with fewer bars than 85%% of the fullest CSV",
    )
    args = parser.parse_args()
    use_rth = not args.extended

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.refresh_partial:
        counts: dict[str, int] = {}
        for p in out_dir.glob("*_1min.csv"):
            sym = p.stem.replace("_1min", "").upper()
            try:
                df = pd.read_csv(p, usecols=["datetime"])
                counts[sym] = len(df)
            except Exception:
                counts[sym] = 0
        if counts:
            max_bars = max(counts.values())
            threshold = int(max_bars * 0.85)
            partial = [t for t, n in sorted(counts.items()) if n < threshold]
            if partial:
                tickers = partial
                print(f"Refreshing {len(partial)} partial tickers (<{threshold} bars): {','.join(partial)}")
            else:
                print("All tickers have full bar counts — nothing to refresh.")
                return 0

    from core.config import BotConfig
    from core.connector import IBConnector
    from core.data import DataManager
    from core.notify import log

    cfg = BotConfig()
    cfg.IB_PORT = args.port
    cfg.IB_CLIENT_ID = args.client_id
    cfg.PAPER_TRADING = True

    connector = IBConnector(cfg)
    log.info(f"Connecting IB Gateway {cfg.IB_HOST}:{cfg.IB_PORT} client_id={cfg.IB_CLIENT_ID} …")
    if not connector.connect():
        log.error("IB Gateway connection failed — start Gateway and retry.")
        return 1

    ok, fail = 0, 0
    n_tickers = len(tickers)
    try:
        for ti, ticker in enumerate(tickers, 1):
            cfg.TICKER = ticker
            dm = DataManager(connector, cfg)
            try:
                _progress(f"📥 [{ti}/{n_tickers}] {ticker} — fetching {args.days}d of 1-min bars…")
                log.info(f"Fetching {args.days}d of 1-min bars for {ticker} …")

                def _chunk_cb(ci, total, end_str, _t=ticker, _ti=ti):
                    _progress(f"  [{_ti}/{n_tickers}] {_t} chunk {ci}/{total} → {end_str}")

                df = _fetch_chunks(
                    dm, args.days, use_rth=use_rth, ticker=ticker, on_chunk=_chunk_cb,
                )
                if df.empty or len(df) < 100:
                    log.warning(f"  {ticker}: insufficient data ({len(df)} bars)")
                    fail += 1
                    continue
                out_path = out_dir / f"{ticker}_1min.csv"
                if args.merge and out_path.is_file():
                    try:
                        old = pd.read_csv(out_path, parse_dates=["datetime"], index_col="datetime")
                        old.index = pd.to_datetime(old.index, utc=True)
                        df = pd.concat([old, df]).sort_index()
                        df = df[~df.index.duplicated(keep="last")]
                    except Exception as exc:
                        log.debug(f"  {ticker} merge skip: {exc}")
                df.to_csv(out_path, index_label="datetime")
                log.info(
                    f"  ✅ {ticker}: {len(df):,} bars "
                    f"[{df.index[0]} → {df.index[-1]}] → {out_path}"
                )
                _progress(
                    f"  ✅ [{ti}/{n_tickers}] {ticker}: {len(df):,} bars "
                    f"[{df.index[0].date()} → {df.index[-1].date()}]"
                )
                ok += 1
            except Exception as exc:
                log.warning(f"  ❌ {ticker}: {exc}")
                fail += 1
            time.sleep(1.0)
    finally:
        connector.disconnect()
        _progress("📡 IB Gateway disconnected")

    try:
        from core.replay_data_housekeeping import clean_replay_farm
        _progress("🧹 Normalizing farm (dedupe + remove duplicate daily sources)…")
        clean_replay_farm(out_dir.parent, retention_days=args.days, verbose=True)
    except Exception as exc:
        _progress(f"  ⚠️  Farm clean skipped: {exc}")

    replay_root = out_dir.parent
    _progress(f"📥 Download complete: {ok}/{n_tickers} tickers → {out_dir}")
    print(f'export REPLAY_DATA_DIR="{replay_root}"')
    print("export REPLAY_LIVE=true")
    print("export REPLAY_REALTIME_PACE=true   # 1 bar = real elapsed time")
    print("PYTHONPATH=. python main.py --mode replay-live --ticker SOFI")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
