#!/usr/bin/env python3
"""
debug_data.py — Quick data sanity check to verify IB returns unique data per ticker.
"""

import sys
from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager

cfg = BotConfig()
cfg.IB_PORT = 4002
cfg.IB_CLIENT_ID = 55
connector = IBConnector(cfg)
if not connector.connect():
    sys.exit("Cannot connect to IB Gateway on port 4002.")

for ticker in ["SOFI", "MARA", "PLTR", "RKLB", "ASTS", "COIN", "IONQ"]:
    cfg.TICKER = ticker
    dm = DataManager(connector, cfg)
    df = dm.fetch_historical(duration="3 M", bar_size="5 mins")
    if df is not None:
        print(f"{ticker}: {len(df)} bars | {df.index[0]} -> {df.index[-1]} | first_close={df['close'].iloc[0]:.4f} last_close={df['close'].iloc[-1]:.4f} first_vol={df['volume'].iloc[0]:.0f} last_vol={df['volume'].iloc[-1]:.0f}")
    else:
        print(f"{ticker}: no data")

connector.disconnect()