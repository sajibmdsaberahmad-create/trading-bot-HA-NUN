#!/usr/bin/env python3
"""
backtest_scalper_v2.py — Improved penny stock scalper backtest.

Uses 5-min bars instead of daily bars for realistic scalping simulation.
Scans a larger universe and properly accounts for:
- $1-$50 price range (scalping works on more than just sub-$20)
- 1-min/5-min bar entry/exit logic
- Trailing stop + trailing profit taker
- Hard stops for connection loss protection

Usage:  python backtest_scalper_v2.py --tickers SOFI,MARA,PLTR --cash 1000
        python backtest_scalper_v2.py --all         # Use full universe
"""

import argparse
import sys
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.risk import compute_atr, compute_momentum_score, TradePlan
from core.scanner import StockScanner
from core.notify import log


def backtest_5min(ticker: str, df_5min: pd.DataFrame, df_daily: pd.DataFrame,
                  cfg: BotConfig, cash: float = 1000.0) -> dict:
    """
    Backtest on 5-min bars with trailing exits.
    Uses daily bars for the scanner/screening only.
    """
    if len(df_5min) < 100:
        return {"ticker": ticker, "error": "insufficient_5min_data", "trades": 0}
    
    closes_5 = df_5min["close"].values
    highs_5 = df_5min["high"].values
    lows_5 = df_5min["low"].values
    volumes_5 = df_5min["volume"].values
    
    positions = []
    position = None  # {entry_price, shares, stop_price, tp_price, entry_idx, peak}
    balance = float(cash)
    nav_curve = [balance]
    total_pnl = 0.0
    trade_results = []
    
    # Pre-compute rolling ATR(14) on 5-min bars
    atr_series = []
    for i in range(len(df_5min)):
        window = df_5min.iloc[max(0, i-15):i+1]
        atr_series.append(compute_atr(window, period=min(14, len(window)-1)))
    
    # For 5-min scalping, use relaxed price limits
    class ScalpScanner(StockScanner):
        pass
    
    scanner = ScalpScanner(cfg)
    scanner.MIN_PRICE = 0.50  # Accept stocks from $0.50 for scalping
    scanner.MAX_PRICE = 200.0  # Accept up to $200 for scalping (most penny stocks are higher now)
    scanner.MIN_REL_VOLUME = 1.2  # More lenient volume filter
    
    last_scan_result = None
    
    for i in range(60, len(df_5min)):
        cp = float(closes_5[i])
        cv = float(volumes_5[i])
        atr = atr_series[i]
        
        # Update NAV
        nav = balance + (position["shares"] * cp if position else 0)
        nav_curve.append(nav)
        
        # Rescan daily every 78 bars (~1 trading day)
        if i % 78 == 0 and df_daily is not None:
            window = df_daily.iloc[-min(30, len(df_daily)):]
            result = scanner.evaluate_stock(ticker, window)
            last_scan_result = result
        
        # === EXIT CHECKS ===
        if position is not None:
            entry_px = position["entry_price"]
            shares = position["shares"]
            stop = position["stop_price"]
            tp = position["tp_price"]
            peak = position["peak"]
            bar_count = i - position["entry_idx"]
            
            # Update peak
            if cp > peak:
                peak = cp
                position["peak"] = peak
            
            # Trailing stop (activate after +0.5% profit)
            gain_pct = (peak - entry_px) / entry_px
            if gain_pct > 0.005 and atr > 0:
                trail_dist = atr * 0.8  # tight trailing
                new_stop = peak - trail_dist
                if new_stop > stop:
                    stop = new_stop
                    position["stop_price"] = stop
            
            # Check stop
            if cp <= stop:
                pnl = (cp - entry_px) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                total_pnl += pnl
                trade_results.append({"pnl": pnl, "exit_reason": "stop", "bars_held": bar_count,
                                      "entry": entry_px, "exit": cp})
                position = None
                continue
            
            # Check take profit (hard)
            if cp >= tp:
                pnl = (cp - entry_px) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                total_pnl += pnl
                trade_results.append({"pnl": pnl, "exit_reason": "tp", "bars_held": bar_count,
                                      "entry": entry_px, "exit": cp})
                position = None
                continue
            
            # Trailing profit taker (lock in gains)
            if gain_pct > 0.01:  # 1% gain
                giveback = 0.3  # allow 30% giveback
                floor = entry_px + (peak - entry_px) * (1 - giveback)
                if cp <= floor:
                    pnl = (cp - entry_px) * shares
                    balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                    total_pnl += pnl
                    trade_results.append({"pnl": pnl, "exit_reason": "trailing_profit",
                                          "bars_held": bar_count, "entry": entry_px, "exit": cp})
                    position = None
                    continue
            
            # Time stop: exit after 24 bars (~2 hours) if no progress
            if bar_count > 24 and cp < entry_px * 0.995:
                pnl = (cp - entry_px) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                total_pnl += pnl
                trade_results.append({"pnl": pnl, "exit_reason": "time_stop", "bars_held": bar_count,
                                      "entry": entry_px, "exit": cp})
                position = None
                continue
        
        # === ENTRY LOGIC ===
        if position is None and last_scan_result and last_scan_result.rank_score > 15:
            # Volume spike: current bar vol > 2x avg
            vol_avg = np.mean(volumes_5[max(0, i-39):i]) if i >= 40 else np.mean(volumes_5[:i])
            vol_spike = cv > vol_avg * 1.5
            
            # Momentum: positive price change over last 3 bars
            mom_3 = (closes_5[i] / closes_5[max(0, i-3)] - 1) if i >= 3 else 0.0
            
            if vol_spike and mom_3 > 0.002 and atr > 0:
                risk_usd = cfg.risk_amount_usd(nav)
                stop_dist = max(atr * 0.6, cp * 0.003)
                stop_price = cp - stop_dist
                
                shares = int(min(risk_usd / stop_dist, (balance * 0.9) / cp, 2000))
                
                if shares >= 1:
                    tp_dist = max(atr * 1.2, stop_dist * 1.5, cp * 0.005)
                    tp_dist = min(tp_dist, cp * 0.03)
                    tp_price = cp + tp_dist
                    
                    cost = shares * cp * (1 + cfg.TRANSACTION_COST_PCT)
                    if cost <= balance:
                        balance -= cost
                        position = {
                            "entry_price": cp, "shares": shares,
                            "stop_price": stop_price, "tp_price": tp_price,
                            "entry_idx": i, "peak": cp,
                            "vol_spike": vol_spike, "mom_3": mom_3,
                        }
    
    # Close any open position at end
    if position is not None:
        cp = float(closes_5[-1])
        pnl = (cp - position["entry_price"]) * position["shares"]
        balance += position["shares"] * cp * (1 - cfg.TRANSACTION_COST_PCT)
        total_pnl += pnl
        trade_results.append({"pnl": pnl, "exit_reason": "end_of_data",
                              "bars_held": len(df_5min) - position["entry_idx"],
                              "entry": position["entry_price"], "exit": cp})
    
    nav_curve.append(balance)
    final_nav = balance
    
    # Metrics
    wins = sum(1 for t in trade_results if t["pnl"] > 0)
    losses = sum(1 for t in trade_results if t["pnl"] <= 0)
    total_trades = len(trade_results)
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0
    total_return = (final_nav / cash - 1) * 100
    avg_win = np.mean([t["pnl"] for t in trade_results if t["pnl"] > 0]) if wins > 0 else 0
    avg_loss = np.mean([t["pnl"] for t in trade_results if t["pnl"] <= 0]) if losses > 0 else 0
    
    nav_arr = np.array(nav_curve)
    peak = np.maximum.accumulate(nav_arr)
    dd = (peak - nav_arr) / (peak + 1e-9)
    max_dd = dd.max() * 100
    
    returns = [t["pnl"] / cash for t in trade_results]
    sharpe = 0.0
    if len(returns) >= 2 and np.std(returns) > 0:
        sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252 * 78)
    
    pf = abs(sum(t["pnl"] for t in trade_results if t["pnl"] > 0) /
             (sum(t["pnl"] for t in trade_results if t["pnl"] <= 0) + 1e-9))
    
    return {
        "ticker": ticker,
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return, 2),
        "final_nav": round(final_nav, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(pf, 2),
        "avg_bars_held": round(np.mean([t["bars_held"] for t in trade_results]), 1) if trade_results else 0,
    }


def run_backtest(tickers: List[str], cash: float = 1000.0, port: int = 4002, client_id: int = 5):
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
    log.info("  SCALPER BACKTEST v2 — 5-min bars")
    log.info(f"  Universe: {len(tickers)} | Cash: ${cash:,.0f}")
    log.info(f"  Risk/trade: ${cfg.risk_amount_usd(cash):.2f}")
    log.info("=" * 70)
    
    for ticker in tickers:
        try:
            cfg.TICKER = ticker
            dm = DataManager(connector, cfg)
            
            log.info(f"  ── {ticker} ──")
            df_5min = dm.fetch_historical(duration="1 M", bar_size="5 mins")
            df_daily = dm.fetch_historical(duration="1 Y", bar_size="1 day")
            
            if df_5min is None or len(df_5min) < 100:
                log.warning(f"  Insufficient 5-min data for {ticker}")
                continue
            
            result = backtest_5min(ticker, df_5min, df_daily, cfg, cash)
            all_results.append(result)
            
            if result.get("trades", 0) > 0:
                log.info(f"    Trades: {result['trades']} | Win: {result['win_rate_pct']}% | "
                         f"P&L: ${result['total_pnl']:+.2f} ({result['total_return_pct']:+.1f}%) | "
                         f"DD: {result['max_drawdown_pct']:.1f}% | "
                         f"Avg bars: {result.get('avg_bars_held', 0)}")
            else:
                log.info(f"    No trades triggered")
            
        except Exception as exc:
            log.warning(f"  Error on {ticker}: {exc}")
            continue
    
    connector.disconnect()
    
    # Summary
    if not all_results:
        log.warning("No valid results.")
        return
    
    total_trades = sum(r["trades"] for r in all_results)
    total_wins = sum(r["wins"] for r in all_results)
    total_losses = sum(r["losses"] for r in all_results)
    total_pnl = sum(r["total_pnl"] for r in all_results)
    
    log.info("=" * 70)
    log.info("  SUMMARY")
    log.info(f"  Stocks: {len(all_results)} | Trades: {total_trades}")
    win_rate = total_wins / (total_wins + total_losses + 1e-9) * 100
    log.info(f"  Win/Loss: {total_wins}W/{total_losses}L ({win_rate:.1f}%)")
    log.info(f"  Combined P&L: ${total_pnl:+.2f}")
    log.info("=" * 70)
    
    sorted_r = sorted(all_results, key=lambda r: r.get("total_return_pct", -999), reverse=True)
    log.info("\n  TOP PERFORMERS:")
    for r in sorted_r[:7]:
        if r["trades"] > 0:
            log.info(f"  {r['ticker']:6s} | {r['trades']:3d} tr | "
                     f"Win: {r['win_rate_pct']:5.1f}% | "
                     f"P&L: ${r['total_pnl']:>+7.2f} ({r['total_return_pct']:>+6.1f}%) | "
                     f"DD: {r['max_drawdown_pct']:4.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="SOFI,MARA,PLTR,RKLB,ASTS,COIN,IONQ,ACHR,NIO",
                        help="Comma-separated tickers")
    parser.add_argument("--all", action="store_true", help="Test full universe")
    parser.add_argument("--cash", default=1000.0, type=float)
    parser.add_argument("--port", default=4002, type=int)
    args = parser.parse_args()
    
    if args.all:
        from core.scanner import PENNY_STOCK_UNIVERSE
        tickers = PENNY_STOCK_UNIVERSE
    else:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    
    run_backtest(tickers, cash=args.cash, port=args.port)