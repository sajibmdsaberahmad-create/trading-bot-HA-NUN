#!/usr/bin/env python3
"""
core/performance.py — Live trading statistics: win rate, Sharpe ratio,
max drawdown, trade log CSV.
"""

from datetime import datetime
from typing import Dict, List, Optional

import numpy as np


class PerformanceTracker:
    """
    Tracks every trade and the equity curve. Writes a row per trade to
    a CSV (importable into Excel/Sheets) and produces a one-line summary
    string used in status logs, Telegram daily summaries, etc.
    """

    def __init__(self, initial_cash: float, perf_path: str = "performance.csv"):
        self.initial_cash = initial_cash
        self.perf_path = perf_path

        self.trades: List[Dict] = []
        self.nav_series: List[float] = [initial_cash]
        self._open_nav: float = initial_cash
        self._open_price: float = 0.0
        self._open_shares: float = 0.0
        self.session_pnl: float = 0.0
        self.win_rate: float = 0.0

        with open(self.perf_path, "w", encoding="utf-8") as fh:
            fh.write("timestamp,action,price,shares,cost_proceeds,nav,return_pct,exit_reason\n")

    def record_trade(self, timestamp: str, action: str, price: float, shares: float,
                      amount: float, nav: float, exit_reason: str = ""):
        ret = (nav / self.initial_cash - 1.0) * 100.0
        with open(self.perf_path, "a", encoding="utf-8") as fh:
            fh.write(f"{timestamp},{action},{price:.4f},{shares:.2f},{amount:.2f},{nav:.2f},{ret:.3f},{exit_reason}\n")

        if action == "BUY":
            self._open_nav = nav
            self._open_price = price
            self._open_shares = shares
        elif action == "SELL":
            pnl_usd = (price - self._open_price) * self._open_shares
            trade_ret = (nav - self._open_nav) / (self._open_nav + 1e-9)
            self.trades.append({
                "entry_nav": self._open_nav, "exit_nav": nav, "return": trade_ret,
                "pnl_usd": pnl_usd, "won": nav > self._open_nav, "exit_reason": exit_reason,
            })

        self.nav_series.append(nav)

    def summary(self, current_nav: float) -> str:
        self.session_pnl = current_nav - self.initial_cash
        if not self.trades:
            ret = (current_nav / self.initial_cash - 1.0) * 100.0
            self.win_rate = 0.0
            return f"No completed trades yet | Running return: {ret:+.2f}%"

        wins = sum(1 for t in self.trades if t["won"])
        self.win_rate = wins / len(self.trades)
        win_rate = self.win_rate * 100.0
        returns = [t["return"] for t in self.trades]
        avg_ret = np.mean(returns) * 100.0

        nav_arr = np.array(self.nav_series)
        peak = np.maximum.accumulate(nav_arr)
        dd = (peak - nav_arr) / (peak + 1e-9)
        max_dd = dd.max() * 100.0

        if len(returns) >= 2 and np.std(returns) > 0:
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252)
        else:
            sharpe = 0.0

        total_ret = (current_nav / self.initial_cash - 1.0) * 100.0

        return (f"Trades: {len(self.trades):3d} | Win: {win_rate:.1f}% | "
                f"Avg: {avg_ret:+.2f}% | Sharpe: {sharpe:.2f} | "
                f"MaxDD: {max_dd:.1f}% | Total: {total_ret:+.2f}%")

    def daily_summary_text(self, current_nav: float, account_equity: float) -> str:
        today_trades = self.trades  # for a single-session run this is all of them
        wins = sum(1 for t in today_trades if t["won"])
        losses = len(today_trades) - wins
        pnl_today = current_nav - self.initial_cash
        return (
            f"Account equity: ${account_equity:,.2f}\n"
            f"NAV: ${current_nav:,.2f} ({pnl_today/self.initial_cash*100:+.2f}%)\n"
            f"Trades: {len(today_trades)} (W:{wins} L:{losses})\n"
            f"{self.summary(current_nav)}"
        )
