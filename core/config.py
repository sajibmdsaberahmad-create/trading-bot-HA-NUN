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


def _total_ram_mb() -> int:
    try:
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024))
    except Exception:
        return 8192


_LOW_RAM = _total_ram_mb() <= 10_240
_8GB_RAM = _total_ram_mb() <= 8192

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
    # MULTI-TIMEFRAME TRAINING CONFIGURATIONS
    # ════════════════════════════════════════════════════════════════════
    # Each timeframe adjusts the bar size and risk parameters
    TRADING_TIMEFRAME: str = "1min"  # Options: "1min", "5min", "1h", "4h", "1d"
    
    # Timeframe-specific bar sizes for IB data fetch
    BAR_SIZE_MAP: dict = field(default_factory=lambda: {
        "scalper_1min": "1 min",
        "scalper_5min": "5 mins",
        "swing_1h": "1 hour",
        "swing_4h": "4 hours",
        "position_1d": "1 day"
    })
    
    # Timeframe-specific risk parameters (auto-adjusted based on timeframe)
    TIMEFRAME_RISK: dict = field(default_factory=lambda: {
        "scalper_1min": {
            "stop_atr_mult": 0.7,
            "tp_atr_mult": 1.5,
            "max_stop_pct": 0.010,
            "max_tp_pct": 0.03,
        },
        "scalper_5min": {
            "stop_atr_mult": 1.0,
            "tp_atr_mult": 2.0,
            "max_stop_pct": 0.015,
            "max_tp_pct": 0.05,
        },
        "swing_1h": {
            "stop_atr_mult": 1.5,
            "tp_atr_mult": 3.0,
            "max_stop_pct": 0.025,
            "max_tp_pct": 0.08,
        },
        "swing_4h": {
            "stop_atr_mult": 2.0,
            "tp_atr_mult": 4.0,
            "max_stop_pct": 0.035,
            "max_tp_pct": 0.12,
        },
        "position_1d": {
            "stop_atr_mult": 2.5,
            "tp_atr_mult": 5.0,
            "max_stop_pct": 0.050,
            "max_tp_pct": 0.20,
        }
    })

    # ════════════════════════════════════════════════════════════════════
    # IB GATEWAY CONNECTION
    # ════════════════════════════════════════════════════════════════════
    IB_HOST:       str  = "127.0.0.1"
    IB_PORT:       int  = 4002     # 4002/4001 = IB Gateway, 7497 = TWS paper, 7496 = TWS live
    IB_CLIENT_ID:  int  = 1       # IB API client ID (use 1–10; must be unique per connection)
    PAPER_TRADING: bool = True     # Never flip to False without 30+ days of paper history
    # Paper $1M+ account: AI sizes from IB equity, learns without small-account caps
    AI_PAPER_FREE_LEARNING: bool = os.getenv(
        "AI_PAPER_FREE_LEARNING", "true"
    ).lower() in ("1", "true", "yes")
    PAPER_EQUITY_HINT: float = float(os.getenv("PAPER_EQUITY_HINT", "1000000"))

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

    MAX_TRADE_SIZE_USD: float = 0.0   # 0 = no cap (paper uses equity); set >0 to hard-cap deploy

    TRANSACTION_COST_PCT: float = 0.001   # 0.1% per trade, both sides

    # ════════════════════════════════════════════════════════════════════
    # RISK MANAGEMENT — HARDCODED, THE AI CANNOT OVERRIDE THESE
    # ════════════════════════════════════════════════════════════════════
    SIZING_MODE: str = "risk_based"

    RISK_PER_TRADE_PCT: float = 0.05      # 5% of equity = $50 on a $1,000 account
    MAX_RISK_PER_TRADE_USD: float = 250_000.0  # ceiling; paper uses equity % when AI_PAPER_FREE_LEARNING

    STOP_ATR_MULTIPLIER:     float = 1.5
    MIN_STOP_DISTANCE_PCT:   float = 0.003
    MAX_STOP_DISTANCE_PCT:   float = 0.02

    TRAILING_STOP_ENABLED:     bool  = True
    TRAILING_STOP_ACTIVATE_PCT: float = 0.005
    TRAILING_STOP_ATR_MULTIPLIER: float = 1.2

    TRAILING_PROFIT_ENABLED:        bool  = True
    TRAILING_PROFIT_ACTIVATE_PCT:   float = 0.01
    TRAILING_PROFIT_GIVEBACK_PCT:   float = 0.40

    # Opportunistic profit hunting — thresholds AI-tunes via param_bounds (not static doctrine)
    PROFIT_HUNT_ENABLED: bool = os.getenv(
        "PROFIT_HUNT_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    PROFIT_HUNT_MECHANICAL_BYPASS_COUNCIL: bool = os.getenv(
        "PROFIT_HUNT_MECHANICAL_BYPASS_COUNCIL", "true"
    ).lower() in ("1", "true", "yes")
    SPIKE_TOP_EXIT_ENABLED: bool = os.getenv(
        "SPIKE_TOP_EXIT_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    SPIKE_TOP_INTRABAR_ENABLED: bool = os.getenv(
        "SPIKE_TOP_INTRABAR_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    SPIKE_TOP_MIN_GAIN_PCT: float = float(os.getenv("SPIKE_TOP_MIN_GAIN_PCT", "0.005"))
    SPIKE_TOP_MIN_VOL_RATIO: float = float(os.getenv("SPIKE_TOP_MIN_VOL_RATIO", "1.15"))
    PROFIT_HUNT_MIN_PNL_PCT: float = float(os.getenv("PROFIT_HUNT_MIN_PNL_PCT", "0.003"))
    HARD_TP_OVERRIDE_TRAILING: bool = os.getenv(
        "HARD_TP_OVERRIDE_TRAILING", "true"
    ).lower() in ("1", "true", "yes")
    EXTENDED_PROFIT_GIVEBACK_PCT: float = float(os.getenv("EXTENDED_PROFIT_GIVEBACK_PCT", "0.30"))
    RL_MISSED_PROFIT_HUNT_PENALTY: float = float(os.getenv("RL_MISSED_PROFIT_HUNT_PENALTY", "-0.75"))
    # Learn from IB market-data failures (162 no data, 420 no permissions) — skip bad tickers
    MARKET_DATA_LEARN_ENABLED: bool = os.getenv(
        "MARKET_DATA_LEARN_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    MARKET_DATA_SKIP_COOLDOWN_SEC: float = float(
        os.getenv("MARKET_DATA_SKIP_COOLDOWN_SEC", "300")
    )
    # Profit hunting is THE primary mission — full freedom within hard risk limits
    PROFIT_HUNT_PRIMARY_GOAL: bool = os.getenv(
        "PROFIT_HUNT_PRIMARY_GOAL", "true"
    ).lower() in ("1", "true", "yes")
    PROFIT_HUNT_FULL_FREEDOM: bool = os.getenv(
        "PROFIT_HUNT_FULL_FREEDOM", "true"
    ).lower() in ("1", "true", "yes")
    PROFIT_HUNT_TRACK_ALL: bool = os.getenv(
        "PROFIT_HUNT_TRACK_ALL", "true"
    ).lower() in ("1", "true", "yes")
    PROFIT_HUNT_SKIP_MIN_HOLD: bool = os.getenv(
        "PROFIT_HUNT_SKIP_MIN_HOLD", "true"
    ).lower() in ("1", "true", "yes")

    # AI fast execution — prioritize top names, fewer bars, spike fast-entry
    AI_FAST_EXECUTION: bool = os.getenv(
        "AI_FAST_EXECUTION", "true"
    ).lower() in ("1", "true", "yes")
    AI_STREAM_PRIORITY_COUNT: int = int(os.getenv("AI_STREAM_PRIORITY_COUNT", "8"))
    AI_WARM_PRIORITY_COUNT: int = int(os.getenv("AI_WARM_PRIORITY_COUNT", "10"))
    AI_MIN_BARS_FOCUS: int = int(os.getenv("AI_MIN_BARS_FOCUS", "6"))
    AI_MIN_BARS_SCAN: int = int(os.getenv("AI_MIN_BARS_SCAN", "10"))
    AI_SPIKE_FAST_ENTRY: bool = os.getenv(
        "AI_SPIKE_FAST_ENTRY", "true"
    ).lower() in ("1", "true", "yes")
    AI_SPIKE_FAST_MIN_RATIO: float = float(os.getenv("AI_SPIKE_FAST_MIN_RATIO", "1.15"))
    AI_SPIKE_FAST_MIN_SCORE: float = float(os.getenv("AI_SPIKE_FAST_MIN_SCORE", "15"))
    AI_TICK_STREAM_COUNT: int = int(os.getenv("AI_TICK_STREAM_COUNT", "4"))
    IB_MAX_REALTIME_BAR_STREAMS: int = int(os.getenv("IB_MAX_REALTIME_BAR_STREAMS", "4"))
    AI_PRIORITY_TICK_STREAMS: bool = os.getenv(
        "AI_PRIORITY_TICK_STREAMS", "false"
    ).lower() in ("1", "true", "yes")
    AI_SPIKE_ATTEMPTS_PER_CYCLE: int = int(os.getenv("AI_SPIKE_ATTEMPTS_PER_CYCLE", "3"))
    FAST_MONITOR_SEC: float = float(os.getenv("FAST_MONITOR_SEC", "0.15"))

    TAKE_PROFIT_ATR_MULTIPLIER: float = 2.5
    MIN_REWARD_RISK_RATIO:      float = 2.0
    MIN_REWARD_RISK_TOLERANCE:  float = float(os.getenv("MIN_REWARD_RISK_TOLERANCE", "0.02"))

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
    MAX_CONCURRENT_POSITIONS: int = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))

    # ════════════════════════════════════════════════════════════════════
    # MARKET DATA / TICK STREAM
    # ════════════════════════════════════════════════════════════════════
    USE_TICK_STREAM:        bool = True
    TICK_BUFFER_MAXLEN:     int  = 5_000
    FAST_BAR_SECONDS:       int  = 5
    DECISION_BAR:           str  = "1 min"

    # ════════════════════════════════════════════════════════════════════
    # PRE-MARKET / EXTENDED HOURS / OVERNIGHT
    # ════════════════════════════════════════════════════════════════════
    ALLOW_PRE_MARKET_TRADING:  bool = os.getenv(
        "ALLOW_PRE_MARKET_TRADING", "true"
    ).lower() in ("1", "true", "yes")
    ALLOW_AFTER_HOURS_TRADING: bool = os.getenv(
        "ALLOW_AFTER_HOURS_TRADING", "true"
    ).lower() in ("1", "true", "yes")
    ALLOW_OVERNIGHT_TRADING: bool = os.getenv(
        "ALLOW_OVERNIGHT_TRADING", "true"
    ).lower() in ("1", "true", "yes")
    PRE_MARKET_START:          str  = "04:00"  # ET
    PRE_MARKET_END:            str  = "09:25"   # ET
    AFTER_HOURS_START:         str  = "16:00"   # ET
    AFTER_HOURS_END:           str  = "20:00"   # ET
    MIN_CONFIDENCE_PRE_MARKET: float = float(os.getenv("MIN_CONFIDENCE_PRE_MARKET", "0.70"))
    MIN_CONFIDENCE_AFTER_HOURS: float = float(os.getenv("MIN_CONFIDENCE_AFTER_HOURS", "0.72"))
    MIN_CONFIDENCE_OVERNIGHT: float = float(os.getenv("MIN_CONFIDENCE_OVERNIGHT", "0.78"))

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
    FINE_TUNE_ANCHOR_SAMPLES: int = 256

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
    CONFIDENCE_THRESHOLD: float = 0.52

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
    GITHUB_FORCE_CLI: bool = os.getenv("GITHUB_FORCE_CLI", "true").lower() not in ("0", "false", "no")

    # ════════════════════════════════════════════════════════════════════
    # AI-DRIVEN DYNAMIC TRAILING PROFIT / LOSS
    # ════════════════════════════════════════════════════════════════════
    # When enabled, the AI's confidence and regime classification dynamically
    # tighten/loosen the trailing-profit giveback and trigger early loss exits
    # before the hard stop. The hard $50 max loss is still enforced by risk.py.
    DYNAMIC_TRAILING_ENABLED: bool = True
    EARLY_LOSS_EXIT_ENABLED: bool = True
    EARLY_LOSS_RISK_PCT_THRESHOLD: float = 0.50        # exit when loss > 50% of risk budget
    DYNAMIC_PROFIT_GIVEBACK_MIN: float = 0.20          # 20% giveback (tight)
    DYNAMIC_PROFIT_GIVEBACK_MAX: float = 0.50          # 50% giveback (loose)

    # ════════════════════════════════════════════════════════════════════
    # SCALPER / INSTITUTIONAL ALGO-WAVE RIDER (USER METHODOLOGY)
    # ════════════════════════════════════════════════════════════════════
    TRADING_MODE: str = "scalper"
    
    # USER RULE: Deploy exactly $1,000 per stock when USE_FIXED_DEPLOY_CAP=true
    DEPLOY_PER_STOCK_USD: float = 1000.0
    PILOT_MAX_DEPLOY_USD: float = 2000.0
    USE_FIXED_DEPLOY_CAP: bool = os.getenv("USE_FIXED_DEPLOY_CAP", "false").lower() in (
        "1", "true", "yes"
    )
    USE_MULTI_POSITION: bool = os.getenv("USE_MULTI_POSITION", "true").lower() in (
        "1", "true", "yes"
    )
    AI_MAX_DEPLOY_PCT: float = float(os.getenv("AI_MAX_DEPLOY_PCT", "0"))
    # AI_UNLIMITED_MODE — lift watch/position/score caps; AI decides lock pool & entries
    AI_UNLIMITED_MODE: bool = os.getenv("AI_UNLIMITED_MODE", "true").lower() in (
        "1", "true", "yes"
    )
    AI_MAX_LOCKED_TARGETS: int = int(os.getenv("AI_MAX_LOCKED_TARGETS", "30"))
    AI_MAX_CONCURRENT_POSITIONS: int = int(os.getenv("AI_MAX_CONCURRENT_POSITIONS", "50"))
    AI_SCAN_UNIVERSE_MAX: int = int(os.getenv("AI_SCAN_UNIVERSE_MAX", "80"))
    AI_MIN_LOCK_SCORE: float = float(os.getenv("AI_MIN_LOCK_SCORE", "0"))
    AI_MIN_CASH_RESERVE_PCT: float = float(os.getenv("AI_MIN_CASH_RESERVE_PCT", "0"))
    AI_MAX_SHARES_PER_TRADE: int = int(os.getenv("AI_MAX_SHARES_PER_TRADE", "100000"))
    # When true (default with AI_FULL_CONTROL), deploy/risk/pool/positions are AI session-defined
    AI_DEFINE_ALL_LIMITS: bool = os.getenv("AI_DEFINE_ALL_LIMITS", "true").lower() in (
        "1", "true", "yes"
    )
    AI_FULL_CAPITAL_ACCESS: bool = os.getenv("AI_FULL_CAPITAL_ACCESS", "true").lower() in (
        "1", "true", "yes"
    )
    DEFER_FEATURE_VALIDATION: bool = os.getenv("DEFER_FEATURE_VALIDATION", "true").lower() in (
        "1", "true", "yes"
    )
    DEFER_BAR_WARM_ON_LOCK: bool = os.getenv("DEFER_BAR_WARM_ON_LOCK", "true").lower() in (
        "1", "true", "yes"
    )
    # Scalper micro-forecast — freshest bars + 1–3 bar momentum (spike/entry/exit)
    SCALPER_MICRO_PREDICT_ENABLED: bool = os.getenv(
        "SCALPER_MICRO_PREDICT_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    SCALPER_LIVE_BARS_FIRST: bool = os.getenv(
        "SCALPER_LIVE_BARS_FIRST", "true"
    ).lower() in ("1", "true", "yes")
    # Fast lock: build bars from live streams only — skip IB HMDS historical (162 on OTC/PINK).
    FAST_LOCK_SKIP_HISTORICAL: bool = os.getenv(
        "FAST_LOCK_SKIP_HISTORICAL", "true"
    ).lower() in ("1", "true", "yes")
    PROFIT_HUNT_MAJOR_EXCHANGES_ONLY: bool = os.getenv(
        "PROFIT_HUNT_MAJOR_EXCHANGES_ONLY", "true"
    ).lower() in ("1", "true", "yes")
    PROFIT_HUNT_REJECT_UNKNOWN_EXCHANGE: bool = os.getenv(
        "PROFIT_HUNT_REJECT_UNKNOWN_EXCHANGE", "true"
    ).lower() in ("1", "true", "yes")
    PROFIT_HUNT_MIN_PRICE: float = float(os.getenv("PROFIT_HUNT_MIN_PRICE", "0.50"))
    PROFIT_HUNT_MAX_PRICE: float = float(os.getenv("PROFIT_HUNT_MAX_PRICE", "500.0"))
    PROFIT_LOCK_ULTRA_FAST: bool = os.getenv(
        "PROFIT_LOCK_ULTRA_FAST", "true"
    ).lower() in ("1", "true", "yes")
    DEFERRED_COUNCIL_LEARNING: bool = os.getenv(
        "DEFERRED_COUNCIL_LEARNING", "true"
    ).lower() in ("1", "true", "yes")
    DEFERRED_COUNCIL_MAX_AGE_SEC: float = float(
        os.getenv("DEFERRED_COUNCIL_MAX_AGE_SEC", "120")
    )
    PPO_LEARNING_WEIGHT: float = float(os.getenv("PPO_LEARNING_WEIGHT", "1.5"))
    PPO_LEAD_WHILE_COUNCIL_PENDING: bool = os.getenv(
        "PPO_LEAD_WHILE_COUNCIL_PENDING", "true"
    ).lower() in ("1", "true", "yes")
    PPO_LEARN_EVERY_ENTRY: bool = os.getenv(
        "PPO_LEARN_EVERY_ENTRY", "true"
    ).lower() in ("1", "true", "yes")
    PPO_ENTRY_MICRO_STEPS: int = int(os.getenv("PPO_ENTRY_MICRO_STEPS", "512"))
    AI_STREAM_WATCH_CAP: int = int(os.getenv("AI_STREAM_WATCH_CAP", "10"))
    MICRO_SPIKE_BOOST: float = float(os.getenv("MICRO_SPIKE_BOOST", "0.35"))
    MICRO_FADE_EXIT: float = float(os.getenv("MICRO_FADE_EXIT", "0.55"))
    MICRO_LOSS_EXIT: float = float(os.getenv("MICRO_LOSS_EXIT", "0.58"))
    LOCK_BAR_REFRESH_SEC: float = float(os.getenv("LOCK_BAR_REFRESH_SEC", "90"))

    # USER RULE: Hard stop $50 per trade when USE_FIXED_RISK_CAP=true
    HARD_STOP_USD: float = 50.0
    USE_FIXED_RISK_CAP: bool = os.getenv("USE_FIXED_RISK_CAP", "false").lower() in (
        "1", "true", "yes"
    )
    USE_ACCOUNT_LOSS_HALT: bool = os.getenv("USE_ACCOUNT_LOSS_HALT", "false").lower() in (
        "1", "true", "yes"
    )

    # Trade any liquid US stock under this price (not penny-only)
    PENNY_STOCK_MAX_PRICE: float = 500.0

    # ════════════════════════════════════════════════════════════════════
    # FULL PILOT MODE — awake AI veteran progression
    # ════════════════════════════════════════════════════════════════════
    PILOT_MODE_ENABLED: bool = True
    COGNITIVE_MODE_ENABLED: bool = True
    USE_LIVE_IB_SCANNER: bool = True
    USE_STATIC_SCAN_FALLBACK: bool = False
    IB_SCANNER_RETRIES: int = 3
    IB_SCANNER_TIMEOUT_SEC: float = float(os.getenv("IB_SCANNER_TIMEOUT_SEC", "25"))
    USE_MULTI_TIMEFRAME_SCAN: bool = True
    SCAN_UNIVERSE_MAX: int = 30
    FAST_SCAN_ENABLED: bool = True
    FAST_SCANNER_LOCK: bool = os.getenv("FAST_SCANNER_LOCK", "true").lower() not in ("0", "false", "no")
    FAST_SCANNER_LOCK_FALLBACK: bool = os.getenv("FAST_SCANNER_LOCK_FALLBACK", "false").lower() in ("1", "true", "yes")
    SCAN_MTF_DURING_RTH: bool = os.getenv("SCAN_MTF_DURING_RTH", "false").lower() in ("1", "true", "yes")
    SCAN_PREFETCH_LOCK_N: int = int(os.getenv("SCAN_PREFETCH_LOCK_N", "5"))
    SCAN_BAR_PREFETCH_PER_LOOP: int = int(os.getenv("SCAN_BAR_PREFETCH_PER_LOOP", "12"))
    SCAN_BAR_DURATION: str = "1800 S"       # 30min bars for fast scan (not full day)
    SCAN_REFINE_TOP_N: int = 12             # MTF/AI refine only top N after fast pass
    SCAN_EARLY_EXIT_QUALIFIED: int = 18     # Stop scanning once this many qualify
    POSITION_LOOP_SEC: float = 0.25           # 250ms loop when in position
    POSITION_LOOP_IN_PROFIT_SEC: float = float(os.getenv("POSITION_LOOP_IN_PROFIT_SEC", "0.1"))
    FLAT_LOOP_SEC: float = 0.25               # Same speed when flat — don't sleep 1s between spike checks
    FLAT_LOOP_LOCKED_SEC: float = float(os.getenv("FLAT_LOOP_LOCKED_SEC", "0.1"))
    TICK_SPIKE_MONITOR: bool = os.getenv("TICK_SPIKE_MONITOR", "true").lower() in (
        "1", "true", "yes"
    )
    TICK_SPIKE_DEBOUNCE_SEC: float = float(os.getenv("TICK_SPIKE_DEBOUNCE_SEC", "0.08"))
    AI_EXIT_CHECK_IN_PROFIT_SEC: float = float(os.getenv("AI_EXIT_CHECK_IN_PROFIT_SEC", "1.0"))
    AI_SPIKE_COOLDOWN_FAST_SEC: float = float(os.getenv("AI_SPIKE_COOLDOWN_FAST_SEC", "6"))
    ENTRY_PENDING_BLOCK_FAST_SEC: float = float(os.getenv("ENTRY_PENDING_BLOCK_FAST_SEC", "12"))
    BACKGROUND_WATCH_SEC: float = float(os.getenv("BACKGROUND_WATCH_SEC", "15"))
    FLAT_PULSE_SEC: float = 15.0              # WATCHING heartbeat log interval (not the monitor rate)
    AI_POSITION_MANAGE_SEC: float = 10.0    # Ollama position decisions — frequent, priority path
    AI_POSITION_MANAGE_IN_PROFIT_SEC: float = float(
        os.getenv("AI_POSITION_MANAGE_IN_PROFIT_SEC", "2.0")
    )
    IN_PROFIT_MANAGE_PNL_PCT: float = float(
        os.getenv("IN_PROFIT_MANAGE_PNL_PCT", "0.003")
    )  # 0.3% — faster council + trail when green
    AI_EXIT_CHECK_SEC: float = 5.0          # AI early-exit evaluation interval in position loop
    AI_ALWAYS_ACTIVE: bool = True           # Never defer trading decisions to PPO-only fallbacks
    OLLAMA_DECISION_BYPASS_RATE_LIMIT: bool = True  # Entry/exit/position calls skip interval gate
    OLLAMA_DECISION_MIN_FREE_RAM_MB: int = int(os.getenv("OLLAMA_DECISION_MIN_FREE_RAM_MB", "768"))
    # Hybrid distillation — Qwen teacher → fast PPO proxy (auto when enough trades)
    HYBRID_DISTILLATION_ENABLED: bool = True
    HYBRID_DISTILL_MIN_TRADES: int = int(os.getenv("HYBRID_DISTILL_MIN_TRADES", "100"))
    HYBRID_DISTILL_FULL_TRADES: int = int(os.getenv("HYBRID_DISTILL_FULL_TRADES", "500"))
    HYBRID_DISTILL_MIN_SAMPLES: int = 30    # Paired decision+feature rows to train proxy
    HYBRID_DISTILL_MIN_ACCURACY: float = 0.62
    HYBRID_DISTILL_ENTER_THRESHOLD: float = 0.45
    HYBRID_DISTILL_AUTO_FAST_PATH: bool = os.getenv(
        "HYBRID_DISTILL_AUTO_FAST_PATH", "true"
    ).lower() in ("1", "true", "yes")
    HYBRID_DISTILL_FAST_PATH: bool = False       # Manual override — skip Ollama on entry
    HYBRID_DISTILL_CHECK_EVERY_N_TRADES: int = 5
    HYBRID_DISTILL_RETRAIN_HOURS: float = 24.0
    # Live AI hotline — Ollama always on, never blocks IB loop, no stale cache
    LIVE_AI_PIPELINE_ENABLED: bool = True
    LIVE_AI_MAX_AGE_SEC: float = float(os.getenv(
        "LIVE_AI_MAX_AGE_SEC", "6" if _LOW_RAM else "4"
    ))
    LIVE_AI_MIN_RING_SEC: float = float(os.getenv(
        "LIVE_AI_MIN_RING_SEC", "1.2" if _LOW_RAM else "0.8"
    ))
    LIVE_AI_PREFETCH_TOP_N: int = int(os.getenv(
        "LIVE_AI_PREFETCH_TOP_N", "2" if _LOW_RAM else "3"
    ))
    LIVE_AI_PREFETCH_SEC: float = float(os.getenv(
        "LIVE_AI_PREFETCH_SEC", "1.5" if _LOW_RAM else "1.0"
    ))
    # Live chart vision (llava) — off on 8GB; Telegram upload still works
    LIVE_CHART_VISION_ENABLED: bool = field(
        default_factory=lambda: os.getenv(
            "LIVE_CHART_VISION_ENABLED", "false" if _LOW_RAM else "true",
        ).lower() in ("1", "true", "yes")
    )
    LIVE_CHART_VISION_MIN_SCORE: float = float(os.getenv(
        "LIVE_CHART_VISION_MIN_SCORE", "80" if _LOW_RAM else "65"
    ))
    LIVE_CHART_VISION_MAX_AGE_SEC: float = float(os.getenv("LIVE_CHART_VISION_MAX_AGE_SEC", "12"))
    LIVE_CHART_VISION_MIN_RING_SEC: float = float(os.getenv("LIVE_CHART_VISION_MIN_RING_SEC", "2.5"))
    # Opportunistic quantized llava — high-score setups only on 8GB (RAM tier enables this)
    LIVE_CHART_VISION_OPPORTUNISTIC: bool = field(
        default_factory=lambda: os.getenv(
            "LIVE_CHART_VISION_OPPORTUNISTIC", "true" if _LOW_RAM else "false",
        ).lower() in ("1", "true", "yes")
    )
    LIVE_CHART_VISION_MIN_FREE_RAM_MB: int = int(os.getenv("LIVE_CHART_VISION_MIN_FREE_RAM_MB", "1300"))
    CHART_VISION_ENTRY_ONLY: bool = os.getenv("CHART_VISION_ENTRY_ONLY", "true").lower() in ("1", "true", "yes")
    CHART_VISION_OPPORTUNISTIC_COOLDOWN_SEC: float = float(
        os.getenv("CHART_VISION_OPPORTUNISTIC_COOLDOWN_SEC", "120")
    )
    CHART_VISION_MAX_PARALLEL: int = int(os.getenv("CHART_VISION_MAX_PARALLEL", "1"))
    SPIKE_ENTRY_ATTEMPT_COOLDOWN_SEC: float = float(
        os.getenv("SPIKE_ENTRY_ATTEMPT_COOLDOWN_SEC", "20")
    )
    OLLAMA_VISION_UNLOAD_AFTER_CALL: bool = field(
        default_factory=lambda: os.getenv(
            "OLLAMA_VISION_UNLOAD_AFTER_CALL", "true" if _LOW_RAM else "false",
        ).lower() in ("1", "true", "yes")
    )
    OLLAMA_VISION_SWAP_TEXT_MODEL: bool = field(
        default_factory=lambda: os.getenv(
            "OLLAMA_VISION_SWAP_TEXT_MODEL", "true" if _LOW_RAM else "false",
        ).lower() in ("1", "true", "yes")
    )
    ENTRY_OLLAMA_WAIT_SEC: float = float(os.getenv(
        "ENTRY_OLLAMA_WAIT_SEC", "2" if _LOW_RAM else "3"
    ))
    AI_COUNCIL_MAX_WAIT_SEC: float = float(os.getenv(
        "AI_COUNCIL_MAX_WAIT_SEC", "4" if _LOW_RAM else "4"
    ))
    # Strong scanner + spike → decide without waiting for slow Ollama
    COUNCIL_SCANNER_FAST_SEC: float = float(os.getenv(
        "COUNCIL_SCANNER_FAST_SEC", "3" if _LOW_RAM else "3"
    ))
    COUNCIL_SCANNER_FAST_MIN_SCORE: float = float(os.getenv("COUNCIL_SCANNER_FAST_MIN_SCORE", "20"))
    COUNCIL_SCANNER_FAST_MIN_SPIKE: float = float(os.getenv("COUNCIL_SCANNER_FAST_MIN_SPIKE", "1.15"))
    OFF_HOURS_HEAVY_TRAINING: bool = field(
        default_factory=lambda: os.getenv(
            "OFF_HOURS_HEAVY_TRAINING", "false" if _LOW_RAM else "true",
        ).lower() in ("1", "true", "yes")
    )
    PERIODIC_CLEANUP_SEC: float = float(os.getenv("PERIODIC_CLEANUP_SEC", "1800"))
    AI_COUNCIL_ALL_DECISIONS: bool = os.getenv(
        "AI_COUNCIL_ALL_DECISIONS", "true"
    ).lower() in ("1", "true", "yes")
    # Ollama does judgment only — Python computes all stop/TP/shares (prevents inverted stops)
    OLLAMA_NUMERIC_BRACKETS: bool = os.getenv(
        "OLLAMA_NUMERIC_BRACKETS", "false"
    ).lower() in ("1", "true", "yes")
    MAX_REWARD_RISK_RATIO: float = float(os.getenv("MAX_REWARD_RISK_RATIO", "10.0"))

    # Post-mortem / shadow circuit breaker / RL risk shaping
    SHADOW_CIRCUIT_ENABLED: bool = os.getenv(
        "SHADOW_CIRCUIT_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    SHADOW_CONSECUTIVE_LOSS_TRIGGER: int = int(os.getenv("SHADOW_CONSECUTIVE_LOSS_TRIGGER", "4"))
    SHADOW_DAILY_DD_PCT: float = float(os.getenv("SHADOW_DAILY_DD_PCT", "0.02"))
    SHADOW_REENTRY_MIN_TRADES: int = int(os.getenv("SHADOW_REENTRY_MIN_TRADES", "20"))
    SHADOW_REENTRY_MIN_WIN_RATE: float = float(os.getenv("SHADOW_REENTRY_MIN_WIN_RATE", "0.45"))
    SHADOW_REENTRY_MIN_EXPECTANCY: float = float(os.getenv("SHADOW_REENTRY_MIN_EXPECTANCY", "0.0"))
    RL_BRACKET_REJECT_PENALTY: float = float(os.getenv("RL_BRACKET_REJECT_PENALTY", "-1.0"))
    RL_LATE_SPIKE_PENALTY: float = float(os.getenv("RL_LATE_SPIKE_PENALTY", "-0.5"))
    RL_LATE_SPIKE_VOL_THRESHOLD: float = float(os.getenv("RL_LATE_SPIKE_VOL_THRESHOLD", "3.0"))

    # Architecture epoch — mood/metrics ignore pre-hybrid legacy scars
    ARCHITECTURE_VERSION: str = os.getenv("ARCHITECTURE_VERSION", "hybrid_atr_v1")
    ARCHITECTURE_EPOCH_MOOD_FILTER: bool = os.getenv(
        "ARCHITECTURE_EPOCH_MOOD_FILTER", "true"
    ).lower() in ("1", "true", "yes")
    ARCHITECTURE_EPOCH_RESET: bool = os.getenv(
        "ARCHITECTURE_EPOCH_RESET", "false"
    ).lower() in ("1", "true", "yes")
    ARCHITECTURE_EPOCH_MIN_TRADES: int = int(os.getenv("ARCHITECTURE_EPOCH_MIN_TRADES", "5"))
    REGIME_ATR_NOISE_STOP_SEC: float = float(os.getenv("REGIME_ATR_NOISE_STOP_SEC", "120.0"))

    MAX_ENTRY_FILL_SLIPPAGE_PCT: float = float(os.getenv("MAX_ENTRY_FILL_SLIPPAGE_PCT", "0.012"))
    MAX_ENTRY_FILL_SLIPPAGE_ATR: float = float(os.getenv("MAX_ENTRY_FILL_SLIPPAGE_ATR", "0.5"))
    POST_FILL_REANCHOR_ENABLED: bool = os.getenv(
        "POST_FILL_REANCHOR_ENABLED", "true"
    ).lower() in ("1", "true", "yes")

    POSITION_PULSE_SEC: float = 5.0          # Live P&L log interval (when price/P&L changes)
    POSITION_PULSE_UNCHANGED_SEC: float = 30.0  # Slower heartbeat when flat (reduces spam)
    STAGNATION_EXIT_ENABLED: bool = True
    STAGNATION_EXIT_SEC: float = 90.0      # Cut dead trades after N sec with no progress
    STAGNATION_FLAT_BAND_PCT: float = 0.008  # |P&L| within ±0.8% = no progress
    STAGNATION_MAX_PEAK_PCT: float = 0.003   # Never reached +0.3% from entry
    STAGNATION_LOSS_CUT_PCT: float = -0.005  # Exit flat losers worse than -0.5%
    AI_STAGNATION_CHECK_SEC: float = 30.0  # Ollama+PPO review after N sec flat
    STALE_PRICE_REFRESH_PULSES: int = 4    # Force IB snapshot after N identical pulses
    STALE_PRICE_REFRESH_SEC: float = 20.0  # Or force snapshot if price frozen this long
    VOLATILITY_STOP_WIDEN_MAX_PCT: float = 0.025  # Unrealized noise cushion (not below $50 risk)
    INCREMENTAL_TRAINING_ENABLED: bool = os.getenv(
        "INCREMENTAL_TRAINING_ENABLED", "false"
    ).lower() in ("1", "true", "yes")
    INCREMENTAL_TRAIN_EVERY_N_TRADES: int = 3
    INCREMENTAL_TRAIN_MIN_NEW_RECORDS: int = 2
    DYNAMIC_AI_NOTIFICATIONS: bool = True
    # Async fill tracking only — never blocks entries/exits/scans. IB execDetails → instant
    # cache lookup each loop tick; fallback quote P&L for notify/learn if IB silent this long.
    FILL_RECONCILE_FALLBACK_SEC: float = float(os.getenv("FILL_RECONCILE_FALLBACK_SEC", "8"))
    AI_TELEGRAM_NOTIFICATIONS: bool = True   # Ollama crafts Telegram text
    AI_TELEGRAM_ALL_OUTBOUND: bool = os.getenv("TRADING_BOT_AI_TELEGRAM_ALL", "true").lower() in ("1", "true", "yes")
    AI_TELEGRAM_MIN_INTERVAL_SEC: float = 6.0  # Throttle duplicate event types
    AI_TELEGRAM_MAX_CHARS: int = 450
    AI_TELEGRAM_COMMANDER_MAX_CHARS: int = int(os.getenv("TRADING_BOT_TELEGRAM_AI_CHARS", "3800"))
    AI_TELEGRAM_OLLAMA_MAX_TOKENS: int = 120   # Short pilot briefings
    AI_TELEGRAM_OLLAMA_TIMEOUT: int = 12       # Seconds — notifications must not stall loop
    OLLAMA_NOTIFY_MIN_FREE_RAM_MB: int = 512   # Lighter gate for Telegram compose (model often warm)
    AI_ACCOUNT_EVALUATION: bool = True         # AI account brief on market open/close
    AI_DAILY_SELF_EVALUATION: bool = os.getenv(
        "AI_DAILY_SELF_EVALUATION", "true"
    ).lower() in ("1", "true", "yes")
    AI_DAILY_SELF_EVAL_MAX_CHARS: int = int(os.getenv("AI_DAILY_SELF_EVAL_MAX_CHARS", "3800"))
    AI_ACCOUNT_EVAL_ON_STARTUP: bool = os.getenv(
        "AI_ACCOUNT_EVAL_ON_STARTUP", "false"
    ).lower() in ("1", "true", "yes")
    AI_ACCOUNT_EVAL_MIN_SEC: float = 300.0     # Min gap between same event type
    LEARNING_RESTORE_ON_STARTUP: bool = True   # Pull experience from GitHub on boot
    LEARNING_SYNC_INTERVAL_SEC: float = 1800.0 # Push all learning artifacts every 30 min
    LEARNING_PUSH_ON_TRADE: bool = os.getenv(
        "LEARNING_PUSH_ON_TRADE", "true"
    ).lower() in ("1", "true", "yes")
    # HANOON session: defer git pushes (shutdown hook still syncs). Standalone daemon handles live pushes.
    GIT_PUSH_DURING_SESSION: bool = os.getenv(
        "GIT_PUSH_DURING_SESSION", "false"
    ).lower() in ("1", "true", "yes")
    GIT_AUTO_WATCH_IN_BOT: bool = False        # Never watch from HANOON — use start_git_sync.sh
    GIT_AUTO_WATCH_ENABLED: bool = os.getenv(
        "GIT_AUTO_WATCH", "true"
    ).lower() in ("1", "true", "yes")
    GIT_AUTO_PUSH_INTERVAL_SEC: float = float(os.getenv("GIT_AUTO_PUSH_INTERVAL_SEC", "8"))
    GIT_PUSH_ALL_CHANGES: bool = os.getenv(
        "GIT_PUSH_ALL_CHANGES", "true"
    ).lower() in ("1", "true", "yes")
    GIT_AUTO_PUSH_MAX_FILES: int = int(os.getenv("GIT_AUTO_PUSH_MAX_FILES", "80"))
    ENV_SYNC_ENABLED: bool = os.getenv("ENV_SYNC_ENABLED", "true").lower() in ("1", "true", "yes")
    ENV_SYNC_PUSH_KEY: bool = os.getenv("ENV_SYNC_PUSH_KEY", "true").lower() in ("1", "true", "yes")
    TELEGRAM_ASYNC_DURING_SESSION: bool = True # Ollama Telegram off hot path
    GENERATIVE_THINKING_ENABLED: bool = True
    # AI learns from failures instead of permanent blacklists / rigid gates
    AI_LEARN_DONT_BLOCK: bool = os.getenv(
        "AI_LEARN_DONT_BLOCK", "true"
    ).lower() in ("1", "true", "yes")
    AI_FAILURE_SOFT_COOLDOWN_SEC: float = float(
        os.getenv("AI_FAILURE_SOFT_COOLDOWN_SEC", "30")
    )
    AI_FAILURE_HARD_COOLDOWN_SEC: float = float(
        os.getenv("AI_FAILURE_HARD_COOLDOWN_SEC", "3600")
    )
    # Loss streak → learn + self-correct instead of long blind cool-off (learn mode)
    AI_LEARN_ON_LOSS_STREAK: bool = os.getenv(
        "AI_LEARN_ON_LOSS_STREAK", "true"
    ).lower() in ("1", "true", "yes")
    LOSS_STREAK_LEARNING_MIN_SEC: float = float(
        os.getenv("LOSS_STREAK_LEARNING_MIN_SEC", "45")
    )
    LOSS_STREAK_LEARNING_MAX_SEC: float = float(
        os.getenv("LOSS_STREAK_LEARNING_MAX_SEC", "300")
    )
    LOSS_STREAK_RESUME_CONFIDENCE: float = float(
        os.getenv("LOSS_STREAK_RESUME_CONFIDENCE", "0.52")
    )
    AI_STATIC_FALLBACK: bool = False      # No rule-based bypass when full control
    AI_HUMAN_COGNITION: bool = True       # Human-like reasoning + gut feel on all decisions
    AI_USE_COMPUTATIONAL_REASONING: bool = True  # Synthesize PPO, scanner, MTF, volume in every call
    
    # USER RULE: Volume spike threshold (1.5x = 50% above average)
    VOLUME_SPIKE_MIN_RATIO: float = 1.25
    LOCKED_SPIKE_MIN_RATIO: float = 1.15      # Slightly easier spike on committed lock targets
    
    # High frequency scanning
    SCAN_INTERVAL_SECONDS: int = 30  # Scan every 30s when positions open
    MAX_LOCKED_TARGETS: int = 5  # Always return 1-5 stocks
    MIN_LOCK_SCORE: float = 30.0          # Min MTF+AI score to earn a lock slot
    MIN_LOCK_CANDIDATES: int = 2          # Need at least N quality names before locking
    LOCK_STALE_RELEASE_SEC: float = float(os.getenv("LOCK_STALE_RELEASE_SEC", "600"))
    LOCK_FOCUS_ROTATE_SEC: float = float(os.getenv("LOCK_FOCUS_ROTATE_SEC", "0"))
    LOCK_BAR_WARM_BUDGET_SEC: float = float(os.getenv("LOCK_BAR_WARM_BUDGET_SEC", "5"))
    ENTRY_PENDING_BLOCK_SEC: float = float(os.getenv("ENTRY_PENDING_BLOCK_SEC", "45"))
    WATCH_ALL_LOCKED_STREAMS: bool = os.getenv(
        "WATCH_ALL_LOCKED_STREAMS", "true"
    ).lower() in ("1", "true", "yes")
    PARALLEL_ENTRY_EXIT: bool = os.getenv("PARALLEL_ENTRY_EXIT", "true").lower() in (
        "1", "true", "yes"
    )
    HOT_SWAP_ON_EXIT: bool = os.getenv("HOT_SWAP_ON_EXIT", "true").lower() in (
        "1", "true", "yes"
    )
    FOCUS_PIN_TOP_PICK: bool = os.getenv("FOCUS_PIN_TOP_PICK", "false").lower() in ("1", "true", "yes")
    
    SCALP_STOP_ATR_MULTIPLIER: float = 0.9
    SCALP_MIN_STOP_PCT: float = 0.004
    SCALP_MAX_STOP_PCT: float = 0.015
    SCALP_TP_ATR_MULTIPLIER: float = 1.5
    SCALP_MIN_RR: float = 1.5
    SCALP_TRAILING_ACTIVATE_PCT: float = 0.002
    SCALP_TRAILING_ATR_MULTIPLIER: float = 0.6
    SCALP_PROFIT_ACTIVATE_PCT: float = 0.005
    SCALP_PROFIT_GIVEBACK_PCT: float = 0.30
    MIN_POSITION_HOLD_SEC: float = 45.0   # Block early-exit rules for first N seconds
    MIN_ENTRY_FILL_RATIO: float = 0.85    # Reject partial fills below 85% of order size
    ENTRY_FILL_WAIT_SEC: float = float(os.getenv("ENTRY_FILL_WAIT_SEC", "0.25"))
    ENTRY_FILL_MAX_WAIT_SEC: float = float(os.getenv("ENTRY_FILL_MAX_WAIT_SEC", "20"))
    ENTRY_FAILURE_COOLDOWN_SEC: float = 30.0
    SPIKE_SKIP_SEC: float = 30.0
    PENNY_PRICE_THRESHOLD: float = 1.0    # Sub-$1 = penny liquidity rules
    PENNY_USE_MARKET_ENTRY: bool = False  # MARKET on SCM tickers → IB error 2161, no fill
    PENNY_MAX_DEPLOY_USD: float = 350.0   # Cap deploy on sub-$1 names
    PENNY_MAX_SHARES: int = 1200          # IB disruptive-order protection kicks in above this
    PENNY_LIMIT_BUFFER_PCT: float = 0.006 # Marketable limit buffer above ask/last
    IB_REGULATORY_LIMIT_PCT: float = 0.01
    MAX_ENTRY_SPREAD_PCT: float = 0.05       # Skip entries when bid/ask gap too wide
    MAX_MARKET_ENTRY_SHARES: int = 400
    LIQUIDITY_MAX_VOL_PCT: float = 0.08   # Max order as fraction of recent bar volume
    ENTRY_RETRY_ON_IB2161: bool = True
    ENTRY_LIMIT_BUFFER_PCT: float = 0.003 # Marketable limit buffer above live price (non-penny)
    # Silent opportunity scan while holding a position (see BACKGROUND_WATCH_SEC above)

    # ════════════════════════════════════════════════════════════════════
    # OLLAMA LOCAL LLM — 2.5GB reserved budget on 8GB Mac (warm, frequent calls)
    # ════════════════════════════════════════════════════════════════════
    OLLAMA_ENABLED: bool = field(
        default_factory=lambda: os.getenv("OLLAMA_ENABLED", "true").lower() in ("1", "true", "yes")
    )
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MEMORY_BUDGET_MB: int = int(os.getenv(
        "OLLAMA_MEMORY_BUDGET_MB", "2048" if _8GB_RAM else ("2560" if _LOW_RAM else "3072")
    ))
    OLLAMA_MODEL: str = os.getenv(
        "OLLAMA_MODEL",
        "qwen2.5:3b" if _LOW_RAM else "llama3",
    )
    OLLAMA_DYNAMIC_MODEL: bool = os.getenv(
        "OLLAMA_DYNAMIC_MODEL", "true" if _LOW_RAM else "false"
    ).lower() in ("1", "true", "yes")
    OLLAMA_PRESSURE_FREE_MB: int = int(os.getenv("OLLAMA_PRESSURE_FREE_MB", "1800"))
    OLLAMA_SEVERE_PRESSURE_FREE_MB: int = int(os.getenv("OLLAMA_SEVERE_PRESSURE_FREE_MB", "1200"))
    OLLAMA_OS_RESERVE_MB: int = int(os.getenv("OLLAMA_OS_RESERVE_MB", "1500"))
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "12" if _LOW_RAM else "20"))
    OLLAMA_MAX_TOKENS: int = int(os.getenv("OLLAMA_MAX_TOKENS", "192" if _LOW_RAM else "384"))
    OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))
    OLLAMA_KEEP_ALIVE: int = int(os.getenv("OLLAMA_KEEP_ALIVE", "300" if _LOW_RAM else "600"))
    OLLAMA_NUM_CTX: int = int(os.getenv("OLLAMA_NUM_CTX", "1536" if _LOW_RAM else "4096"))
    OLLAMA_MIN_CALL_INTERVAL_SEC: float = float(os.getenv("OLLAMA_MIN_CALL_INTERVAL_SEC", "1"))
    OLLAMA_UNLOAD_AFTER_CALL: bool = os.getenv("OLLAMA_UNLOAD_AFTER_CALL", "false").lower() in ("1", "true", "yes")
    OLLAMA_MIN_FREE_RAM_MB: int = int(os.getenv("OLLAMA_MIN_FREE_RAM_MB", "1024"))

    # ════════════════════════════════════════════════════════════════════
    # MULTI-REPO GIT ARCHITECTURE (HANOON / Grandmaster / Logs)
    # ════════════════════════════════════════════════════════════════════
    GITHUB_HANOON_REPO: str = os.getenv(
        "GITHUB_HANOON_REPO",
        os.getenv("GITHUB_HA_NUN_REPO", "sajibmdsaberahmad-create/trading-bot-HA-NUN"),
    )
    GITHUB_GRANDMASTER_REPO: str = os.getenv("GITHUB_GRANDMASTER_REPO", "sajibmdsaberahmad-create/trading-bot-Grandmaster")
    GITHUB_LOGS_REPO: str = os.getenv("GITHUB_LOGS_REPO", "sajibmdsaberahmad-create/trading-bot-Logs")
    GITHUB_ALGO_REPO: str = os.getenv("GITHUB_ALGO_REPO", "sajibmdsaberahmad-create/Algo")
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
    TRAINING_MEMORY_LIMIT_MB: int = 2048 if _LOW_RAM else 4096
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
    TELEGRAM_BOT_TOKEN: str = (
        os.getenv("TRADING_BOT_TELEGRAM_TOKEN", "")
        or os.getenv("TELEGRAM_BOT_TOKEN", "")
    ).strip().strip("'\"")
    # Optional legacy default chat — outbound uses verified chats by default
    TELEGRAM_CHAT_ID: str = (
        os.getenv("TRADING_BOT_TELEGRAM_CHAT_ID", "")
        or os.getenv("TELEGRAM_CHAT_ID", "")
    ).strip().strip("'\"")
    TELEGRAM_AUTO_VERIFY_PRIMARY: bool = os.getenv(
        "TRADING_BOT_TELEGRAM_AUTO_VERIFY_PRIMARY", "true"
    ).lower() in ("1", "true", "yes")
    TELEGRAM_VERIFIED_ONLY_OUTBOUND: bool = os.getenv(
        "TRADING_BOT_TELEGRAM_VERIFIED_ONLY", "true"
    ).lower() in ("1", "true", "yes")

    # Inbound Telegram copilot (two-way chat, verify-any-account)
    TELEGRAM_LISTEN_ENABLED: bool = os.getenv(
        "TRADING_BOT_TELEGRAM_LISTEN", "true"
    ).lower() in ("1", "true", "yes")
    TELEGRAM_VERIFY_SECRET: str = os.getenv("TRADING_BOT_TELEGRAM_VERIFY_SECRET", "hall of fame")
    TELEGRAM_POLL_INTERVAL_SEC: float = float(os.getenv("TRADING_BOT_TELEGRAM_POLL_SEC", "1.5"))
    TELEGRAM_VERIFIED_STORE: str = "models/telegram_verified.json"
    TELEGRAM_COMMANDER_GUIDANCE: str = "models/commander_guidance.jsonl"
    TELEGRAM_DAILY_REPORT_MAX_CHARS: int = int(os.getenv("TRADING_BOT_TELEGRAM_REPORT_CHARS", "3800"))
    TELEGRAM_BROADCAST_OPS: bool = os.getenv("TRADING_BOT_TELEGRAM_BROADCAST_OPS", "true").lower() in ("1", "true", "yes")
    TELEGRAM_BROADCAST_GIT: bool = os.getenv("TRADING_BOT_TELEGRAM_BROADCAST_GIT", "false").lower() in ("1", "true", "yes")
    GIT_NOTIFY_MODE: str = os.getenv("GIT_NOTIFY_MODE", "off")  # off | log | session | failures | all
    TELEGRAM_BROADCAST_LEARNING: bool = os.getenv(
        "TRADING_BOT_TELEGRAM_BROADCAST_LEARNING", "false"
    ).lower() in ("1", "true", "yes")

    # Real-time AI self-correction (5W reasoning on every algo event)
    AI_RUNTIME_OBSERVER_ENABLED: bool = os.getenv(
        "AI_RUNTIME_OBSERVER_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    AI_RUNTIME_REASONING_ENABLED: bool = os.getenv(
        "AI_RUNTIME_REASONING_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    AI_RUNTIME_AUTO_APPLY: bool = os.getenv(
        "AI_RUNTIME_AUTO_APPLY", "true"
    ).lower() in ("1", "true", "yes")
    AI_RUNTIME_EVENT_MIN_SEC: float = float(os.getenv("AI_RUNTIME_EVENT_MIN_SEC", "25"))
    OFF_HOURS_TRAIN_INTERVAL_SEC: float = float(os.getenv("OFF_HOURS_TRAIN_INTERVAL_SEC", "3600"))

    # Commander chat → self-improvement loop
    COMMANDER_LEARNING_ENABLED: bool = os.getenv(
        "COMMANDER_LEARNING_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    COMMANDER_AUTO_APPLY_FROM_CHAT: bool = os.getenv(
        "COMMANDER_AUTO_APPLY_FROM_CHAT", "true"
    ).lower() in ("1", "true", "yes")
    COMMANDER_AUTO_APPLY_MIN_SEC: float = float(os.getenv("COMMANDER_AUTO_APPLY_MIN_SEC", "90"))

    # Generative mood (free-form, not fixed labels)
    GENERATIVE_MOOD_ENABLED: bool = os.getenv(
        "GENERATIVE_MOOD_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    GENERATIVE_MOOD_MIN_SEC: float = float(os.getenv("GENERATIVE_MOOD_MIN_SEC", "45"))

    # Vision — quantized llava per RAM tier (see core/ollama_vision.py)
    OLLAMA_VISION_MODEL: str = os.getenv("OLLAMA_VISION_MODEL", "llava-phi3:3.8b" if _LOW_RAM else "llava")
    OLLAMA_VISION_TIMEOUT: int = int(os.getenv("OLLAMA_VISION_TIMEOUT", "30" if _LOW_RAM else "45"))
    OLLAMA_VISION_MAX_TOKENS: int = int(os.getenv("OLLAMA_VISION_MAX_TOKENS", "160" if _LOW_RAM else "512"))

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
    LOG_PATH:      str = "HANOON.log"
    PERF_PATH:     str = "performance.csv"
    STATE_PATH:    str = "bot_state.json"
    AUDIT_PATH:    str = "audit_trail.jsonl"

    def risk_amount_usd(self, account_equity: float) -> float:
        pct_based = account_equity * self.RISK_PER_TRADE_PCT
        if getattr(self, "PAPER_TRADING", False) and getattr(self, "AI_PAPER_FREE_LEARNING", True):
            cap = float(getattr(self, "MAX_RISK_PER_TRADE_USD", 250_000))
            return min(pct_based, cap) if cap > 0 else pct_based
        return min(pct_based, self.MAX_RISK_PER_TRADE_USD)

    # ════════════════════════════════════════════════════════════════════
    # OLLAMA META-OPTIMIZER (Active File Mutation)
    # ════════════════════════════════════════════════════════════════════
    OLLAMA_META_OPTIMIZER_ENABLED: bool = (
        os.getenv("OLLAMA_META_OPTIMIZER_ENABLED", "false" if _LOW_RAM else "true").lower()
        in ("1", "true", "yes")
    )
    META_OPTIMIZE_ONLY_WHEN_MARKET_CLOSED: bool = True
    MAX_PARAM_MUTATIONS_PER_DAY: int = 5
    META_OPTIMIZER_MODEL: str = os.getenv(
        "META_OPTIMIZER_MODEL",
        os.getenv("OLLAMA_MODEL", "qwen2.5:3b" if _LOW_RAM else "llama3"),
    )

    # RAM auto-tune — upgrades features when more physical RAM is detected
    RAM_AUTO_TUNE: bool = field(
        default_factory=lambda: os.getenv("RAM_AUTO_TUNE", "true").lower() in ("1", "true", "yes")
    )
    RAM_TIER: str = "auto"
    RAM_TIER_LABEL: str = ""
    RAM_TIER_FORCE: str = os.getenv("RAM_TIER_FORCE", "")

    def __post_init__(self) -> None:
        from core.ram_tier import apply_ram_tier_to_config
        apply_ram_tier_to_config(self)
