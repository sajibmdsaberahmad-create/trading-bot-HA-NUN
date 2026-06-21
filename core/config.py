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
    HEARTBEAT_TIMEOUT_SEC:    int = 30    # if no IB event in this long, assume connection dead

    # ════════════════════════════════════════════════════════════════════
    # CAPITAL & ACCOUNT SIZE
    # ════════════════════════════════════════════════════════════════════
    # The bot reads live account equity from IB and scales everything off
    # ACCOUNT_EQUITY automatically — it is not a fixed number. INITIAL_CASH
    # is only the fallback used for backtests / paper-sim when no IB
    # connection exists yet (e.g. warmup mode).
    INITIAL_CASH: float = 1_000.0

    MAX_TRADE_SIZE_USD: float = 1_000.0

    TRANSACTION_COST_PCT: float = 0.001   # 0.1% per trade, both sides

    # ════════════════════════════════════════════════════════════════════
    # RISK MANAGEMENT — HARDCODED, THE AI CANNOT OVERRIDE THESE
    # ════════════════════════════════════════════════════════════════════
    SIZING_MODE: str = "risk_based"

    RISK_PER_TRADE_PCT: float = 0.05      # 5% of equity = $50 on a $1,000 account
    MAX_RISK_PER_TRADE_USD: float = 50.0

    STOP_ATR_MULTIPLIER:     float = 1.5
    MIN_STOP_DISTANCE_PCT:   float = 0.003
    MAX_STOP_DISTANCE_PCT:   float = 0.02

    TRAILING_STOP_ENABLED:     bool  = True
    TRAILING_STOP_ACTIVATE_PCT: float = 0.005
    TRAILING_STOP_ATR_MULTIPLIER: float = 1.2

    TRAILING_PROFIT_ENABLED:        bool  = True
    TRAILING_PROFIT_ACTIVATE_PCT:   float = 0.01
    TRAILING_PROFIT_GIVEBACK_PCT:   float = 0.40

    TAKE_PROFIT_ATR_MULTIPLIER: float = 2.5
    MIN_REWARD_RISK_RATIO:      float = 1.5

    MAX_ACCEPTABLE_SLIPPAGE_PCT: float = 0.004
    USE_LIMIT_ORDERS_IN_FAST_MARKETS: bool = True

    MAX_DAILY_LOSS_PCT:      float = 0.03
    MAX_WEEKLY_LOSS_PCT:     float = 0.08
    MAX_CONSECUTIVE_LOSSES:  int   = 4
    COOL_OFF_MINUTES_AFTER_HALT: int = 60

    FULL_CASH_ORDER_SIZE_USD: Optional[float] = None
    DEFAULT_MAX_POSITION_PCT: float = 0.90
    MAX_SHARES_PER_TRADE:  int   = 2_000
    MIN_CASH_RESERVE_PCT:  float = 0.05
    MAX_CONCURRENT_POSITIONS: int = 1

    # ════════════════════════════════════════════════════════════════════
    # MARKET DATA / TICK STREAM
    # ════════════════════════════════════════════════════════════════════
    USE_TICK_STREAM:        bool = True
    TICK_BUFFER_MAXLEN:     int  = 5_000
    FAST_BAR_SECONDS:       int  = 5
    DECISION_BAR:           str  = "1 min"

    # ════════════════════════════════════════════════════════════════════
    # OBSERVATION WINDOW (PPO input)
    # ════════════════════════════════════════════════════════════════════
    WINDOW_SIZE: int = 30
    N_FEATURES:  int = 18   # Increased from 14 to 18 — see core/features_enhanced.py

    # ════════════════════════════════════════════════════════════════════
    # WARM-UP (HISTORICAL) TRAINING
    # ════════════════════════════════════════════════════════════════════
    HISTORY_DURATION: str   = "10 Y"
    HISTORY_BAR_SIZE:  str  = "1 day"
    WARMUP_TIMESTEPS:  int   = 1_000_000  # Doubled for deeper learning
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
    PPO_N_STEPS:       int   = 2_048
    PPO_BATCH_SIZE:    int   = 256
    PPO_N_EPOCHS:      int   = 15
    PPO_CLIP_RANGE:    float = 0.15
    PPO_LR:            float = 2.5e-4
    PPO_GAMMA:         float = 0.99
    PPO_GAE_LAM:       float = 0.95
    PPO_ENT_COEF:      float = 0.01
    PPO_VF_COEF:       float = 0.5
    PPO_MAX_GRAD_NORM: float = 0.5
    PPO_NET_ARCH:      tuple = (1024, 512, 256)  # Deeper network

    # ════════════════════════════════════════════════════════════════════
    # ENHANCED AI FEATURES
    # ════════════════════════════════════════════════════════════════════
    # Enable the enhanced AI reasoning engine
    USE_ENHANCED_AI: bool = True

    # Minimum confidence threshold for action execution (0.0 to 1.0)
    # Higher = fewer but higher-quality trades
    CONFIDENCE_THRESHOLD: float = 0.55

    # Ensemble voting: combine PPO with rule-based strategies
    USE_ENSEMBLE: bool = True

    # Market regime classification
    USE_REGIME_CLASSIFIER: bool = True

    # Guardrails enabled?
    USE_GUARDRAILS: bool = True

    # Guardrail override level: 0=full, 1=warn, 2=disabled
    GUARDRAIL_OVERRIDE_LEVEL: int = 0

    # Maximum number of model backup versions to keep
    MODEL_BACKUPS_TO_KEEP: int = 20

    # ════════════════════════════════════════════════════════════════════
    # GITHUB AUTO-PUSH
    # ════════════════════════════════════════════════════════════════════
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_REPO:  str = os.getenv("GITHUB_REPO", "")

    # ════════════════════════════════════════════════════════════════════
    # SCALPER / INSTITUTIONAL MOMENTUM MODE
    # ════════════════════════════════════════════════════════════════════
    TRADING_MODE: str = "scalper"
    SCALP_STOP_ATR_MULTIPLIER: float = 0.7
    SCALP_MIN_STOP_PCT: float = 0.003
    SCALP_MAX_STOP_PCT: float = 0.010
    SCALP_TP_ATR_MULTIPLIER: float = 1.5
    SCALP_MAX_TP_PCT: float = 0.03
    SCALP_MIN_RR: float = 1.5
    SCALP_TRAILING_ACTIVATE_PCT: float = 0.002
    SCALP_TRAILING_ATR_MULTIPLIER: float = 0.6
    SCALP_PROFIT_ACTIVATE_PCT: float = 0.005
    SCALP_PROFIT_GIVEBACK_PCT: float = 0.30
    SCAN_INTERVAL_SECONDS: int = 300

    # ════════════════════════════════════════════════════════════════════
    # OLLAMA LOCAL LLM REASONING HEAD
    # ════════════════════════════════════════════════════════════════════
    OLLAMA_ENABLED: bool = False
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"
    OLLAMA_TIMEOUT: int = 10
    OLLAMA_MAX_TOKENS: int = 256
    OLLAMA_TEMPERATURE: float = 0.7

    # ════════════════════════════════════════════════════════════════════
    # MULTI-REPO GIT ARCHITECTURE (HA-NUN / Grandmaster / Logs)
    # ════════════════════════════════════════════════════════════════════
    GITHUB_HA_NUN_REPO: str = os.getenv("GITHUB_HA_NUN_REPO", "")
    GITHUB_GRANDMASTER_REPO: str = os.getenv("GITHUB_GRANDMASTER_REPO", "")
    GITHUB_LOGS_REPO: str = os.getenv("GITHUB_LOGS_REPO", "")
    GITHUB_PAT: str = os.getenv("GITHUB_PAT", "")
    MAX_GIT_PUSH_RETRIES: int = 3
    GIT_PUSH_TIMEOUT_SEC: int = 30

    # ════════════════════════════════════════════════════════════════════
    # HIDDEN MARKOV REGIME SWITCHING (HMRS)
    # ════════════════════════════════════════════════════════════════════
    HMRS_ENABLED: bool = True
    HMRS_NUM_REGIMES: int = 4  # QuietGrowth, HighVolTrend, LiquidChop, LiquidityShock
    HMRS_LOOKBACK_DAYS: int = 90
    HMRS_RETRAIN_HOURS: int = 24
    HMRS_MIN_REGIME_PROB: float = 0.35

    # ════════════════════════════════════════════════════════════════════
    # GRANDMASTER DISTILLATION (210M Teacher → 21M Student)
    # ════════════════════════════════════════════════════════════════════
    GRANDMASTER_ENABLED: bool = True
    GRANDMASTER_D_MODEL: int = 768
    GRANDMASTER_NUM_HEADS: int = 12
    GRANDMASTER_FFN_DIM: int = 3072
    GRANDMASTER_NUM_LAYERS: int = 8
    DISTILLATION_TEMPERATURE: float = 3.0
    DISTILLATION_ALPHA: float = 0.4
    DISTILLATION_EPOCHS: int = 10
    DISTILLATION_LR: float = 1e-4

    # ════════════════════════════════════════════════════════════════════
    # STATIONARY FEATURE ENGINEERING
    # ════════════════════════════════════════════════════════════════════
    USE_FRACTIONAL_DIFF: bool = True
    FRACTIONAL_DIFF_D: float = 0.4  # Order of fractional differentiation
    FRAC_DIFF_WINDOW: int = 60
    USE_VPIN: bool = True
    USE_AMIHUD: bool = True
    AMIHUD_WINDOW: int = 20
    VPIN_WINDOW: int = 50

    # ════════════════════════════════════════════════════════════════════
    # ADVANCED TRAINING (Purged/Embargoed CV + Regime Bootstrapping)
    # ════════════════════════════════════════════════════════════════════
    PURGE_EMBARGO_ENABLED: bool = True
    PURGE_BARS: int = 12  # 12 minutes for 1-min bars
    EMBARGO_BARS: int = 6
    REGIME_BOOTSTRAP_ENABLED: bool = True
    BOOTSTRAP_SAMPLES: int = 5000
    TRAIN_VAL_SPLIT: float = 0.70

    # ════════════════════════════════════════════════════════════════════
    # OFF-HOURS TRAINING SUBPROCESS
    # ════════════════════════════════════════════════════════════════════
    OFF_HOURS_TRAINING_ENABLED: bool = True
    TRAINING_START_HOUR_UTC: int = 21  # 9 PM UTC = after US market close
    TRAINING_MEMORY_LIMIT_MB: int = 4096
    TRAINING_TIMEOUT_MIN: int = 480  # 8 hours max

    # ════════════════════════════════════════════════════════════════════
    # GIT BLOAT GUARDRAILS
    # ════════════════════════════════════════════════════════════════════
    MAX_RAW_DATA_DAYS_IN_GIT: int = 30  # Rolling window only
    GIT_LFS_THRESHOLD_MB: float = 10.0
    AUTO_PRUNE_DAILY_LOGS: bool = True

    # ════════════════════════════════════════════════════════════════════
    # NOTIFICATIONS
    # ════════════════════════════════════════════════════════════════════
    TELEGRAM_ENABLED:  bool = True
    TELEGRAM_BOT_TOKEN: str = os.getenv("TRADING_BOT_TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID:   str = os.getenv("TRADING_BOT_TELEGRAM_CHAT_ID", "")

    EMAIL_ENABLED: bool = False
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
    DAILY_SUMMARY_HOUR_UTC:  int  = 21

    # ════════════════════════════════════════════════════════════════════
    # FILES
    # ════════════════════════════════════════════════════════════════════
    MODEL_PATH:    str = "ppo_trader.zip"
    LOG_PATH:      str = "HA-NUN.log"
    PERF_PATH:     str = "performance.csv"
    STATE_PATH:    str = "bot_state.json"
    AUDIT_PATH:    str = "audit_trail.jsonl"

    def risk_amount_usd(self, account_equity: float) -> float:
        pct_based = account_equity * self.RISK_PER_TRADE_PCT
        return min(pct_based, self.MAX_RISK_PER_TRADE_USD)

    # ════════════════════════════════════════════════════════════════════
    # OLLAMA META-OPTIMIZER (Active File Mutation)
    # ════════════════════════════════════════════════════════════════════
    OLLAMA_META_OPTIMIZER_ENABLED: bool = True
    META_OPTIMIZE_ONLY_WHEN_MARKET_CLOSED: bool = True
    MAX_PARAM_MUTATIONS_PER_DAY: int = 5
    META_OPTIMIZER_MODEL: str = "llama3:70b-instruct"
