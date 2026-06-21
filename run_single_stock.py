#!/usr/bin/env python3
"""
run_single_stock.py — Run backtest on ONE stock at a time to avoid IB Gateway rate limits.

Usage:
  python run_single_stock.py --stock SOFI
  python run_single_stock.py --stock MARA --months 3
  python run_single_stock.py --list              # Show available stocks

This script:
1. Tests one stock at a time (no parallel requests to IB)
2. Waits between stocks to avoid rate limits
3. Saves individual results
4. After all stocks, auto-runs AI training on all results
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.risk import compute_atr
from core.notify import log

RESULTS_DIR = Path("backtest_results")
RESULTS_DIR.mkdir(exist_ok=True)

# Penny stock universe (from core/scanner.py)
STOCK_UNIVERSE = [
    "SOFI", "PLTR", "MARA", "RIOT", "COIN", "RKLB", "ASTS",
    "QS", "LCID", "RIVN", "CHPT", "FCEL", "PLUG",
    "DNA", "CRSP", "EDIT", "NTLA", "BEAM",
    "DWAC", "ATER", "BBIG",
    "UUUU", "CCJ",
    "OCGN", "MRNA", "BNTX", "NVAX", "AXSM",
    "GME", "BB", "CEI",
    "NKLA", "GOEV", "WKHS", "BLNK",
    "AG", "HL", "PAAS",
    "TQQQ", "SQQQ", "SOXL", "FNGU", "LABU", "JNUG",
    "MSTY", "NVDY", "CONY", "AMDY",
    "ACHR", "JOBY", "PDYN",
    "IONQ", "QMCO", "RGTI",
    "HIVE", "CLSK", "WULF",
    "VKTX", "CERO", "MNMD",
    "MAXN", "ARRY", "NOVA",
    "VALE", "X", "CLF",
    "NIO", "XPEV", "LI",
    "HSAI", "BABA", "JD",
]


def run_backtest_single(ticker: str, cash: float = 1000.0, months: int = 6, port: int = 4002, client_id: int = 5):
    """Run backtest on a single stock using backtest_1min_ai.py logic."""
    cfg = BotConfig()
    cfg.INITIAL_CASH = cash
    cfg.PAPER_TRADING = True
    cfg.IB_PORT = port
    cfg.IB_CLIENT_ID = client_id
    
    connector = IBConnector(cfg)
    if not connector.connect():
        log.error(f"Cannot connect to IB Gateway for {ticker}")
        return None
    
    try:
        # Fetch data
        cfg.TICKER = ticker
        dm = DataManager(connector, cfg)
        log.info(f"Fetching {months}M of 1-min bars for {ticker} …")
        df = dm.fetch_historical(duration=f"{months} M", bar_size="1 min")
        if df is None or len(df) < 500:
            log.warning(f"  Insufficient 1-min data for {ticker}")
            return None
        log.info(f"  Got {len(df):,} 1-min bars")
        
        # Run backtest (inline to avoid subprocess overhead)
        from backtest_1min_ai import backtest_1min_ai
        result = backtest_1min_ai(ticker, df, cfg, cash=cash, ai_threshold=0.65)
        
        log.info(
            f"  {ticker:6s} | {result['trades']:3d} tr | W:{result['win_rate_pct']:5.1f}% | "
            f"P&L:${result['total_pnl']:>+8.2f} ({result['total_return_pct']:>+6.1f}%) | "
            f"DD:{result['max_drawdown_pct']:5.1f}% | Conf:{result['avg_confidence']:.2f}"
        )
        
        # Save individual result
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        result_path = RESULTS_DIR / f"single_{ticker}_{ts}.json"
        with open(result_path, "w") as f:
            json.dump({"meta": {"ticker": ticker, "run_at": datetime.utcnow().isoformat(), "months": months, "cash": cash}, "result": result}, f, indent=2)
        
        return result
        
    finally:
        connector.disconnect()


def run_batch(stocks: list, cash: float = 1000.0, months: int = 6, delay: float = 2.0):
    """Run backtest on multiple stocks, one at a time, with delays."""
    log.info("=" * 70)
    log.info(f"  SINGLE-STOCK BACKTEST BATCH")
    log.info(f"  Stocks: {len(stocks)} | Cash: ${cash:,} | History: {months}M")
    log.info(f"  Delay between stocks: {delay}s (prevents IB rate limits)")
    log.info("=" * 70)
    
    all_results = []
    for i, ticker in enumerate(stocks, 1):
        log.info(f"\n[{i}/{len(stocks)}] Testing {ticker}...")
        
        result = run_backtest_single(ticker, cash=cash, months=months)
        if result:
            all_results.append(result)
        
        # Wait between stocks to avoid rate limits (except after last)
        if i < len(stocks):
            log.info(f"  Waiting {delay}s before next stock...")
            time.sleep(delay)
    
    # Summary
    if all_results:
        total_trades = sum(r["trades"] for r in all_results)
        total_wins = sum(r["wins"] for r in all_results)
        total_losses = sum(r["losses"] for r in all_results)
        total_pnl = sum(r["total_pnl"] for r in all_results)
        avg_wr = total_wins / (total_wins + total_losses + 1e-9) * 100
        
        log.info("\n" + "=" * 70)
        log.info("  BATCH RESULTS")
        log.info(f"  Tested: {len(all_results)}/{len(stocks)} stocks | {total_trades} trades")
        log.info(f"  Win/Loss: {total_wins}W/{total_losses}L ({avg_wr:.1f}%)")
        log.info(f"  Combined P&L: ${total_pnl:+.2f}")
        log.info("=" * 70)
        
        # Save combined results
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        json_path = RESULTS_DIR / f"batch_{ts}.json"
        csv_path = RESULTS_DIR / "results_1min_latest.csv"
        
        with open(json_path, "w") as f:
            json.dump({"meta": {"run_at": datetime.utcnow().isoformat(), "stocks": stocks, "avg_wr": round(avg_wr, 1), "total_pnl": round(total_pnl, 2)}, "results": all_results}, f, indent=2)
        
        df_results = pd.DataFrame(all_results)
        df_results.to_csv(csv_path, index=False)
        
        log.info(f"Saved: {json_path}")
        log.info(f"Saved: {csv_path}")
        
        # Auto-run AI training
        log.info("\n" + "=" * 70)
        log.info("  RUNNING AI TRAINING ON ALL RESULTS...")
        log.info("=" * 70)
        try:
            subprocess.run([sys.executable, "train_from_backtest.py", "--backtest", str(csv_path)], check=True)
            log.info("✅ AI training complete")
        except subprocess.CalledProcessError as e:
            log.warning(f"AI training failed: {e}")
        
        # Git commit
        try:
            subprocess.run(["git", "add", str(json_path), str(csv_path)], check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"backtest batch: {len(all_results)} stocks | wr {avg_wr:.0f}% | P&L ${total_pnl:+.0f}"],
                check=True, capture_output=True
            )
            subprocess.run(["git", "push"], check=True, capture_output=True)
            log.info("✅ Git: committed and pushed")
        except subprocess.CalledProcessError as e:
            log.warning(f"Git failed: {e}")
        
        return all_results
    
    return None


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stock", default=None, help="Single stock ticker (e.g., SOFI)")
    p.add_argument("--stocks", default=None, help="Comma-separated list of stocks")
    p.add_argument("--all", action="store_true", help="Test ALL stocks in universe (one at a time)")
    p.add_argument("--list", action="store_true", help="Show available stocks")
    p.add_argument("--cash", default=1000.0, type=float)
    p.add_argument("--months", default=6, type=int)
    p.add_argument("--port", default=4002, type=int)
    p.add_argument("--client-id", default=5, type=int)
    p.add_argument("--delay", default=2.0, type=float, help="Delay between stocks (seconds)")
    args = p.parse_args()
    
    if args.list:
        print("\n📊 Available stocks for testing:")
        for i, s in enumerate(STOCK_UNIVERSE, 1):
            print(f"  {s:6s}", end="")
            if i % 6 == 0:
                print()
        print()
        sys.exit(0)
    
    if args.stock:
        # Single stock mode
        ticker = args.stock.strip().upper()
        log.info(f"Single-stock mode: {ticker} (no rate limit issues)")
        result = run_backtest_single(ticker, cash=args.cash, months=args.months, port=args.port, client_id=args.client_id)
        if result:
            # Auto-train on single result
            log.info("\nRunning AI training on single stock result...")
            csv_path = RESULTS_DIR / "results_1min_latest.csv"
            pd.DataFrame([result]).to_csv(csv_path, index=False)
            subprocess.run([sys.executable, "train_from_backtest.py", "--backtest", str(csv_path)])
    elif args.stocks:
        # Custom list
        stocks = [s.strip().upper() for s in args.stocks.split(",") if s.strip()]
        run_batch(stocks, cash=args.cash, months=args.months, delay=args.delay)
    elif args.all:
        # All stocks
        run_batch(STOCK_UNIVERSE, cash=args.cash, months=args.months, delay=args.delay)
    else:
        log.error("Specify --stock, --stocks, or --all")
        sys.exit(1)