#!/usr/bin/env python3
"""Verify tick-by-tick market data configuration and optional live IB probe."""

from __future__ import annotations

import argparse
import inspect
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def check_config() -> list[str]:
    from core.config import BotConfig
    from core.data import tick_by_tick_type
    from core.fast_execution import assign_stream_modes, tick_stream_count

    errors: list[str] = []
    cfg = BotConfig()

    if not cfg.USE_TICK_STREAM:
        errors.append("USE_TICK_STREAM is false — bot will not call reqTickByTickData")
    if cfg.PAPER_TRADING and cfg.PAPER_REALTIME_BARS_ONLY:
        errors.append("PAPER_REALTIME_BARS_ONLY=true forces 5s bars only on paper")

    tbt = tick_by_tick_type(cfg)
    if tbt != "AllLast":
        errors.append(f"TICK_BY_TICK_TYPE={tbt!r} (expected AllLast)")

    wanted = ["SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB"]
    modes = assign_stream_modes(wanted, cfg)
    tick_names = [t for t, m in modes.items() if m == "tick"]
    if len(tick_names) < min(4, tick_stream_count(cfg)):
        errors.append(
            f"assign_stream_modes allocated only {len(tick_names)} tick slots: {modes}"
        )

    print("── Config ──")
    print(f"  USE_TICK_STREAM          = {cfg.USE_TICK_STREAM}")
    print(f"  TICK_BY_TICK_TYPE        = {tbt}")
    print(f"  PAPER_TRADING            = {cfg.PAPER_TRADING}")
    print(f"  PAPER_REALTIME_BARS_ONLY = {cfg.PAPER_REALTIME_BARS_ONLY}")
    print(f"  AI_TICK_STREAM_COUNT     = {tick_stream_count(cfg)}")
    print(f"  Stream modes (sample)    = {modes}")
    return errors


def check_data_manager_source() -> list[str]:
    from core import data as data_mod

    errors: list[str] = []
    src = inspect.getsource(data_mod.DataManager.start_tick_stream)
    if '"AllLast"' not in src and "tick_by_tick_type" not in src:
        errors.append("start_tick_stream does not use AllLast / tick_by_tick_type()")
    if "reqHistoricalData" in src:
        errors.append("start_tick_stream must not use reqHistoricalData for live ticks")
    if "reqTickByTickData" not in src:
        errors.append("start_tick_stream missing reqTickByTickData")

    print("── DataManager.start_tick_stream ──")
    print("  reqTickByTickData present:", "reqTickByTickData" in src)
    print("  tick_by_tick_type() used:", "tick_by_tick_type" in src)
    print("  reqHistoricalData absent:", "reqHistoricalData" not in src)
    return errors


def live_probe(symbol: str, host: str, port: int, client_id: int, wait_sec: float) -> list[str]:
    """Connect to IB Gateway and wait for AllLast ticks on one symbol."""
    errors: list[str] = []
    try:
        import ib_insync as ibi
    except ImportError:
        return ["ib_insync not installed"]

    from core.config import BotConfig
    from core.data import DataManager, tick_by_tick_type
    from core.connector import IBConnector

    cfg = BotConfig(
        TICKER=symbol,
        IB_HOST=host,
        IB_PORT=port,
        IB_CLIENT_ID=client_id,
    )
    conn = IBConnector(cfg)
    print(f"── Live probe {symbol} @ {host}:{port} (client {client_id}) ──")
    dm = None
    try:
        if not conn.connect():
            return ["IB connect returned false"]
    except Exception as exc:
        return [f"IB connect failed: {exc}"]

    tbt = tick_by_tick_type(cfg)
    ticks_before = 0
    try:
        dm = DataManager(conn, cfg)
        dm.start_tick_stream()
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            conn.ib.sleep(0.2)
            handle = dm._tick_handle
            if handle is not None and getattr(handle, "tickByTicks", None):
                ticks_before = len(handle.tickByTicks)
                if ticks_before > 0:
                    last = handle.tickByTicks[-1]
                    print(
                        f"  ✓ Received {ticks_before} {tbt} tick(s) — "
                        f"last ${last.price} x {last.size}"
                    )
                    break
        if ticks_before == 0:
            if dm._realtime_handle is not None:
                errors.append(
                    f"No {tbt} ticks in {wait_sec}s — fell back to 5s bars "
                    "(check subscription / live session conflict)"
                )
            else:
                errors.append(f"No ticks and no 5s fallback in {wait_sec}s")
    finally:
        if dm is not None:
            try:
                dm.stop_tick_stream()
            except Exception:
                pass
        try:
            conn.disconnect()
        except Exception:
            pass

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify HANOON tick-by-tick setup")
    parser.add_argument("--live", action="store_true", help="Probe IB Gateway for live ticks")
    parser.add_argument("--symbol", default="SOFI")
    parser.add_argument("--host", default=os.getenv("IB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IB_PORT", "4002")))
    parser.add_argument("--client-id", type=int, default=int(os.getenv("CLIENT_ID", "99")))
    parser.add_argument("--wait", type=float, default=8.0, help="Seconds to wait for ticks")
    args = parser.parse_args()

    print("HANOON tick-by-tick verification\n")
    all_errors: list[str] = []
    all_errors.extend(check_config())
    all_errors.extend(check_data_manager_source())

    if args.live:
        all_errors.extend(
            live_probe(args.symbol, args.host, args.port, args.client_id, args.wait)
        )
    else:
        print("\n  (skip live probe — pass --live to test IB Gateway)")

    print()
    if all_errors:
        print("FAIL:")
        for err in all_errors:
            print(f"  ✗ {err}")
        return 1

    print("PASS — tick-by-tick configuration looks correct.")
    if not args.live:
        print("  Run: python scripts/verify_tick_stream.py --live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
