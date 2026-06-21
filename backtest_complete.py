#!/usr/bin/env python3
"""
backtest_complete.py — Self-contained backtest with persistent results & git sync.

Features:
- Fetches data once from IB (or uses cached CSV)
- Saves per-ticker + portfolio results to JSON & CSV
- Auto-commits results to git
- Optimised for 80% win rate + 20%+ return target
- Daily trend filter + quality gates to boost win rate
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.risk import compute_atr
from core.notify import log

# ──────────────────────────────────────────────────────────────────────────────
# Result persistence
# ──────────────────────────────────────────────────────────────────────────────

RESULTS_DIR = Path("backtest_results")
RESULTS_DIR.mkdir(exist_ok=True)

def save_results(results: list, meta: dict):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    json_path = RESULTS_DIR / f"results_{ts}.json"
    csv_path  = RESULTS_DIR / "results_latest.csv"

    payload = {
        "timestamp": ts,
        "meta": meta,
        "results": results,
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    df = pd.DataFrame(results)
    df.to_csv(csv_path, index=False)

    log.info(f"Results saved → {json_path}  +  {csv_path}")
    return json_path, csv_path


def git_commit_results(paths: list, message: str):
    try:
        for p in paths:
            subprocess.run(["git", "add", str(p)], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        log.info(f"Git: committed & pushed results ({message})")
    except subprocess.CalledProcessError as exc:
        log.warning(f"Git commit failed: {exc.stderr.decode().strip()}")


# ──────────────────────────────────────────────────────────────────────────────
# Optimised scalper — daily-trend filter + strict momentum gates
# ──────────────────────────────────────────────────────────────────────────────

def run_backtest_optimised(ticker: str, df_5min: pd.DataFrame, cfg: BotConfig, cash: float) -> dict:
    closes = df_5min["close"].values
    highs  = df_5min["high"].values
    lows   = df_5min["low"].values
    volumes= df_5min["volume"].values

    balance = float(cash)
    position = None
    results = []
    nav_curve = [balance]

    # Precompute ATR(14) on 5-min bars
    atr_vals = []
    for i in range(len(df_5min)):
        w = df_5min.iloc[max(0, i-15):i+1]
        atr_vals.append(compute_atr(w, period=min(14, len(w)-1)))

    # Build 20-period SMA filter (trend gate) on 5min
    sma20 = pd.Series(closes).rolling(20).mean().values

    for i in range(60, len(df_5min)):
        cp = closes[i]
        cv = volumes[i]
        atr = atr_vals[i]
        sma = sma20[i]

        if np.isnan(sma):
            continue

        nav = balance + (position["shares"] * cp if position else 0)
        nav_curve.append(nav)

        # ── EXIT ──────────────────────────────────────────────────────────
        if position is not None:
            entry = position["entry"]
            shares = position["shares"]
            stop = position["stop"]
            tp = position["tp"]
            peak = position["peak"]
            bars = i - position["entry_idx"]

            if cp > peak:
                peak = cp
                position["peak"] = peak

            # Tighten stop aggressively once in profit (+0.3%)
            gain = (peak - entry) / entry
            if gain > 0.003 and atr > 0:
                trail = atr * 0.5          # tight trail
                ns = peak - trail
                if ns > stop:
                    stop = ns
                    position["stop"] = stop

            # Hard stop
            if cp <= stop:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                results.append({"pnl": pnl, "reason": "stop", "bars": bars})
                position = None
                continue

            # Hard TP (generous 1.5%+ target)
            if cp >= tp:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                results.append({"pnl": pnl, "reason": "tp", "bars": bars})
                position = None
                continue

            # Trailing profit: lock 70% of gains after 1% move
            if gain > 0.01:
                floor = entry + (peak - entry) * 0.70
                if cp <= floor:
                    pnl = (cp - entry) * shares
                    balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                    results.append({"pnl": pnl, "reason": "trail_profit", "bars": bars})
                    position = None
                    continue

            # Time stop: force exit after 8 bars (40 min) no progress
            if bars > 8 and cp < entry * 1.0005:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                results.append({"pnl": pnl, "reason": "time_stop", "bars": bars})
                position = None
                continue

        # ── ENTRY ──────────────────────────────────────────────────────────
        if position is None and atr > 0 and not np.isnan(sma):
            # FILTER 1: above 20-SMA (trend filter)
            if cp < sma:
                continue

            # FILTER 2: volume spike (>2.0x 20-bar avg)
            vol_avg = np.mean(volumes[max(0, i-20):i]) if i >= 20 else np.mean(volumes[:i])
            if vol_avg <= 0 or cv < vol_avg * 2.0:
                continue

            # FILTER 3: VWAP of last 5 bars (price above short-term value)
            vwap_5 = np.average(closes[max(0, i-5):i+1],
                                weights=volumes[max(0, i-5):i+1]) if i >= 5 else cp
            if cp < vwap_5:
                continue

            # FILTER 4: price rising over last 3 bars
            if i >= 3 and closes[i] <= closes[i-3]:
                continue

            # FILTER 5: ATR not too wide (< 4% of price) — avoid chaotic stocks
            if atr / cp > 0.04:
                continue

            # FILTER 6: price cap removed for momentum; floor $0.50
            if cp < 0.50:
                continue

            risk = cfg.risk_amount_usd(balance + (shares * cp if position else 0))
            stop_dist = max(atr * 0.6, cp * 0.003)         # 0.3% minimum
            stop = cp - stop_dist

            shares = int(min(risk / stop_dist, (balance * 0.9) / cp, 500))
            if shares < 1:
                continue

            # Take profit: 1.2x stop distance (min 0.6% up to 3%)
            tp_dist = max(stop_dist * 1.5, cp * 0.006)
            tp_dist = min(tp_dist, cp * 0.03)
            tp = cp + tp_dist

            cost = shares * cp * (1 + cfg.TRANSACTION_COST_PCT)
            if cost <= balance:
                balance -= cost
                position = {
                    "entry": cp, "shares": shares,
                    "stop": stop, "tp": tp,
                    "entry_idx": i, "peak": cp,
                }

    # Close residual
    if position is not None:
        cp = closes[-1]
        pnl = (cp - position["entry"]) * position["shares"]
        balance += position["shares"] * cp * (1 - cfg.TRANSACTION_COST_PCT)
        results.append({"pnl": pnl, "reason": "end", "bars": len(df_5min) - position["entry_idx"]})

    nav_curve.append(balance)

    wins = sum(1 for r in results if r["pnl"] > 0)
    losses = sum(1 for r in results if r["pnl"] <= 0)
    total = len(results)
    wr = wins / total * 100 if total > 0 else 0
    ret = (balance / cash - 1) * 100

    nav_arr = np.array(nav_curve)
    peak_nav = np.maximum.accumulate(nav_arr)
    dd = (peak_nav - nav_arr) / (peak_nav + 1e-9)
    max_dd = dd.max() * 100

    avg_win = np.mean([r["pnl"] for r in results if r["pnl"] > 0]) if wins > 0 else 0
    avg_loss = np.mean([r["pnl"] for r in results if r["pnl"] <= 0]) if losses > 0 else 0
    avg_bars = np.mean([r["bars"] for r in results]) if results else 0

    return {
        "ticker": ticker,
        "trades": total, "wins": wins, "losses": losses,
        "win_rate_pct": round(wr, 1),
        "total_pnl": round(sum(r["pnl"] for r in results), 2),
        "total_return_pct": round(ret, 2),
        "final_nav": round(balance, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_bars_held": round(avg_bars, 1),
    }


# ──────────────────────────────────────────────────────────────────────────────
# IB fetch wrapper
# ──────────────────────────────────────────────────────────────────────────────

def fetch_data(connector, cfg, ticker):
    cfg.TICKER = ticker
    dm = DataManager(connector, cfg)
    log.info(f"Fetching 5-min bars for {ticker} …")
    df = dm.fetch_historical(duration="3 M", bar_size="5 mins")   # 3 months for significance
    if df is None or len(df) < 200:
        log.warning(f"  Insufficient data for {ticker}")
        return None
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Main runner
# ──────────────────────────────────────────────────────────────────────────────

def run_backtest(tickers: list, cash: float = 10000.0, port: int = 4002, client_id: int = 5):
    cfg = BotConfig()
    cfg.INITIAL_CASH = cash
    cfg.PAPER_TRADING = True
    cfg.IB_PORT = port
    cfg.IB_CLIENT_ID = client_id

    connector = IBConnector(cfg)
    if not connector.connect():
        log.error("Cannot connect to IB Gateway.")
        return

    all_results = []

    log.info("=" * 70)
    log.info("  COMPLETE BACKTEST — Optimised 5-min Scalper v4")
    log.info(f"  Universe: {len(tickers)} | Start cash: ${cash:,.0f} each")
    log.info(f"  Filters: SMA trend | Vol>2x | Above VWAP | Rising | Tight trail")
    log.info("=" * 70)

    for ticker in tickers:
        try:
            df = fetch_data(connector, cfg, ticker)
            if df is None:
                continue
            res = run_backtest_optimised(ticker, df, cfg, cash)
            all_results.append(res)

            if res["trades"] > 0:
                log.info(
                    f"  {ticker:6s} | {res['trades']:3d} tr | "
                    f"Win:{res['win_rate_pct']:5.1f}% | "
                    f"P&L:${res['total_pnl']:>+8.2f} ({res['total_return_pct']:>+6.1f}%) | "
                    f"DD:{res['max_drawdown_pct']:5.1f}% | "
                    f"Avg:{res['avg_bars_held']:4.1f} bars"
                )
            else:
                log.info(f"  {ticker:6s} | No trades")
        except Exception as exc:
            log.warning(f"  {ticker}: {exc}")
            continue

    connector.disconnect()

    if not all_results:
        log.warning("No results to save.")
        return

    # Portfolio totals
    total_trades = sum(r["trades"] for r in all_results)
    total_wins   = sum(r["wins"]   for r in all_results)
    total_losses = sum(r["losses"] for r in all_results)
    total_pnl    = sum(r["total_pnl"] for r in all_results)

    avg_wr = total_wins / (total_wins + total_losses + 1e-9) * 100

    log.info("=" * 70)
    log.info("  PORTFOLIO SUMMARY")
    log.info(f"  Stocks: {len(all_results)} | Trades: {total_trades}")
    log.info(f"  Win/Loss: {total_wins}W/{total_losses}L ({avg_wr:.1f}%)")
    log.info(f"  Combined P&L: ${total_pnl:+.2f}  (weighted return avg {total_pnl/(cash*len(all_results))*100:.1f}%)")
    log.info("=" * 70)

    ranked = sorted(all_results, key=lambda r: r.get("win_rate_pct", 0), reverse=True)
    log.info("\n  TOP BY WIN RATE:")
    for r in ranked[:10]:
        if r["trades"] > 0:
            log.info(
                f"  {r['ticker']:6s} | {r['trades']:3d} tr | "
                f"W:{r['win_rate_pct']:5.1f}% | "
                f"${r['total_pnl']:>+8.2f} ({r['total_return_pct']:>+6.1f}%)"
            )

    # Persist results
    meta = {
        "run_at": datetime.utcnow().isoformat(),
        "tickers_requested": tickers,
        "tickers_success": [r["ticker"] for r in all_results],
        "cash": cash,
        "avg_win_rate": round(avg_wr, 1),
        "combined_pnl": round(total_pnl, 2),
    }
    paths = save_results(all_results, meta)

    git_commit_results(paths, f"backtest: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC | win% {avg_wr:.0f} | P&L ${total_pnl:+.0f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", default="SOFI,MARA,PLTR,RKLB,ASTS,COIN,IONQ,ACHR,NIO,XPEV,LCID,QS,GME,NKLA,FCEL")
    p.add_argument("--cash",   default=10000.0, type=float)
    p.add_argument("--port",   default=4002, type=int)
    args = p.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    run_backtest(tickers, cash=args.cash, port=args.port)