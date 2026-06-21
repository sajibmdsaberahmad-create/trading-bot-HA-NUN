#!/usr/bin/env python3
"""
backtest_scalper_v3.py — Direct 5-min bar scalper backtest.

No scanner filters — just trades on volume spikes + momentum.
This is the simplest possible scalper and shows us whether the
concept works. We add scanner filters later.

Usage:  python backtest_scalper_v3.py --tickers SOFI,MARA,PLTR,RKLB,ASTS,COIN,IONQ --cash 1000
"""

import argparse
import sys
import numpy as np
import pandas as pd
from typing import List, Optional

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.risk import compute_atr
from core.notify import log


def run_backtest(tickers: List[str], cash: float = 1000.0, 
                 port: int = 4002, client_id: int = 5):
    
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
    log.info("  SCALPER BACKTEST v3 — Direct 5-min Momentum Scalper")
    log.info(f"  Universe: {len(tickers)} | Cash: ${cash:,.0f}")
    log.info(f"  Max risk/trade: ${cfg.risk_amount_usd(cash):.2f}")
    log.info("=" * 70)
    
    for ticker in tickers:
        try:
            cfg.TICKER = ticker
            dm = DataManager(connector, cfg)
            
            log.info(f"  ── {ticker} ──")
            df = dm.fetch_historical(duration="1 M", bar_size="5 mins")
            
            if df is None or len(df) < 100:
                log.warning(f"  Insufficient data")
                continue
            
            result = backtest_one(ticker, df, cfg, cash)
            all_results.append(result)
            
            if result["trades"] > 0:
                log.info(f"    Trades: {result['trades']} | Win: {result['win_rate_pct']}% | "
                         f"P&L: ${result['total_pnl']:+.2f} ({result['total_return_pct']:+.1f}%) | "
                         f"DD: {result['max_drawdown_pct']:.1f}% | "
                         f"Avg hold: {result.get('avg_bars_held', 0):.0f} bars")
            else:
                log.info(f"    No trades")
                
        except Exception as exc:
            log.warning(f"  Error: {exc}")
            continue
    
    connector.disconnect()
    
    if not all_results:
        return
    
    total_trades = sum(r["trades"] for r in all_results)
    total_wins = sum(r["wins"] for r in all_results)
    total_losses = sum(r["losses"] for r in all_results)
    total_pnl = sum(r["total_pnl"] for r in all_results)
    
    log.info("=" * 70)
    log.info("  COMBINED SUMMARY")
    log.info(f"  Stocks: {len(all_results)} | Trades: {total_trades}")
    wr = total_wins / (total_wins + total_losses + 1e-9) * 100
    log.info(f"  Win/Loss: {total_wins}W/{total_losses}L ({wr:.1f}%)")
    log.info(f"  Combined P&L: ${total_pnl:+.2f}")
    log.info("=" * 70)
    
    sorted_r = sorted(all_results, key=lambda r: r.get("total_return_pct", 0), reverse=True)
    log.info("\n  RANKINGS:")
    for r in sorted_r[:10]:
        if r["trades"] > 0:
            log.info(f"  {r['ticker']:6s} | {r['trades']:3d} tr | "
                     f"W:{r['win_rate_pct']:5.1f}% | "
                     f"${r['total_pnl']:>+7.2f} ({r['total_return_pct']:>+5.1f}%) | "
                     f"DD:{r['max_drawdown_pct']:4.1f}% | "
                     f"Avg:{r.get('avg_bars_held',0):.0f} bars")


def backtest_one(ticker: str, df: pd.DataFrame, cfg: BotConfig, cash: float) -> dict:
    """Backtest a simple 5-min volume spike + momentum scalper."""
    
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values
    
    balance = float(cash)
    position = None
    nav_curve = [balance]
    total_pnl = 0.0
    results = []
    
    # Precompute ATR
    atrs = []
    for i in range(len(df)):
        w = df.iloc[max(0, i-15):i+1]
        atrs.append(compute_atr(w, period=min(14, max(2, len(w)-1))))
    
    for i in range(60, len(df)):
        cp = closes[i]
        cv = volumes[i]
        atr = atrs[i]
        
        nav = balance + (position["shares"] * cp if position else 0)
        nav_curve.append(nav)
        
        # === EXIT ===
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
            
            # Trailing stop from +0.3%
            gain = (peak - entry) / entry
            if gain > 0.003 and atr > 0:
                trail = atr * 0.6
                ns = peak - trail
                if ns > stop:
                    stop = ns
                    position["stop"] = stop
            
            if cp <= stop:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                total_pnl += pnl
                results.append({"pnl": pnl, "reason": "stop", "bars": bars})
                position = None
                continue
            
            if cp >= tp:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                total_pnl += pnl
                results.append({"pnl": pnl, "reason": "tp", "bars": bars})
                position = None
                continue
            
            # Trailing profit taker
            if gain > 0.01:
                floor = entry + (peak - entry) * 0.7  # keep 70%, give back 30%
                if cp <= floor:
                    pnl = (cp - entry) * shares
                    balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                    total_pnl += pnl
                    results.append({"pnl": pnl, "reason": "trailing_profit", "bars": bars})
                    position = None
                    continue
        
        # === ENTRY ===
        if position is None and atr > 0:
            # Volume spike: >2x avg of last 40 bars
            vol_avg = np.mean(volumes[max(0, i-40):i])
            vol_spike = vol_avg > 0 and cv > vol_avg * 1.8
            
            # Momentum: close > VWAP of last 3 bars
            vwap_3 = np.average(closes[max(0, i-3):i+1], 
                               weights=volumes[max(0, i-3):i+1]) if i >= 3 else cp
            above_vwap = cp > vwap_3
            
            # Price rising over last 3 bars
            rising = cp > closes[max(0, i-3)] if i >= 3 else True
            
            # Don't enter above $200
            if cp > 200:
                continue
            
            if vol_spike and above_vwap and rising:
                risk = cfg.risk_amount_usd(nav)
                stop_dist = max(atr * 0.5, cp * 0.002)  # 0.2-0.5% stop
                stop = cp - stop_dist
                
                shares = int(min(risk / stop_dist, (balance * 0.9) / cp, 500))
                
                if shares >= 1:
                    tp_dist = max(atr * 1.0, stop_dist * 1.5, cp * 0.004)
                    tp_dist = min(tp_dist, cp * 0.025)  # cap at 2.5%
                    tp = cp + tp_dist
                    
                    cost = shares * cp * (1 + cfg.TRANSACTION_COST_PCT)
                    if cost <= balance:
                        balance -= cost
                        position = {
                            "entry": cp, "shares": shares,
                            "stop": stop, "tp": tp,
                            "entry_idx": i, "peak": cp,
                        }
    
    # Close at end
    if position is not None:
        cp = closes[-1]
        pnl = (cp - position["entry"]) * position["shares"]
        balance += position["shares"] * cp * (1 - cfg.TRANSACTION_COST_PCT)
        total_pnl += pnl
        results.append({"pnl": pnl, "reason": "end", "bars": len(df) - position["entry_idx"]})
    
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
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(ret, 2),
        "final_nav": round(balance, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_bars_held": round(avg_bars, 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="SOFI,MARA,PLTR,RKLB,ASTS,COIN,IONQ,ACHR,NIO,XPEV,LCID,QS,GME,NKLA,FCEL")
    parser.add_argument("--cash", default=1000.0, type=float)
    parser.add_argument("--port", default=4002, type=int)
    args = parser.parse_args()
    
    tickers = [t.strip().upper() for t in args.tickers.split(",")]
    run_backtest(tickers, cash=args.cash, port=args.port)