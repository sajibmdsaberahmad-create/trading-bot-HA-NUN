#!/usr/bin/env python3
"""Self-improvement guideline text from scanner weights + session context."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Union


def generate_scalper_guidelines(
    weights: Dict[str, Any],
    scan_results: Sequence[Any],
    bot_nav: float,
    initial_cash: float,
) -> str:
    """Build HANOON self-improvement guidelines from weights and scan context."""
    win_history = weights.get("win_history", []) or []
    wins = [w for w in win_history if w.get("result") == "win"]
    losses = [w for w in win_history if w.get("result") == "loss"]
    win_rate = len(wins) / max(len(win_history), 1)
    rules: List[str] = []

    if win_rate < 0.4:
        rules.append(
            "URGENT: Win rate below 40%. Tighten stop-loss "
            "(reduce SCALP_STOP_ATR_MULTIPLIER from 0.7 to 0.5)."
        )
        rules.append(
            "Reduce trade frequency: increase SCAN_INTERVAL_SECONDS from 300 to 600."
        )
    elif win_rate > 0.7:
        rules.append(
            "Win rate excellent (>70%). Consider increasing position size or "
            "reducing SCALP_STOP_ATR_MULTIPLIER for bigger wins."
        )
    else:
        rules.append(f"Win rate {win_rate:.0%} — stable. Continue current risk parameters.")

    if losses:
        avg_loss = sum(l.get("pnl_usd", 0) for l in losses) / len(losses)
        if avg_loss > 30:
            rules.append(
                f"Average loss ${avg_loss:.0f} is high. "
                "Consider reducing MAX_TRADE_SIZE_USD from $1,000 to $500."
            )
            rules.append("Review trailing stop: tighten SCALP_TRAILING_ATR_MULTIPLIER.")

    w = weights
    if w.get("momentum", 0) > 30:
        rules.append(
            "Momentum weight is very high — strategy is overly focused on momentum. "
            "Consider rebalancing."
        )
    if w.get("volume", 0) > 30:
        rules.append(
            "Volume weight is very high — add volume_decay check to avoid chasing pumps."
        )
    if w.get("institutional", 0) > 30:
        rules.append(
            "Institutional weight is very high — ensure institutional detector is "
            "accurate (check for false signals)."
        )

    if scan_results:
        max_score = max(
            (r.get("total_score", 0) if isinstance(r, dict) else r.rank_score)
            for r in scan_results[:3]
        )
        if max_score < 20:
            rules.append(
                "Market conditions are weak (low scores). Consider wider "
                "SCALP_MIN_STOP_PCT or wait for better setups."
            )
        elif max_score > 50:
            rules.append(
                "Strong market conditions. Increase SCALP_MAX_TP_PCT from 3% to 5% "
                "to capture more upside."
            )

    if bot_nav > float(initial_cash) * 1.5:
        rules.append(
            f"Account grew {bot_nav / float(initial_cash):.0%}x. "
            "Consider adding a second concurrent position (MAX_CONCURRENT_POSITIONS)."
        )

    rules.append("Always use limit orders in fast markets (USE_LIMIT_ORDERS_IN_FAST_MARKETS = True).")
    rules.append("Monitor slippage: if fills consistently >0.4%, reduce order size.")

    pnl = bot_nav - float(initial_cash)
    pnl_pct = pnl / float(initial_cash)
    if pnl_pct < -0.1:
        rules.append("ALERT: Drawdown >10%. Pause trading for 24 hours and review strategy.")
        rules.append("Strengthen uptrend filter: require price > SMA50 instead of SMA20.")

    if not rules:
        rules.append("No guideline changes needed. System running optimally.")

    rules_text = "\n".join(f"• {r}" for r in rules)
    return f"🧭 HANOON SELF-IMPROVEMENT GUIDELINES\n{'_' * 40}\n{rules_text}\n"
