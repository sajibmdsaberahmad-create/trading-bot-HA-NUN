#!/usr/bin/env python3
"""
dashboard/app.py — Lightweight Streamlit monitoring dashboard.
Runs in a browser tab independent of the bot's execution engine.
Launch with:  streamlit run dashboard/app.py
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

# Add parent to path so we can import bot config
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.config import BotConfig

st.set_page_config(
    page_title="Trading Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
JOURNAL_PATH = BASE_DIR / "training_journal.json"
PERF_PATH = BASE_DIR / "performance.csv"
STATE_PATH = BASE_DIR / "bot_state.json"
LOG_PATH = BASE_DIR / "trading_bot.log"
MODEL_PATH = BASE_DIR / "ppo_trader.zip"
LIVE_METRICS_PATH = BASE_DIR / "live_metrics.json"


# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.title("📈 Bot Dashboard")
st.sidebar.markdown("---")

mode = st.sidebar.radio(
    "View",
    ["Backtest Results", "Training Journal", "Live Metrics", "Bot Config", "Logs"],
)

st.sidebar.markdown("---")
st.sidebar.markdown("**Quick links**")
st.sidebar.markdown("[Run warmup](command:python main.py --mode warmup)")
st.sidebar.markdown("[Run trade](command:python main.py --mode trade)")
st.sidebar.markdown("---")

# Bot status indicator
model_exists = MODEL_PATH.exists()
st.sidebar.markdown(
    f"**Model:** {'✅ Loaded' if model_exists else '❌ Not trained'}"
)


# ── Helpers ──────────────────────────────────────────────────────────────────
def load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def load_journal() -> list:
    if JOURNAL_PATH.exists():
        try:
            with open(JOURNAL_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return []
    return []


def read_log_tail(lines: int = 200) -> str:
    if LOG_PATH.exists():
        try:
            with open(LOG_PATH) as f:
                all_lines = f.readlines()
                return "".join(all_lines[-lines:])
        except Exception:
            return "Could not read log file."
    return "No log file found."


# ── Page: Backtest Results ──────────────────────────────────────────────────
if mode == "Backtest Results":
    st.title("📊 Backtest / Evaluation Results")
    st.markdown("Results from `--mode evaluate` runs.")

    journal = load_journal()
    if not journal:
        st.info("No training/evaluation data yet. Run `python main.py --mode warmup` first.")
    else:
        # Latest entry
        latest = journal[-1]
        metrics = latest.get("metrics", {})
        hp = latest.get("hyperparameters", {})

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            val = metrics.get("final_portfolio_value", "N/A")
            st.metric("Final Portfolio", f"${val:,.2f}" if isinstance(val, (int, float)) else val)
        with col2:
            val = metrics.get("ppo_return_pct", "N/A")
            st.metric("PPO Return", f"{val:+.1f}%" if isinstance(val, (int, float)) else val)
        with col3:
            val = metrics.get("bh_return_pct", "N/A")
            st.metric("Buy & Hold Return", f"{val:+.1f}%" if isinstance(val, (int, float)) else val)
        with col4:
            val = metrics.get("alpha_vs_bh_pct", "N/A")
            delta_color = "normal" if isinstance(val, (int, float)) and val >= 0 else "inverse"
            st.metric("Alpha vs B&H", f"{val:+.1f}%" if isinstance(val, (int, float)) else val,
                      delta_color=delta_color if isinstance(val, (int, float)) else "off")

        # Action breakdown
        acts = metrics.get("action_counts", {})
        if acts:
            st.subheader("Action Breakdown")
            act_df = pd.DataFrame([
                {"Action": k, "Count": v} for k, v in acts.items()
            ])
            fig = px.bar(act_df, x="Action", y="Count", color="Action",
                         title="PPO Agent Actions (HOLD / BUY / SELL)")
            st.plotly_chart(fig, use_container_width=True)

        # All sessions as a table
        if len(journal) > 1:
            st.subheader("All Training Sessions")
            rows = []
            for entry in journal:
                m = entry.get("metrics", {})
                rows.append({
                    "Date": entry.get("timestamp", "")[:19],
                    "Event": entry.get("event", ""),
                    "Portfolio": m.get("final_portfolio_value"),
                    "PPO %": m.get("ppo_return_pct"),
                    "B&H %": m.get("bh_return_pct"),
                    "Alpha %": m.get("alpha_vs_bh_pct"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

# ── Page: Training Journal ──────────────────────────────────────────────────
elif mode == "Training Journal":
    st.title("📜 Training Journal")
    st.markdown("Versioned record of all training/fine-tuning sessions.")

    journal = load_journal()
    if not journal:
        st.info("No journal entries yet.")
    else:
        for i, entry in enumerate(reversed(journal)):
            with st.expander(f"#{len(journal) - i} — {entry.get('event', '?')} @ {entry.get('timestamp', '?')[:19]}", expanded=(i == 0)):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Session Info**")
                    st.write(f"Ticker: {entry.get('ticker', 'N/A')}")
                    st.write(f"Cash: ${entry.get('initial_cash', 0):,.0f}")
                    st.write(f"Sizing: {entry.get('sizing_mode', 'N/A')}")
                    st.write(f"Model: {entry.get('versioned_model_path', 'N/A')}")
                with col2:
                    metrics = entry.get("metrics", {})
                    st.markdown("**Metrics**")
                    for k, v in metrics.items():
                        if isinstance(v, float):
                            st.write(f"{k}: {v:.2f}")
                        elif isinstance(v, dict):
                            st.write(f"{k}: {v}")
                        else:
                            st.write(f"{k}: {v}")

                hp = entry.get("hyperparameters", {})
                if hp:
                    st.markdown("**Hyperparameters**")
                    st.json(hp)

# ── Page: Live Metrics ─────────────────────────────────────────────────────
elif mode == "Live Metrics":
    st.title("🔴 Live Trading Metrics")
    st.markdown("Real-time metrics streamed from the trading engine.")
    st.caption("Auto-refreshes every 5 seconds while the bot is running.")

    live = load_json(LIVE_METRICS_PATH)

    if not live:
        st.info(
            "No live metrics available yet. "
            "Live data appears when the bot runs in `--mode trade`. "
            "The bot writes to `live_metrics.json` during execution."
        )
    else:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Account Equity", f"${live.get('account_equity', 0):,.2f}")
        with col2:
            st.metric("Open P&L", f"${live.get('open_pnl', 0):,.2f}")
        with col3:
            st.metric("Daily P&L", f"${live.get('daily_pnl', 0):,.2f}")
        with col4:
            st.metric("Position", live.get("position", "NONE"))

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Cash", f"${live.get('cash', 0):,.2f}")
        with col2:
            st.metric("Shares", live.get("shares", 0))
        with col3:
            st.metric("Current Price", f"${live.get('current_price', 0):,.2f}")
        with col4:
            st.metric("Win Rate", f"{live.get('win_rate', 0):.1f}%")

        if "trades" in live and live["trades"]:
            st.subheader("Recent Trades")
            trades_df = pd.DataFrame(live["trades"])
            st.dataframe(trades_df.tail(20), use_container_width=True)

        if "equity_curve" in live and live["equity_curve"]:
            st.subheader("Equity Curve")
            ec = live["equity_curve"]
            fig = go.Figure()
            fig.add_trace(go.Scatter(y=ec, mode="lines", name="Equity"))
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)

    # Auto-refresh
    st.rerun() if st.checkbox("Auto-refresh (5s)", value=True) else None

# ── Page: Bot Config ───────────────────────────────────────────────────────
elif mode == "Bot Config":
    st.title("⚙️ Bot Configuration")
    st.markdown("Current settings loaded from `core/config.py`.")

    cfg = BotConfig()
    config_data = {k: v for k, v in cfg.__class__.__dict__.items()
                   if not k.startswith("_") and not callable(v)}

    # Group by category
    risk_keys = [k for k in config_data if any(x in k.upper() for x in ["RISK", "STOP", "PROFIT", "LOSS", "SLIPPAGE"])]
    conn_keys = [k for k in config_data if any(x in k.upper() for x in ["IB_", "RECONNECT", "HEARTBEAT"])]
    ppo_keys = [k for k in config_data if k.startswith("PPO_")]
    other_keys = [k for k in config_data if k not in risk_keys + conn_keys + ppo_keys]

    with st.expander("🔒 Risk Management", expanded=True):
        rk = {k: config_data[k] for k in risk_keys if k in config_data}
        st.json(rk)

    with st.expander("🔌 IB Gateway Connection"):
        ck = {k: config_data[k] for k in conn_keys if k in config_data}
        st.json(ck)

    with st.expander("🧠 PPO Hyperparameters"):
        pk = {k: config_data[k] for k in ppo_keys if k in config_data}
        st.json(pk)

    with st.expander("📋 Other Settings"):
        ok = {k: config_data[k] for k in other_keys if k in config_data}
        st.json(ok)

# ── Page: Logs ─────────────────────────────────────────────────────────────
elif mode == "Logs":
    st.title("📝 Bot Logs")
    st.markdown("Tail of `trading_bot.log`")

    log_tail = read_log_tail(300)
    st.text_area("Last 300 lines", log_tail, height=600)

    if st.button("Refresh Logs"):
        st.rerun()