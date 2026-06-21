#!/usr/bin/env python3
"""
backtest_multi_tf_safe.py — Multi-timeframe scalper with ONE IB fetch per ticker.

Fetches only 1-min bars, then derives higher-TF SMAs via resampling.
IB calls: 1 per ticker instead of 5 per ticker. No rate limits.
Includes timeout protection for IB data fetches.
"""

import argparse
import json
import signal
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

FETCH_TIMEOUT_SECONDS = 90


class _FetchTimeout(Exception):
    pass

def _timeout_handler(signum, frame):
    raise _FetchTimeout("IB fetch timed out")

def fetch_with_timeout(dm, duration, bar_size, timeout=FETCH_TIMEOUT_SECONDS):
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        df = dm.fetch_historical(duration=duration, bar_size=bar_size)
        signal.alarm(0)
        return df, None
    except _FetchTimeout as exc:
        return None, str(exc)
    except Exception as exc:
        return None, str(exc)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def resample_sma(series_1min: pd.Series, higher_tf_rule: str, period: int) -> pd.Series:
    """
    Build a higher-TF SMA from 1-min close series, then align back to 1-min timestamps.
    """
    sma_higher = series_1min.resample(higher_tf_rule).last().dropna().rolling(period).mean()
    sma_aligned = sma_higher.reindex(series_1min.index, method="ffill").fillna(method="bfill")
    return sma_aligned


def multi_tf_signal(closes_1min: pd.Series, volumes_1min: pd.Series, i: int, atr: float,
                     indicators: dict) -> tuple[float, float, str]:
    if i < 200 or np.isnan(atr) or atr <= 0:
        return 0.0, 0.0, "insufficient"
    
    cp = closes_1min.iloc[i]
    cv = volumes_1min.iloc[i]
    
    sma20_5min = indicators["sma20_5min"].iloc[i]
    sma50_15min = indicators["sma50_15min"].iloc[i]
    sma200_1hr = indicators["sma200_1hr"].iloc[i]
    sma20_daily = indicators["sma20_daily"].iloc[i]
    sma50_daily = indicators["sma50_daily"].iloc[i]
    
    if any(np.isnan(x) for x in [sma20_5min, sma50_15min, sma200_1hr, sma20_daily, sma50_daily]):
        return 0.0, 0.0, "nan_sma"
    
    score = 0.0
    reasons = []
    
    # Multi-TF trend alignment (1-min entry must align with all higher TFs)
    if cp > sma20_5min:
        score += 0.15
        reasons.append("5min_bull")
    if cp > sma50_15min:
        score += 0.20
        reasons.append("15min_bull")
    if cp > sma200_1hr:
        score += 0.20
        reasons.append("1hr_bull")
    if cp > sma20_daily:
        score += 0.15
        reasons.append("daily_bull")
    if cp > sma50_daily:
        score += 0.10
        reasons.append("daily_trend_up")
    
    # Volume spike on 1-min
    vol_avg = volumes_1min.iloc[max(0, i-20):i].mean()
    if vol_avg > 0 and cv > vol_avg * 2.0:
        score += 0.10
        reasons.append("vol_spike")
    
    # Rising momentum on 1-min
    if i >= 3 and closes_1min.iloc[i] > closes_1min.iloc[i-3]:
        score += 0.10
        reasons.append("rising")
    
    conf = min(score * 1.2, 1.0)
    if atr <= 0 or atr / cp < 0.003 or atr / cp > 0.04:
        conf *= 0.7
    
    return score, conf, " | ".join(reasons) if reasons else "weak"


def backtest_multi_tf(ticker, data, cfg, cash=1000.0, score_threshold=0.65):
    df_1min = data["1min"]
    closes = df_1min["close"]
    volumes = df_1min["volume"]
    
    # Precompute higher-TF SMAs from 1-min data
    indicators = {
        "sma20_5min": resample_sma(closes, "5min", 20),
        "sma50_15min": resample_sma(closes, "15min", 50),
        "sma200_1hr": resample_sma(closes, "1h", 200),
        "sma20_daily": resample_sma(closes, "1D", 20),
        "sma50_daily": resample_sma(closes, "1D", 50),
    }
    
    balance = float(cash)
    position = None
    results = []
    nav_curve = [balance]
    
    atr_vals = []
    for i in range(len(df_1min)):
        w = df_1min.iloc[max(0, i-15):i+1]
        atr_vals.append(compute_atr(w, period=min(14, len(w)-1)))
    
    last_trade_bar = -100
    
    for i in range(200, len(df_1min)):
        cp = closes.iloc[i]
        cv = volumes.iloc[i]
        atr = atr_vals[i]
        
        nav = balance + (position["shares"] * cp if position else 0)
        nav_curve.append(nav)
        
        # EXIT
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
            
            if gain > 0.01:
                floor = entry + (peak - entry) * 0.65
                if cp <= floor:
                    pnl = (cp - entry) * shares
                    balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                    results.append({"pnl": pnl, "bars": bars, "reason": "lock"})
                    position = None
                    last_trade_bar = i
                    continue
            
            if bars > 15 and cp < entry * 1.0005:
                pnl = (cp - entry) * shares
                balance += shares * cp * (1 - cfg.TRANSACTION_COST_PCT)
                results.append({"pnl": pnl, "bars": bars, "reason": "time"})
                position = None
                last_trade_bar = i
                continue
        
        # ENTRY
        if position is None and (i - last_trade_bar) >= 3:
            score, conf, reason = multi_tf_signal(closes, volumes, i, atr, indicators)
            
            if score >= score_threshold and conf >= 0.55:
                if atr / cp < 0.003 or atr / cp > 0.04:
                    continue
                if cv < volumes.iloc[max(0, i-20):i].mean() * 2.0:
                    continue
                
                risk = cfg.risk_amount_usd(balance)
                stop_dist = max(atr * 0.5, cp * 0.002)
                stop = cp - stop_dist
                
                shares = int(min(risk / stop_dist, (balance * 0.9) / cp, 500))
                if shares < 1:
                    continue
                
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
    
    if position is not None:
        cp = closes.iloc[-1]
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


def run_backtest(tickers, cash=1000.0, months=3, port=4002, client_id=11):
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
    log.info(f"  MULTI-TF BACKTEST (SAFE) | {months}M | ${cash} each | IB calls: 1/ticker | timeout {FETCH_TIMEOUT_SECONDS}s")
    log.info("=" * 70)
    
    for ticker in tickers:
        try:
            cfg.TICKER = ticker
            dm = DataManager(connector, cfg)
            log.info(f"Fetching 1-min bars for {ticker} …")
            df_1min, err = fetch_with_timeout(dm, f"{months} M", "1 min")
            if err or df_1min is None or len(df_1min) < 500:
                log.warning(f"  {ticker}: skipped ({err})")
                continue
            
            log.info(f"  Got {len(df_1min)} bars [{df_1min.index[0]} -> {df_1min.index[-1]}]")
            
            data = {"1min": df_1min}
            res = backtest_multi_tf(ticker, data, cfg, cash=cash)
            all_results.append(res)
            
            log.info(
                f"  {ticker:6s} | {res['trades']:3d} tr | W:{res['win_rate_pct']:5.1f}% | "
                f"P&L:${res['total_pnl']:>+8.2f} ({res['total_return_pct']:>+6.1f}%) | "
                f"DD:{res['max_drawdown_pct']:5.1f}% | Avg:{res['avg_bars_held']:4.1f} bars"
            )
            time.sleep(0.5)
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
    
    try:
        subprocess.run(["git", "add", str(json_path), str(csv_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"backtest multi-TF safe: {ts} | wr {avg_wr:.0f}% | P&L ${total_pnl:+.0f}"],
            check=True, capture_output=True
        )
        subprocess.run(["git", "push"], check=True, capture_output=True)
        log.info("Git: committed and pushed.")
    except subprocess.CalledProcessError as exc:
        log.warning(f"Git failed: {exc}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", default="SOFI,MARA,PLTR,RKLB,ASTS,COIN,IONQ")
    p.add_argument("--cash", default=1000.0, type=float)
    p.add_argument("--months", default=3, type=int)
    p.add_argument("--port", default=4002, type=int)
    p.add_argument("--client-id", default=11, type=int)
    args = p.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    run_backtest(tickers, cash=args.cash, months=args.months, port=args.port, client_id=args.client_id)