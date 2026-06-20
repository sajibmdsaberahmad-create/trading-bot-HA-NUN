#!/usr/bin/env python3
"""
core/config.py — Single source of truth for every tunable parameter.

If you only read one file in this whole project, make it this one.
Every number that controls risk, money, or behaviour lives here.
Nothing else in the codebase should contain a hardcoded risk number.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

@dataclass
class BotConfig:
    """
    All parameters for the bot. CLI flags (see main.py) override these
    defaults at startup. Edit this file directly for permanent changes,
    or use --flags for one-off runs.
    """

    # ════════════════════════════════════════════════════════════════════
    # INSTRUMENT
    # ════════════════════════════════════════════════════════════════════
    TICKER:   str = "SPY"
    EXCHANGE: str = "SMART"
    CURRENCY: str = "USD"

    # ════════════════════════════════════════════════════════════════════
    # IB GATEWAY CONNECTION
    # ════════════════════════════════════════════════════════════════════
    IB_HOST:       str  = "127.0.0.1"
    IB_PORT:       int  = 7497     # 7497 = paper, 7496 = live, 4002/4001 = IB Gateway variants
    IB_CLIENT_ID:  int  = 1
    PAPER_TRADING: bool = True     # Never flip to False without 30+ days of paper history

    # Reconnection behaviour (handles VPS network blips / ISP drops)
    RECONNECT_MAX_ATTEMPTS:   int = 10
    RECONNECT_BASE_DELAY_SEC: int = 2     # exponential backoff: 2,4,8,16...capped below
    RECONNECT_MAX_DELAY_SEC:  int = 60
    HEARTBEAT_TIMEOUT_SEC:    int = 15    # if no IB event in this long, assume connection dead

    # ════════════════════════════════════════════════════════════════════
    # CAPITAL & ACCOUNT SIZE
    # ════════════════════════════════════════════════════════════════════
    # The bot reads live account equity from IB and scales everything off
    # ACCOUNT_EQUITY automatically — it is not a fixed number. INITIAL_CASH
    # is only the fallback used for backtests / paper-sim when no IB
    # connection exists yet (e.g. warmup mode).
    INITIAL_CASH: float = 1_000.0

    # As the account grows, risk per trade grows with it (see RISK section).
    # This is what lets the same config file work for a $1,000 account
    # today and a $50,000 account later with zero code changes.

    # Per-trade capital cap: never deploy more than this per single trade,
    # regardless of account size. Use this to fragment a large account
    # into $1,000 chunks across multiple tickers.
    MAX_TRADE_SIZE_USD: float = 1_000.0

    TRANSACTION_COST_PCT: float = 0.001   # 0.1% per trade, both sides

    # ════════════════════════════════════════════════════════════════════
    # RISK MANAGEMENT — HARDCODED, THE AI CANNOT OVERRIDE THESE
    # ════════════════════════════════════════════════════════════════════
    # This is the most important section in the file. Read core/risk.py
    # alongside this to see exactly how each number is used.

    # ---- Sizing Mode ----
    # "risk_based" uses traditional ATR-based stop distance and determines shares from that.
    # "full_cash" deploys your entire cash (MAX_POSITION_PCT of equity) and back-calculates
    # the stop-loss distance dynamically so that the potential loss remains exactly $50/risk limit.
    # This is perfect for high-momentum/penny stocks with small accounts.
    SIZING_MODE: str = "risk_based"

    # ---- Per-trade dollar risk (this answers "max $50 loss on $1k") ----
    # The bot computes share quantity from this, NOT from % of cash.
    # risk_amount = ACCOUNT_EQUITY * RISK_PER_TRADE_PCT
    # shares      = risk_amount / stop_distance_in_dollars
    # This GUARANTEES the dollar loss cap regardless of price or volatility,
    # as long as the fill price is within MAX_ACCEPTABLE_SLIPPAGE_PCT of the
    # stop trigger (see slippage protection below).
    RISK_PER_TRADE_PCT: float = 0.05      # 5% of equity = $50 on a $1,000 account

    # Absolute dollar ceiling as a second backstop, independent of %.
    # Whichever is SMALLER (the % calc or this) wins. Protects you if
    # equity is misreported or balloons unexpectedly.
    MAX_RISK_PER_TRADE_USD: float = 50.0

    # ---- Initial stop-loss distance (ATR-based, calculated per trade) ----
    # The bot does NOT use a fixed % stop. It computes the stop distance
    # from current market volatility (ATR = Average True Range) so the
    # stop is wide in choppy markets (avoids noise stop-outs) and tight
    # in calm markets (avoids giving back unnecessary edge).
    STOP_ATR_MULTIPLIER:     float = 1.5   # stop = entry -/+ (ATR14 * this)
    MIN_STOP_DISTANCE_PCT:   float = 0.003 # never let stop be tighter than 0.3% (avoids noise)
    MAX_STOP_DISTANCE_PCT:   float = 0.02  # never let stop be wider than 2% (caps worst case)

    # ---- Trailing STOP-LOSS (protects against giving back gains AND limits loss) ----
    TRAILING_STOP_ENABLED:     bool  = True
    TRAILING_STOP_ACTIVATE_PCT: float = 0.005  # only starts trailing once position is +0.5% in profit
    TRAILING_STOP_ATR_MULTIPLIER: float = 1.2  # trail distance = ATR14 * this (tighter than initial stop)

    # ---- Trailing PROFIT TAKER (separate concept: locks in profit as it runs) ----
    # Once price moves favourably past TRAILING_PROFIT_ACTIVATE_PCT, a
    # profit floor is set and ratcheted up behind price. This is what lets
    # winners run further than a fixed take-profit would, while still
    # guaranteeing you keep most of an unrealised gain if price reverses.
    TRAILING_PROFIT_ENABLED:        bool  = True
    TRAILING_PROFIT_ACTIVATE_PCT:   float = 0.01   # arms after +1% unrealised gain
    TRAILING_PROFIT_GIVEBACK_PCT:   float = 0.40   # allow giving back 40% of peak gain, lock in the rest

    # ---- Hard take-profit (belt-and-suspenders ceiling) ----
    # Predictive target computed from ATR + recent momentum (see
    # core/risk.py compute_take_profit). This is a HARD order sent to IB —
    # it fires even if your machine is offline. Trailing profit can still
    # exit earlier and better; this is just the worst-case-still-good exit.
    TAKE_PROFIT_ATR_MULTIPLIER: float = 2.5   # target = entry +/- (ATR14 * this)
    MIN_REWARD_RISK_RATIO:      float = 1.5   # target distance must be >= 1.5x stop distance

    # ---- Slippage / fill protection ----
    MAX_ACCEPTABLE_SLIPPAGE_PCT: float = 0.004  # if fill would be >0.4% worse than signal price, use LIMIT not MARKET
    USE_LIMIT_ORDERS_IN_FAST_MARKETS: bool = True

    # ---- Account-level circuit breakers ----
    MAX_DAILY_LOSS_PCT:      float = 0.03   # halt all trading if down 3% on the day (~$30 on $1k)
    MAX_WEEKLY_LOSS_PCT:     float = 0.08   # halt the week if down 8%
    MAX_CONSECUTIVE_LOSSES:  int   = 4      # halt after N consecutive losing trades (cool-off)
    COOL_OFF_MINUTES_AFTER_HALT: int = 60   # minutes to wait before re-arming after a halt

    # ---- Position & exposure limits ----
    FULL_CASH_ORDER_SIZE_USD: Optional[float] = None # When SIZING_MODE = "full_cash", use this dollar amount as order size
    DEFAULT_MAX_POSITION_PCT: float = 0.90   # For "risk_based" mode: never deploy more than 90% of equity in one position
    MAX_SHARES_PER_TRADE:  int   = 2_000  # absolute share-count cap regardless of price
    MIN_CASH_RESERVE_PCT:  float = 0.05   # always keep >=5% cash as buffer
    MAX_CONCURRENT_POSITIONS: int = 1     # this bot trades ONE symbol; one position at a time

    # ════════════════════════════════════════════════════════════════════
    # MARKET DATA / TICK STREAM
    # ════════════════════════════════════════════════════════════════════
    # IB Gateway's fastest true feed is tick-by-tick last-trade prints
    # (reqTickByTickData), which arrive as trades happen on the exchange —
    # sub-second in liquid names, but NOT a guaranteed fixed interval
    # (there is no "millisecond bar" concept on real exchanges; ticks
    # arrive whenever a trade prints). The bot consumes this tick stream
    # directly for stop/target monitoring (every tick, no waiting for a
    # bar to close) while still building 5-second and 1-minute bars for
    # the feature engine and the PPO agent.
    USE_TICK_STREAM:        bool = True
    TICK_BUFFER_MAXLEN:     int  = 5_000
    FAST_BAR_SECONDS:       int  = 5        # fast bar used for stop/target intrabar checks
    DECISION_BAR:           str  = "1 min"  # bar size the PPO agent makes decisions on

    # ════════════════════════════════════════════════════════════════════
    # OBSERVATION WINDOW (PPO input)
    # ════════════════════════════════════════════════════════════════════
    WINDOW_SIZE: int = 30
    N_FEATURES:  int = 14   # see core/features.py — 11 original + 3 new predictive features

    # ════════════════════════════════════════════════════════════════════
    # WARM-UP (HISTORICAL) TRAINING
    # ════════════════════════════════════════════════════════════════════
    HISTORY_DURATION: str   = "10 Y"   # MAXIMUM available history for training memory
    HISTORY_BAR_SIZE:  str  = "1 day"
    WARMUP_TIMESTEPS:  int   = 500_000  # Extended training for deep memory
    WARMUP_SPLIT_PCT:  float = 0.70

    # ════════════════════════════════════════════════════════════════════
    # ONLINE FINE-TUNING (during live trading)
    # ════════════════════════════════════════════════════════════════════
    FINE_TUNE_EVERY_BARS:  int = 30
    FINE_TUNE_STEPS:       int = 2_048
    MIN_BARS_FOR_FINETUNE: int = 60

    # ════════════════════════════════════════════════════════════════════
    # PPO HYPERPARAMETERS
    # ════════════════════════════════════════════════════════════════════
    PPO_N_STEPS:       int   = 2_048   # Larger batch for stable learning
    PPO_BATCH_SIZE:    int   = 256
    PPO_N_EPOCHS:      int   = 15      # More epochs per update
    PPO_CLIP_RANGE:    float = 0.15
    PPO_LR:            float = 2.5e-4
    PPO_GAMMA:         float = 0.99
    PPO_GAE_LAM:       float = 0.95
    PPO_ENT_COEF:      float = 0.01
    PPO_VF_COEF:       float = 0.5
    PPO_MAX_GRAD_NORM: float = 0.5
    PPO_NET_ARCH:      tuple = (512, 256, 128)

    # ════════════════════════════════════════════════════════════════════
    # GITHUB AUTO-PUSH
    # ════════════════════════════════════════════════════════════════════
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_REPO:  str = os.getenv("GITHUB_REPO", "")  # e.g. "sajibmdsaberahmad-create/Algo"

    # ════════════════════════════════════════════════════════════════════
    # SCALPER / INSTITUTIONAL MOMENTUM MODE
    # ════════════════════════════════════════════════════════════════════
    # When TRADING_MODE = "scalper", the bot runs the institutional
    # momentum scalper instead of the PPO agent. It scans a universe
    # of penny stocks, detects institutional accumulation, and executes
    # ultra-tight scalp trades with aggressive trailing exits.
    TRADING_MODE: str = "scalper"   # "ppo" (original) or "scalper" (new)
    
    # Scalper-specific risk parameters (overrides for scalping)
    SCALP_STOP_ATR_MULTIPLIER: float = 0.7   # Tighter: ATR(5) * 0.7
    SCALP_MIN_STOP_PCT: float = 0.003       # 0.3% minimum stop
    SCALP_MAX_STOP_PCT: float = 0.010       # 1.0% maximum stop
    SCALP_TP_ATR_MULTIPLIER: float = 1.5    # Tighter: ATR(5) * 1.5
    SCALP_MAX_TP_PCT: float = 0.03          # Cap gain at 3% for scalping
    SCALP_MIN_RR: float = 1.5              # Min risk/reward ratio
    SCALP_TRAILING_ACTIVATE_PCT: float = 0.002  # Trail from +0.2% profit
    SCALP_TRAILING_ATR_MULTIPLIER: float = 0.6  # Tighter trailing distance
    SCALP_PROFIT_ACTIVATE_PCT: float = 0.005    # Profit taker arms at +0.5%
    SCALP_PROFIT_GIVEBACK_PCT: float = 0.30     # Give back 30% of peak gain
    
    # Scanner frequency
    SCAN_INTERVAL_SECONDS: int = 300  # Rescan every 5 minutes
    
    # ════════════════════════════════════════════════════════════════════
    # NOTIFICATIONS
    # ════════════════════════════════════════════════════════════════════
    # Telegram is the primary channel: free, instant, push-to-phone,
    # identical setup on Mac and on a headless VPS. Fill these in via
    # environment variables (see docs/LAUNCH_GUIDE.md) — never hardcode
    # a bot token in source code.
    TELEGRAM_ENABLED:  bool = True
    TELEGRAM_BOT_TOKEN: str = os.getenv("TRADING_BOT_TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID:   str = os.getenv("TRADING_BOT_TELEGRAM_CHAT_ID", "")

    EMAIL_ENABLED: bool = False   # optional fallback; configure via env vars if used
    EMAIL_SMTP_HOST: str = os.getenv("TRADING_BOT_SMTP_HOST", "")
    EMAIL_SMTP_PORT: int = int(os.getenv("TRADING_BOT_SMTP_PORT", "587"))
    EMAIL_FROM:      str = os.getenv("TRADING_BOT_EMAIL_FROM", "")
    EMAIL_TO:        str = os.getenv("TRADING_BOT_EMAIL_TO", "")
    EMAIL_PASSWORD:  str = os.getenv("TRADING_BOT_EMAIL_PASSWORD", "")

    NOTIFY_ON_TRADE_OPEN:    bool = True
    NOTIFY_ON_TRADE_CLOSE:   bool = True
    NOTIFY_ON_STOP_TRIGGER:  bool = True
    NOTIFY_ON_RISK_HALT:     bool = True
    NOTIFY_ON_RECONNECT:     bool = True
    NOTIFY_ON_ERROR:         bool = True
    NOTIFY_DAILY_SUMMARY:    bool = True
    DAILY_SUMMARY_HOUR_UTC:  int  = 21   # ~4-5pm ET, after US close

    # ════════════════════════════════════════════════════════════════════
    # FILES
    # ════════════════════════════════════════════════════════════════════
    MODEL_PATH:    str = "ppo_trader.zip"
    LOG_PATH:      str = "trading_bot.log"
    PERF_PATH:     str = "performance.csv"
    STATE_PATH:    str = "bot_state.json"   # persisted position/risk state across restarts

    def risk_amount_usd(self, account_equity: float) -> float:
        """
        The dollar amount the bot is willing to lose on the NEXT trade,
        recalculated from current account equity every time it's called.
        This is what makes the bot scale automatically as the account
        grows or shrinks — no code change needed when you go from
        $1,000 to $10,000.
        """
        pct_based = account_equity * self.RISK_PER_TRADE_PCT
        return min(pct_based, self.MAX_RISK_PER_TRADE_USD)
