#!/usr/bin/env python3
"""
backtest_multi_tf.py — Multi-timeframe 1-min scalper with higher-TF trend confirmation.

Uses:
- 1-min bars for entries (tick-level precision)
- 5-min SMA20 for micro trend
- 15-min SMA50 for medium trend  
- 1-hour SMA200 for macro trend
- Daily for regime filter

Target: 160% in 3 months ($1,000 → $2,600) ≈ 53% monthly
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.risk import compute_atr, compute_momentum_score
from core.notify import log

RESULTS_DIR = Path("backtest_results")
RESULTS_DIR.mkdir(exist_ok=True)


def fetch_multi_tf(connector, cfg, ticker):
    """Fetch 1-min, 5-min, 15-min, 1-hour, and daily bars."""
    cfg.TICKER = ticker
    dm = DataManager(connector, cfg)
    
    log.info(f"Fetching multi-TF data for {ticker} …")
    
    # 1-min: 3 months for entries
    df_1min = dm.fetch_historical(duration="3 M", bar_size="1 min")
    # 5-min: for micro trend
    df_5min = dm.fetch_historical(duration="3 M", bar_size="5 mins")
    # 15-min: for medium trend
    df_15min = dm.fetch_historical(duration="3 M", bar_size="15 mins")
    # 1-hour: for macro trend
    df_1hr = dm.fetch_historical(duration="3 M", bar_size="1 hour")
    # Daily: for regime
    df_daily = dm.fetch_historical(duration="1 Y", bar_size="1 day")
    
    if df_1min is None or len(df_1min) < 500:
        log.warning(f"  Insufficient 1-min data for {ticker}")
        return None
    
    log.info(f"  Got: 1min={len(df_1min)} 5min={len(df_5min)} 15min={len(df_15min)} 1hr={len(df_1hr)} daily={len(df_daily)} bars")
    return {
        "1min": df_1min,
        "5min": df_5min,
        "15min": df_15min,
        "1hr": df_1hr,
        "daily": df_daily,
    }


def resample_sma(df_1min: pd.DataFrame, df_higher: pd.DataFrame, period: int, col: str = "close") -> pd.Series:
    """
    Create a higher-TF SMA series aligned to 1-min timestamps.
    Each 1-min bar gets the last known higher-TF SMA value.
    """
    closes = df_higher[col]
    sma = closes.rolling(period).mean()
    sma.index.name = "datetime"
    sma.name = f"sma_{period}"
    # Reindex to 1-min, forward-fill last known value
    sma_1min = sma.reindex(df_1min.index, method="ffill").fillna(method="bfill")
    return sma_1min


def multi_tf_signal(df_1min: pd.DataFrame, data: dict, i: int, atr: float):
    """
    Multi-timeframe signal generator.
    Returns (score 0..1, confidence 0..1, reason).
    """
    if i < 200 or np.isnan(atr) or atr <= 0:
        return 0.0, 0.0, "insufficient"
    
    cp = df_1min["close"].iloc[i]
    cv = df_1min["volume"].iloc[i]
    
    # Get higher-TF SMAs
    sma20_5min = resample_sma(df_1min, data["5min"], 20).iloc[i]
    sma50_15min = resample_sma(df_1min, data["15min"], 50).iloc[i]
    sma200_1hr = resample_sma(df_1min, data["1hr"], 200).iloc[i]
    sma20_daily = resample_sma(df_1min, data["daily"], 20).iloc[i]
    
    if np.isnan(sma20_5min) or np.isnan(sma50_15min) or np.isnan(sma200_1hr) or np.isnan(sma20_daily):
        return 0.0, 0.0, "nan_sma"
    
    # Score components
    score = 0.0
    reasons = []
    
    # 1. Micro trend: above 5-min SMA20
    if cp > sma20_5min:
        score += 0.20
        reasons.append("above_5min_sma20")
    
    # 2. Medium trend: above 15-min SMA50
    if cp > sma50_15min:
        score += 0.25
        reasons.append("above_15min_sma50")
    
    # 3. Macro trend: above 1-hour SMA200
    if cp > sma200_1hr:
        score += 0.25
        reasons.append("above_1hr_sma200")
    
    # 4. Daily regime: above daily SMA20 (bullish regime)
    if cp > sma20_daily:
        score += 0.15
        reasons.append("above_daily_sma20")
    
    # 5. Volume spike > 2x 20-bar avg on 1-min
    vol_avg = df_1min["volume"].iloc[max(0, i-20):i].mean()
    if vol_avg > 0 and cv > vol_avg * 2.0:
        score += 0.10
        reasons.append("vol_spike")
    
    # 6. Rising momentum: 1-min close > close 3 bars ago
    if i >= 3 and df_1min["close"].iloc[i] > df_1min["close"].iloc[i-3]:
        score += 0.05
        reasons.append("rising")
    
    # Confidence scales with score and ATR sanity
    conf = min(score * 1.2, 1.0)
    if atr / cp < 0.005 or atr / cp > 0.04:
        conf *= 0.7  # Reduce confidence in extreme volatility
    
    return score, conf, " | ".join(reasons) if reasons else "weak"


def backtest_multi_tf(ticker: str, data: dict, cfg: BotConfig, cash: float = 1000.0, 
                       score_threshold: float = 0.60) -> dict:
    """
    Multi-TF backtest. Entries on 1-min, confirmed by 5min/15min/1hr/daily trends.
    """
    df_1min = data["1min"]
    closes = df_1min["close"].values
    volumes = df_1min["volume"].values
    
    balance = float(cash)
    position = None
    results = []
    nav_curve = [balance]
    
    # Precompute 1-min ATR
    atr_vals = []
    for i in range(len(df_1min)):
        w = df_1min.iloc[max(0, i-15):i+1]
        atr_vals.append(compute_atr(w, period=min(14, len(w)-1)))
    
    # Trade throttle: max 1 per 15 min per ticker
    last_trade_bar = -100
    
    for i in range(200, len(df_1min)):
        cp = closes[i]
        cv = volumes[i]
        atr = atr_vals[i]
        
        nav = balance + (position["shares"] * cp if position else 0)
        nav_curve.append(nav)
        
        # ── EXIT ──────────────────────────────────────────────────────
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
            
            # Trailing stop once in profit
            gain = (peak - entry) / entry
            if gain > 0.002 and atr > 0:
                trail = atr * 0.5
                ns = peak - trail
                if ns > stop:
                    stop = ns
                    position["stop"] = stop
            
            if cp <= stop:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                results.append({"pnl": pnl, "bars": bars, "reason": "stop"})
                position = None
                last_trade_bar = i
                continue
            
            if cp >= tp:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                results.append({"pnl": pnl, "bars": bars, "reason": "tp"})
                position = None
                last_trade_bar = i
                continue
            
            # Trailing profit lock after 1%
            if gain > 0.01:
                floor = entry + (peak - entry) * 0.65
                if cp <= floor:
                    pnl = (cp - entry) * shares
                    balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                    results.append({"pnl": pnl, "bars": bars, "reason": "lock"})
                    position = None
                    last_trade_bar = i
                    continue
            
            # Time stop: 15 bars = 15 min
            if bars > 15 and cp < entry * 1.0005:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                results.append({"pnl": pnl, "bars": bars, "reason": "time"})
                position = None
                last_trade_bar = i
                continue
        
        # ── ENTRY ─────────────────────────────────────────────────────
        if position is None and (i - last_trade_bar) >= 3:
            score, conf, reason = multi_tf_signal(df_1min, data, i, atr)
            
            if score >= score_threshold and conf >= 0.55:
                # Hard filters
                if atr / cp < 0.003 or atr / cp > 0.04:
                    continue
                if cv < volumes[max(0, i-20):i].mean() * 2.0:
                    continue
                
                risk = cfg.risk_amount_usd(balance)
                stop_dist = max(atr * 0.5, cp * 0.002)
                stop = cp - stop_dist
                
                shares = int(min(risk / stop_dist, (balance * 0.9) / cp, 500))
                if shares < 1:
                    continue
                
                # Take profit: 2x stop distance
                tp_dist = max(stop_dist * 2.0, cp * 0.005)
                tp_dist = min(tp_dist, cp * 0.025)
                tp = cp + tp_dist
                
                cost = shares * cp * (1 + cfg.TRANSACTION_COST_PCT)
                if cost <= balance:
                    balance -= cost
                    position = {
                        "entry": cp, "shares": shares,
                        "stop": stop, "tp": tp,
                        "entry_idx": i, "peak": cp,
                        "score": score, "conf": conf, "reason": reason,
                    }
    
    # Close residual
    if position is not None:
        cp = closes[-1]
        pnl = (cp - position["entry"]) * position["shares"]
        balance += position["shares"] * cp * (1 - cfg.TRANSACTION_COST_PCT)
        results.append({"pnl": pnl, "reason": "end", "bars": len(df_1min) - position["entry_idx"]})
    
    nav_curve.append(balance)
    
    wins = sum(1 for r in results if r["pnl"] > 0)
    losses = sum(1 for r in results if r["pnl"] <= 0)
    total = len(results)
    wr = wins / total * 100 if total > 0 else 0
    ret = (balance / cash - 1) * 100
    
    peak_nav = np.maximum.accumulate(np.array(nav_curve))
    dd = (peak_nav - np.array(nav_curve)) / (peak_nav + 1e-9)
    max_dd = dd.max() * 100
    
    return {
        "ticker": ticker,
        "trades": total, "wins": wins, "losses": losses,
        "win_rate_pct": round(wr, 1),
        "total_pnl": round(sum(r["pnl"] for r in results), 2),
        "total_return_pct": round(ret, 2),
        "final_nav": round(balance, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_bars_held": round(np.mean([r["bars"] for r in results]), 1) if results else 0,
    }


def run_backtest(tickers, cash=1000.0, months=3, port=4002, client_id=5):
    cfg = BotConfig()
    cfg.INITIAL_CASH = cash
    cfg.PAPER_TRADING = True
    cfg.IB_PORT = port
    cfg.IB_CLIENT_ID = client_id
    
    connector = IBConnector(cfg)
    if not connector.connect():
        log.error("Cannot connect.")
        return
    
    all_results = []
    
    log.info("=" * 70)
    log.info(f"  MULTI-TIMEFRAME BACKTEST | {months}M | ${cash} each | Score≥0.60")
    log.info("  TFs: 1min entry | 5min/15min/1hr/daily trend filter")
    log.info("=" * 70)
    
    for ticker in tickers:
        try:
            data = fetch_multi_tf(connector, cfg, ticker)
            if data is None:
                continue
            
            res = backtest_multi_tf(ticker, data, cfg, cash=cash)
            all_results.append(res)
            
            log.info(
                f"  {ticker:6s} | {res['trades']:3d} tr | W:{res['win_rate_pct']:5.1f}% | "
                f"P&L:${res['total_pnl']:>+8.2f} ({res['total_return_pct']:>+6.1f}%) | "
                f"DD:{res['max_drawdown_pct']:5.1f}% | Avg:{res['avg_bars_held']:4.1f} bars"
            )
        except Exception as exc:
            log.warning(f"  {ticker}: {exc}")
            continue
    
    connector.disconnect()
    
    total_trades = sum(r["trades"] for r in all_results)
    total_wins = sum(r["wins"] for r in all_results)
    total_losses = sum(r["losses"] for r in all_results)
    total_pnl = sum(r["total_pnl"] for r in all_results)
    avg_wr = total_wins / (total_wins + total_losses + 1e-9) * 100
    
    log.info("=" * 70)
    log.info(f"  MULTI-TF PORTFOLIO: {len(all_results)} stocks | {total_trades} trades")
    log.info(f"  Win/Loss: {total_wins}W/{total_losses}L ({avg_wr:.1f}%)")
    log.info(f"  Combined P&L: ${total_pnl:+.2f}")
    log.info("=" * 70)
    
    ranked = sorted(all_results, key=lambda r: r["win_rate_pct"], reverse=True)
    log.info("  TOP BY WIN RATE:")
    for r in ranked[:10]:
        log.info(
            f"  {r['ticker']:6s} | {r['trades']:3d} tr | "
            f"W:{r['win_rate_pct']:5.1f}% | ${r['total_pnl']:>+8.2f} ({r['total_return_pct']:>+6.1f}%)"
        )
    
    # Persist
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    meta = {
        "run_at": datetime.utcnow().isoformat(),
        "tickers": tickers,
        "cash": cash,
        "months": months,
        "avg_wr": round(avg_wr, 1),
        "total_pnl": round(total_pnl, 2),
    }
    json_path = RESULTS_DIR / f"results_multi_tf_{ts}.json"
    csv_path = RESULTS_DIR / "results_multi_tf_latest.csv"
    
    with open(json_path, "w") as f:
        json.dump({"meta": meta, "results": all_results}, f, indent=2)
    pd.DataFrame(all_results).to_csv(csv_path, index=False)
    
    log.info(f"Saved: {json_path}")
    log.info(f"Saved: {csv_path}")
    
    # Git
    try:
        subprocess.run(["git", "add", str(json_path), str(csv_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"backtest multi-TF: {ts} | wr {avg_wr:.0f}% | P&L ${total_pnl:+.0f}"],
            check=True, capture_output=True
        )
        subprocess.run(["git", "push"], check=True, capture_output=True)
        log.info("Git: committed and pushed.")
    except subprocess.CalledProcessError as exc:
        log.warning(f"Git failed: {exc}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", default="SOFI,MARA,PLTR,RKLB,ASTS,COIN,IONQ,ACHR,NIO,XPEV,LCID,QS,GME,FCEL,RIOT")
    p.add_argument("--cash", default=1000.0, type=float)
    p.add_argument("--months", default=3, type=int)
    p.add_argument("--port", default=4002, type=int)
    p.add_argument("--client-id", default=11, type=int)
    args = p.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    run_backtest(tickers, cash=args.cash, months=args.months, port=args.port, client_id=args.client_id)