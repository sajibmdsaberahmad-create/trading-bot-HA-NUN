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
    MAX_RISK_PER_TRADE_USD: float = 75.0

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
    MIN_REWARD_RISK_RATIO:      float = 2.0

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
    # PRE-MARKET / EXTENDED HOURS
    # ════════════════════════════════════════════════════════════════════
    ALLOW_PRE_MARKET_TRADING:  bool = True   # Trade before 9:30 ET if high confidence
    ALLOW_AFTER_HOURS_TRADING: bool = False  # Trade after 4:00 ET
    PRE_MARKET_START:          str  = "04:00"  # ET
    PRE_MARKET_END:            str  = "09:25"   # ET
    AFTER_HOURS_START:         str  = "16:00"   # ET
    AFTER_HOURS_END:           str  = "20:00"   # ET
    MIN_CONFIDENCE_PRE_MARKET: float = 0.70    # Min fusion confidence to trade pre-market

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
    
    # USER RULE: Deploy exactly $1,000 per stock (veterans scale via pilot_mode)
    DEPLOY_PER_STOCK_USD: float = 1000.0
    PILOT_MAX_DEPLOY_USD: float = 2000.0

    # USER RULE: Hard stop $50 per trade (cannot be overridden)
    HARD_STOP_USD: float = 50.0

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
    USE_MULTI_TIMEFRAME_SCAN: bool = True
    SCAN_UNIVERSE_MAX: int = 30
    FAST_SCAN_ENABLED: bool = True
    SCAN_BAR_DURATION: str = "1800 S"       # 30min bars for fast scan (not full day)
    SCAN_REFINE_TOP_N: int = 12             # MTF/AI refine only top N after fast pass
    SCAN_EARLY_EXIT_QUALIFIED: int = 18     # Stop scanning once this many qualify
    POSITION_LOOP_SEC: float = 0.25           # 250ms loop when in position
    FLAT_LOOP_SEC: float = 0.25               # Same speed when flat — don't sleep 1s between spike checks
    FLAT_PULSE_SEC: float = 15.0              # WATCHING heartbeat log interval (not the monitor rate)
    AI_POSITION_MANAGE_SEC: float = 10.0    # Ollama position decisions — frequent, priority path
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
    HYBRID_DISTILL_AUTO_FAST_PATH: bool = True   # Enable fast path when proxy + trade count ready
    HYBRID_DISTILL_FAST_PATH: bool = False       # Manual override — skip Ollama on entry
    HYBRID_DISTILL_CHECK_EVERY_N_TRADES: int = 5
    HYBRID_DISTILL_RETRAIN_HOURS: float = 24.0
    # Live AI hotline — Ollama always on, never blocks IB loop, no stale cache
    LIVE_AI_PIPELINE_ENABLED: bool = True
    LIVE_AI_MAX_AGE_SEC: float = 4.0       # Discard Ollama answers older than this
    LIVE_AI_MIN_RING_SEC: float = 0.8      # Min gap between identical fingerprint rings
    LIVE_AI_PREFETCH_TOP_N: int = 3        # Keep hotline open on top locked tickers
    LIVE_AI_PREFETCH_SEC: float = 1.0      # How often to prefetch watchlist
    POSITION_PULSE_SEC: float = 5.0          # Live P&L log interval
    VOLATILITY_STOP_WIDEN_MAX_PCT: float = 0.025  # Unrealized noise cushion (not below $50 risk)
    INCREMENTAL_TRAINING_ENABLED: bool = True
    INCREMENTAL_TRAIN_EVERY_N_TRADES: int = 3
    INCREMENTAL_TRAIN_MIN_NEW_RECORDS: int = 2
    DYNAMIC_AI_NOTIFICATIONS: bool = True
    AI_TELEGRAM_NOTIFICATIONS: bool = True   # Ollama crafts Telegram text
    AI_TELEGRAM_MIN_INTERVAL_SEC: float = 6.0  # Throttle duplicate event types
    AI_TELEGRAM_MAX_CHARS: int = 450
    AI_TELEGRAM_OLLAMA_MAX_TOKENS: int = 120   # Short pilot briefings
    AI_TELEGRAM_OLLAMA_TIMEOUT: int = 12       # Seconds — notifications must not stall loop
    OLLAMA_NOTIFY_MIN_FREE_RAM_MB: int = 512   # Lighter gate for Telegram compose (model often warm)
    AI_ACCOUNT_EVALUATION: bool = True         # AI account brief on market open/close
    AI_ACCOUNT_EVAL_ON_STARTUP: bool = True    # Compare to last close on bot start
    AI_ACCOUNT_EVAL_MIN_SEC: float = 300.0     # Min gap between same event type
    LEARNING_RESTORE_ON_STARTUP: bool = True   # Pull experience from GitHub on boot
    LEARNING_SYNC_INTERVAL_SEC: float = 1800.0 # Push all learning artifacts every 30 min
    LEARNING_PUSH_ON_TRADE: bool = True        # Push experience after each trade close
    GENERATIVE_THINKING_ENABLED: bool = True
    AI_FULL_CONTROL: bool = True          # AI owns all decisions, logs, journals, notifications
    AI_STATIC_FALLBACK: bool = False      # No rule-based bypass when full control
    AI_HUMAN_COGNITION: bool = True       # Human-like reasoning + gut feel on all decisions
    AI_USE_COMPUTATIONAL_REASONING: bool = True  # Synthesize PPO, scanner, MTF, volume in every call
    
    # USER RULE: Max 5 concurrent positions
    MAX_CONCURRENT_POSITIONS: int = 5
    
    # USER RULE: Volume spike threshold (1.5x = 50% above average)
    VOLUME_SPIKE_MIN_RATIO: float = 1.25
    LOCKED_SPIKE_MIN_RATIO: float = 1.15      # Slightly easier spike on committed lock targets
    
    # High frequency scanning
    SCAN_INTERVAL_SECONDS: int = 30  # Scan every 30s when positions open
    MAX_LOCKED_TARGETS: int = 5  # Always return 1-5 stocks
    MIN_LOCK_SCORE: float = 30.0          # Min MTF+AI score to earn a lock slot
    MIN_LOCK_CANDIDATES: int = 2          # Need at least N quality names before locking
    LOCK_BAR_REFRESH_SEC: float = 180.0   # Refresh locked bars every 3 min (not 60s)
    FOCUS_PIN_TOP_PICK: bool = True       # No tick-stream rotation — stay on #1 pick
    
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
    ENTRY_FILL_WAIT_SEC: float = 1.0      # Seconds per fill poll iteration
    ENTRY_FILL_MAX_WAIT_SEC: float = 30.0 # Max wait for IB parent fill
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
    BACKGROUND_WATCH_SEC: float = 45.0    # Silent opportunity scan while holding a position

    # ════════════════════════════════════════════════════════════════════
    # OLLAMA LOCAL LLM — 2.5GB reserved budget on 8GB Mac (warm, frequent calls)
    # ════════════════════════════════════════════════════════════════════
    OLLAMA_ENABLED: bool = field(
        default_factory=lambda: os.getenv("OLLAMA_ENABLED", "true").lower() in ("1", "true", "yes")
    )
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MEMORY_BUDGET_MB: int = int(os.getenv("OLLAMA_MEMORY_BUDGET_MB", "2560"))
    OLLAMA_MODEL: str = os.getenv(
        "OLLAMA_MODEL",
        "qwen2.5:3b" if _LOW_RAM else "llama3",
    )
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "20"))
    OLLAMA_MAX_TOKENS: int = int(os.getenv("OLLAMA_MAX_TOKENS", "256" if _LOW_RAM else "384"))
    OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))
    OLLAMA_KEEP_ALIVE: int = int(os.getenv("OLLAMA_KEEP_ALIVE", "600"))  # keep warm 10 min
    OLLAMA_NUM_CTX: int = int(os.getenv("OLLAMA_NUM_CTX", "2048" if _LOW_RAM else "4096"))
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
    LOG_PATH:      str = "HANOON.log"
    PERF_PATH:     str = "performance.csv"
    STATE_PATH:    str = "bot_state.json"
    AUDIT_PATH:    str = "audit_trail.jsonl"

    def risk_amount_usd(self, account_equity: float) -> float:
        pct_based = account_equity * self.RISK_PER_TRADE_PCT
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
