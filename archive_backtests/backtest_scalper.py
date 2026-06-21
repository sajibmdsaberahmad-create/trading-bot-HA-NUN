#!/usr/bin/env python3
"""
backtest_scalper.py — Aggressive backtest of the institutional scalper strategy
on penny stocks (NASDAQ/NYSE only, no OTC/Pink Sheets).

Runs historical data through the scalper logic:
- Scans for volume spikes + momentum
- Enters with tight stops (0.3-1.0%)
- Exits via trailing stop + trailing profit
- Measures win rate, avg gain/loss, Sharpe, max drawdown

Usage:  python backtest_scalper.py --tickers SOFI,MARA,PLTR --cash 1000
"""

import argparse
import sys
import time
from typing import List, Optional, Tuple
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.institutional import InstitutionalDetector
from core.scanner import StockScanner, ScanResult, PENNY_STOCK_UNIVERSE
from core.risk import TradePlan, compute_atr, compute_momentum_score
from core.notify import log


def backtest_single_ticker(
    ticker: str,
    df: pd.DataFrame,
    cfg: BotConfig,
    initial_cash: float = 1000.0,
    verbose: bool = True,
) -> dict:
    """
    Backtest the scalper strategy on a single ticker's daily data.
    Uses 1-day bars for simulation (scalps at daily close).
    Returns performance metrics dict.
    """
    if len(df) < 30:
        return {"ticker": ticker, "error": "insufficient_data", "trades": 0}

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values
    
    cash = float(initial_cash)
    shares = 0.0
    nav = float(initial_cash)
    
    trades_taken = 0
    wins = 0
    losses = 0
    total_pnl = 0.0
    trade_pnls = []
    trade_returns = []
    
    nav_curve = [initial_cash]
    entry_price = 0.0
    entry_bar = 0
    plan = None
    
    scanner = StockScanner(cfg)
    detector = InstitutionalDetector()
    atr_values = []
    prev_signal = 0  # 0=none, 1=buy, -1=sell
    
    for i in range(20, len(df)):
        current_price = float(closes[i])
        current_vol = float(volumes[i])
        
        # Update NAV if in position
        if shares > 0:
            nav = cash + shares * current_price
        else:
            nav = cash
        nav_curve.append(nav)
        
        # Run scanner evaluation on a rolling window
        window_df = df.iloc[max(0, i-30):i+1].copy()
        scan_result = scanner.evaluate_stock(ticker, window_df)
        
        # Feed institutional detector
        detector.feed_bar(current_vol, current_price)
        
        # Compute ATR for stop/target
        atr = compute_atr(window_df, period=5)
        atr_values.append(atr)
        
        # === EXIT LOGIC ===
        if shares > 0 and plan is not None:
            # Hard stop check
            if current_price <= plan.initial_stop_price:
                pnl = (current_price - entry_price) * shares
                cash += shares * current_price * (1.0 - cfg.TRANSACTION_COST_PCT)
                total_pnl += pnl
                trade_pnls.append(pnl)
                trade_returns.append((current_price - entry_price) / entry_price)
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                if verbose:
                    log.info(f"  [{ticker}] HARD STOP @ ${current_price:.2f} | P&L: ${pnl:+.2f}")
                shares = 0.0
                plan = None
                continue
            
            # Take profit check
            if current_price >= plan.take_profit_price:
                pnl = (current_price - entry_price) * shares
                cash += shares * current_price * (1.0 - cfg.TRANSACTION_COST_PCT)
                total_pnl += pnl
                trade_pnls.append(pnl)
                trade_returns.append((current_price - entry_price) / entry_price)
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                if verbose:
                    log.info(f"  [{ticker}] TAKE PROFIT @ ${current_price:.2f} | P&L: ${pnl:+.2f}")
                shares = 0.0
                plan = None
                continue
        
        # === ENTRY LOGIC (only when flat) ===
        if shares == 0:
            if scan_result and scan_result.rank_score > 20:
                # Check if it passed the scanner AND has momentum
                momentum = compute_momentum_score(window_df, lookback=5)
                if momentum > 0.02:  # minimum positive momentum
                    # Calculate entry
                    risk_usd = cfg.risk_amount_usd(nav)
                    stop_dist = atr * cfg.SCALP_STOP_ATR_MULTIPLIER
                    min_dist = current_price * cfg.SCALP_MIN_STOP_PCT
                    max_dist = current_price * cfg.SCALP_MAX_STOP_PCT
                    stop_dist = float(np.clip(stop_dist, min_dist, max_dist))
                    stop_price = current_price - stop_dist
                    
                    # Sizing
                    max_shares = (cash * 0.9) / current_price
                    risk_shares = risk_usd / stop_dist
                    qty = min(risk_shares, max_shares, cfg.MAX_SHARES_PER_TRADE)
                    qty = float(np.floor(qty))
                    
                    if qty >= 1:
                        # TP
                        tp_dist = atr * cfg.SCALP_TP_ATR_MULTIPLIER
                        tp_dist *= (1.0 + 0.3 * max(0.0, momentum))
                        min_tp = stop_dist * cfg.SCALP_MIN_RR
                        tp_dist = max(tp_dist, min_tp)
                        tp_dist = min(tp_dist, current_price * cfg.SCALP_MAX_TP_PCT)
                        tp_price = current_price + tp_dist
                        
                        entry_price = current_price
                        shares = qty
                        cash -= qty * current_price * (1.0 + cfg.TRANSACTION_COST_PCT)
                        entry_bar = i
                        trades_taken += 1
                        
                        plan = TradePlan(
                            side="LONG",
                            entry_price=current_price,
                            shares=qty,
                            initial_stop_price=round(stop_price, 4),
                            take_profit_price=round(tp_price, 4),
                            risk_usd=round(qty * stop_dist, 2),
                            atr_at_entry=atr,
                        )
                        
                        if verbose:
                            gain_pct = (tp_price - current_price) / current_price * 100
                            stop_pct = (current_price - stop_price) / current_price * 100
                            log.info(f"  [{ticker}] ENTRY {qty:.0f}x @ ${current_price:.2f} | "
                                     f"Stop -{stop_pct:.2f}% | Target +{gain_pct:.2f}% | "
                                     f"Score: {scan_result.rank_score:.0f}")
    
    # Close any open position at end of data
    if shares > 0:
        final_price = float(closes[-1])
        pnl = (final_price - entry_price) * shares
        cash += shares * final_price * (1.0 - cfg.TRANSACTION_COST_PCT)
        total_pnl += pnl
        trade_pnls.append(pnl)
        trade_returns.append((final_price - entry_price) / entry_price)
        if pnl >= 0:
            wins += 1
        else:
            losses += 1
        if verbose:
            log.info(f"  [{ticker}] CLOSE @ ${final_price:.2f} | P&L: ${pnl:+.2f}")
        shares = 0.0
        nav = cash
    
    final_nav = cash
    
    # Metrics
    total_return = (final_nav / initial_cash - 1.0) * 100
    win_rate = wins / (wins + losses + 1e-9) * 100
    avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
    avg_loss = np.mean([p for p in trade_pnls if p < 0]) if losses > 0 else 0
    expectancy = (win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss) if avg_loss else 0
    
    nav_arr = np.array(nav_curve)
    peak = np.maximum.accumulate(nav_arr)
    dd = (peak - nav_arr) / (peak + 1e-9)
    max_dd = dd.max() * 100
    
    sharpe = 0.0
    if len(trade_returns) >= 2:
        ret_arr = np.array(trade_returns)
        if ret_arr.std() > 0:
            sharpe = (ret_arr.mean() / ret_arr.std()) * np.sqrt(252)
    
    return {
        "ticker": ticker,
        "trades": trades_taken,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return, 2),
        "final_nav": round(final_nav, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "profit_factor": round(abs(sum(p for p in trade_pnls if p > 0) / (sum(p for p in trade_pnls if p < 0) + 1e-9)), 2),
    }


def run_backtest(tickers: List[str], cash: float = 1000.0, verbose: bool = False, port: int = 4002, client_id: int = 5):
    """Run backtest across multiple tickers."""
    cfg = BotConfig()
    cfg.INITIAL_CASH = cash
    cfg.PAPER_TRADING = True
    cfg.IB_PORT = port
    cfg.IB_CLIENT_ID = client_id
    
    connector = IBConnector(cfg)
    if not connector.connect():
        log.error("Cannot connect to IB Gateway for historical data.")
        return
    
    data_mgr = DataManager(connector, cfg)
    
    all_results = []
    total_trades = 0
    total_wins = 0
    total_losses = 0
    combined_pnl = 0.0
    combined_nav = cash
    
    log.info("=" * 70)
    log.info("  AGGRESSIVE PENNY STOCK SCALPER BACKTEST")
    log.info(f"  Universe: {len(tickers)} stocks | Cash: ${cash:,.0f}")
    log.info(f"  Max risk/trade: ${cfg.risk_amount_usd(cash):.2f}")
    log.info("=" * 70)
    
    for ticker in tickers:
        try:
            log.info(f"  ── {ticker} ──")
            cfg.TICKER = ticker
            data_mgr = DataManager(connector, cfg)
            hist = data_mgr.fetch_historical(duration="1 Y", bar_size="1 day")
            if hist is None or len(hist) < 30:
                log.warning(f"  Skipping {ticker}: insufficient data ({len(hist) if hist is not None else 0} rows)")
                continue
            
            result = backtest_single_ticker(ticker, hist, cfg, cash, verbose=verbose)
            all_results.append(result)
            
            total_trades += result.get("trades", 0)
            total_wins += result.get("wins", 0)
            total_losses += result.get("losses", 0)
            combined_pnl += result.get("total_pnl", 0)
            
            if result.get("trades", 0) > 0:
                log.info(f"    Trades: {result['trades']} | Win: {result['win_rate_pct']}% | "
                         f"P&L: ${result['total_pnl']:+.2f} ({result['total_return_pct']:+.1f}%) | "
                         f"DD: {result['max_drawdown_pct']:.1f}%")
            
        except Exception as exc:
            log.warning(f"  Error backtesting {ticker}: {exc}")
            continue
    
    connector.disconnect()
    
    # ── Summary ────────────────────────────────────────────────────────
    if not all_results:
        log.warning("No valid backtest results.")
        return all_results
    
    log.info("=" * 70)
    log.info("  BACKTEST SUMMARY")
    log.info(f"  Stocks tested: {len(all_results)}")
    log.info(f"  Total trades:  {total_trades}")
    log.info(f"  Win/Loss:      {total_wins}W / {total_losses}L")
    win_rate = total_wins / (total_wins + total_losses + 1e-9) * 100
    log.info(f"  Win rate:      {win_rate:.1f}%")
    log.info(f"  Combined P&L:  ${combined_pnl:+.2f}")
    log.info("=" * 70)
    
    # Top performers
    sorted_results = sorted(all_results, key=lambda r: r.get("total_return_pct", 0), reverse=True)
    log.info("\n  TOP PERFORMERS:")
    for r in sorted_results[:5]:
        if r.get("trades", 0) > 0:
            log.info(f"  {r['ticker']:6s} | {r['trades']:3d} trades | "
                     f"Win: {r['win_rate_pct']:5.1f}% | "
                     f"P&L: ${r['total_pnl']:>+7.2f} ({r['total_return_pct']:>+6.1f}%) | "
                     f"DD: {r['max_drawdown_pct']:5.1f}%")
    
    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Penny Stock Scalper Backtest")
    parser.add_argument("--tickers", default="SOFI,MARA,PLTR,COIN,RKLB,ASTS,QS,LCID,IONQ",
                        help="Comma-separated tickers to backtest")
    parser.add_argument("--cash", default=1000.0, type=float, help="Initial cash")
    parser.add_argument("--port", default=7497, type=int, help="IB Gateway port")
    parser.add_argument("--client-id", default=5, type=int, help="IB API client ID")
    parser.add_argument("--verbose", action="store_true", help="Show detailed per-trade logs")
    args = parser.parse_args()
    
    ticker_list = [t.strip().upper() for t in args.tickers.split(",")]
    run_backtest(ticker_list, cash=args.cash, verbose=args.verbose, port=args.port, client_id=args.client_id)
