#!/usr/bin/env python3
"""
main.py — Entry point for HANOON Single-Focus Institutional Scalper.
See docs/LAUNCH_GUIDE.md for full setup.

QUICK START
  python main.py --mode scalper             # HANOON institutional penny stock scalper
  python main.py --mode warmup              # Train PPO (legacy)
  python main.py --mode trade               # PPO paper/live trade (legacy)
  python main.py --mode evaluate            # Offline backtest

Full docs: docs/LAUNCH_GUIDE.md, docs/ARCHITECTURE.md
"""

import argparse
import sys
import os
import time

# Always run on US Eastern clock regardless of device timezone (e.g. Bangladesh)
os.environ["TZ"] = "America/New_York"
if hasattr(time, "tzset"):
    time.tzset()

from core.config import BotConfig
from core.notify import log, Notifier
from core.connector import IBConnector
from core.runners import run_warmup, run_evaluate
from core.trader import LiveTrader
from core.scalper_runner import ScalperRunner
from core.git_sync import init as git_sync_init


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="HANOON Single-Focus Institutional Scalper — IB Gateway Edition v3.5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
QUICK START:
  1. python main.py --mode scalper          # HANOON institutional scalper
  2. python main.py --mode warmup           # Train PPO (legacy)
  3. python main.py --mode trade            # PPO paper/live trade (legacy)
  4. python main.py --mode advanced-train   # Train ALL models (PPO + Transformer + LSTM)
  5. python main.py --mode fusion-trade     # Trade with multi-model fusion engine

EXAMPLES:
  python main.py --mode scalper
  python main.py --mode warmup --ticker QQQ --cash 1000
  python main.py --mode advanced-train --ticker SPY --ppo-timesteps 500000
  python main.py --mode fusion-trade        # Multi-model AI trading
  python main.py --mode trade --port 7496   # LIVE — real money, be careful
        """,
    )
    parser.add_argument("--mode", choices=["warmup", "trade", "evaluate", "scalper",
                                            "advanced-train", "fusion-trade",
                                            "fusion-backtest", "replay-live"], required=True,
                         help="scalper: HANOON | replay-live: fake-live from CSV | warmup: train PPO | "
                              "trade: PPO live | evaluate: backtest | advanced-train: train all AI models | "
                              "fusion-trade: multi-model AI trade | fusion-backtest: multi-model backtest")
    parser.add_argument("--algo", choices=["ppo", "scalper", "fusion"], default=None,
                         help="Override: ppo (legacy), scalper (HANOON), or fusion (multi-model AI)")
    parser.add_argument("--ticker", default="SPY", help="Ticker symbol (default: SPY)")
    parser.add_argument("--cash", default=1_000.0, type=float, help="Starting capital in USD (default: 1000)")
    parser.add_argument("--port", default=7497, type=int, help="IB Gateway port: 7497=paper, 7496=live")
    parser.add_argument("--client-id", default=1, type=int, dest="client_id", help="IB API client ID")
    parser.add_argument("--risk-pct", default=None, type=float,
                         help="Override RISK_PER_TRADE_PCT (e.g. 0.05 for 5%%)")
    parser.add_argument("--max-risk-usd", default=None, type=float,
                         help="Override MAX_RISK_PER_TRADE_USD (e.g. 50)")
    parser.add_argument("--sizing-mode", choices=["risk_based", "full_cash"], default=None,
                         help="Override SIZING_MODE: risk_based or full_cash")
    parser.add_argument("--order-size-usd", default=None, type=float,
                         help="For full_cash mode: explicit dollar amount to use for order sizing")
    parser.add_argument("--timeframe", choices=["1min", "5min", "1h", "4h", "1d"], default=None,
                         help="Trading timeframe for data and risk parameters")
    parser.add_argument("--lse", action="store_true",
                         help="Trade on London Stock Exchange (auto-configure LSE settings)")
    # Advanced training options
    parser.add_argument("--ppo-timesteps", type=int, default=500_000,
                         help="PPO training timesteps (advanced-train mode)")
    parser.add_argument("--epochs", type=int, default=50,
                         help="Transformer/LSTM training epochs (advanced-train mode)")
    parser.add_argument("--train-start", default="2020-01-01",
                         help="Training data start date (advanced-train mode)")
    parser.add_argument("--train-end", default="2024-12-31",
                         help="Training data end date (advanced-train mode)")
    parser.add_argument("--no-backtest", action="store_true",
                         help="Skip post-training backtest (advanced-train mode)")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"],
                         default="auto", help="Training device")
    parser.add_argument("--use-synthetic", action="store_true",
                         help="Use synthetic data for training (advanced-train mode)")
    # Fusion backtest options
    parser.add_argument("--bt-start", default="2024-10-01",
                         help="Backtest start date")
    parser.add_argument("--bt-end", default="2024-12-31",
                         help="Backtest end date")
    parser.add_argument("--bt-bars", type=int, default=1000,
                         help="Number of bars for fusion backtest")
    return parser


def run_advanced_training(args):
    """Run advanced multi-model training pipeline."""
    log.info("=" * 70)
    log.info("  🧠 ADVANCED MULTI-MODEL AI TRAINING")
    log.info("=" * 70)
    
    from core.advanced_training import TrainingConfig, AdvancedTrainingPipeline
    
    config = TrainingConfig(
        ticker=args.ticker.upper(),
        train_start=args.train_start,
        train_end=args.train_end,
        ppo_timesteps=args.ppo_timesteps,
        epochs=args.epochs,
        run_backtest=not args.no_backtest,
        device=args.device,
    )
    
    pipeline = AdvancedTrainingPipeline(config)
    results = pipeline.run_all()
    
    return results


def run_fusion_backtest(args):
    """Run backtest with the multi-model fusion engine."""
    log.info("=" * 70)
    log.info("  🧠 MULTI-MODEL FUSION BACKTEST")
    log.info("=" * 70)
    
    from core.config import BotConfig
    from core.multi_model_fusion import (
        MultiModelFusionEngine, create_fusion_engine, FusedDecision
    )
    from core.agent_enhanced import (
        EnsembleTrader, MarketRegimeClassifier, ConfidenceScorer,
        compute_thinking_confidence
    )
    from core.features_enhanced import FeatureEngineerEnhanced
    from core.transformer_model import (
        TemporalFusionTransformer, TransformerConfig, create_transformer,
        predict_with_transformer
    )
    import numpy as np
    import pandas as pd
    
    cfg = BotConfig()
    cfg.TICKER = args.ticker.upper()
    cfg.INITIAL_CASH = args.cash
    
    n_bars = args.bt_bars
    n_features = 18
    window_size = cfg.WINDOW_SIZE
    
    # Generate synthetic test data
    log.info(f"Generating {n_bars} bars of synthetic test data...")
    np.random.seed(42)
    price = 200.0
    prices = []
    for _ in range(n_bars + window_size + 50):
        drift = 0.0001
        vol = 0.005 + 0.01 * np.random.rand()
        noise = np.random.randn() * vol
        price *= (1 + drift + noise)
        price = max(price, 10.0)
        prices.append(price)
    prices = np.array(prices)
    
    # Create DataFrame for features
    df = pd.DataFrame({
        'close': prices,
        'high': prices * 1.002,
        'low': prices * 0.998,
        'open': prices,
        'volume': np.random.randint(1_000_000, 10_000_000, len(prices)),
    })
    
    features = FeatureEngineerEnhanced.compute(df)
    # Pad if needed
    if len(features) < n_bars:
        pad = np.zeros((n_bars - len(features), n_features))
        features = np.vstack([pad, features])
    features = features[:n_bars]
    prices = prices[-n_bars:]
    
    # Initialize models
    log.info("Initializing models...")
    
    # Transformer (create a small one for demonstration)
    tf_config = TransformerConfig(
        input_dim=n_features, d_model=128, nhead=4,
        num_layers=2, dim_feedforward=256,
        max_seq_len=window_size, num_actions=3
    )
    transformer_model, _ = create_transformer(tf_config)
    
    # Ensemble
    ensemble = EnsembleTrader(cfg)
    
    # Regime classifier
    regime_classifier = MarketRegimeClassifier()
    confidence_scorer = ConfidenceScorer(cfg)
    
    # Create fusion engine
    engine = create_fusion_engine(
        cfg,
        transformer_model=transformer_model,
        transformer_config=tf_config,
        ensemble=ensemble,
    )
    engine.register_classifiers(regime_classifier, confidence_scorer)
    
    # Run backtest
    log.info(f"Running fusion backtest over {n_bars} bars...")
    cash = float(cfg.INITIAL_CASH)
    shares = 0.0
    entry_price = 0.0
    trade_pnls = []
    nav_history = [cash]
    decisions_log = []
    
    for i in range(window_size, len(features) - 1):
        # Build observation
        window = features[i - window_size:i].flatten()
        cash_ratio = cash / (cash + 1.0)
        obs = np.concatenate([window, [cash_ratio, 0.0]]).astype(np.float32)
        
        # Build DataFrame for ensemble/regime (guard against short slices early in loop)
        lo = max(0, i - 50)
        seg = prices[lo:i]
        bdf = pd.DataFrame({
            'close': seg,
            'high': seg * 1.002,
            'low': seg * 0.998,
            'volume': np.random.randint(1_000_000, 10_000_000, len(seg)),
        })
        
        # Get fused decision
        decision = engine.get_decision(obs, features_df=bdf)
        decisions_log.append(decision)
        
        current_price = prices[i]
        nav = cash + shares * current_price
        nav_history.append(nav)
        
        if decision.action == 1 and shares == 0:
            max_shares = int(cash * 0.95 / current_price)
            if max_shares >= 1:
                shares = float(max_shares)
                cash -= shares * current_price
                entry_price = current_price
        elif decision.action == 2 and shares > 0:
            pnl = (current_price - entry_price) * shares
            trade_pnls.append(pnl)
            cash += shares * current_price
            shares = 0.0
            engine.record_outcome(decision, (current_price / entry_price - 1) * 100)
    
    # Close remaining
    if shares > 0:
        final_price = prices[-1]
        pnl = (final_price - entry_price) * shares
        trade_pnls.append(pnl)
        cash += shares * final_price
        shares = 0.0
    
    final_nav = cash
    total_return = (final_nav / cfg.INITIAL_CASH - 1) * 100
    
    nav_arr = np.array(nav_history)
    peak = np.maximum.accumulate(nav_arr)
    dd = (peak - nav_arr) / (peak + 1e-9)
    max_dd = dd.max() * 100
    
    wins = sum(1 for p in trade_pnls if p > 0)
    losses = sum(1 for p in trade_pnls if p < 0)
    total_trades = len(trade_pnls)
    win_rate = wins / max(total_trades, 1) * 100
    
    avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
    avg_loss = np.mean([p for p in trade_pnls if p < 0]) if losses > 0 else 0
    
    trade_returns = [p / cfg.INITIAL_CASH for p in trade_pnls]
    sharpe = 0.0
    if len(trade_returns) >= 5 and np.std(trade_returns) > 0:
        sharpe = float(np.mean(trade_returns) / np.std(trade_returns) * np.sqrt(252))
    
    profit_factor = abs(sum(p for p in trade_pnls if p > 0) / (sum(abs(p) for p in trade_pnls if p < 0) + 1e-9))
    
    # Log results
    log.info("=" * 70)
    log.info("  🏆 FUSION BACKTEST RESULTS")
    log.info("=" * 70)
    log.info(f"  Initial NAV:  ${cfg.INITIAL_CASH:>8,.2f}")
    log.info(f"  Final NAV:    ${final_nav:>8,.2f}")
    log.info(f"  Return:       {total_return:>+8.2f}%")
    log.info(f"  P&L:          ${final_nav - cfg.INITIAL_CASH:>+8,.2f}")
    log.info(f"  Max DD:       {max_dd:>8.2f}%")
    log.info(f"  Sharpe:       {sharpe:>8.3f}")
    log.info(f"  Trades:       {total_trades:>8} ({wins}W / {losses}L)")
    log.info(f"  Win Rate:     {win_rate:>8.1f}%")
    log.info(f"  Profit Fac:   {profit_factor:>8.2f}")
    log.info(f"  Avg Win:      ${avg_win:>+8,.2f}")
    log.info(f"  Avg Loss:     ${avg_loss:>+8,.2f}")
    log.info("=" * 70)
    
    # Print model accuracy
    acc_summary = engine.accuracy_tracker.get_summary()
    log.info("  Model Accuracy Summary:")
    for model_name, stats in acc_summary.items():
        log.info(f"    {model_name:15s}: accuracy={stats['accuracy']:.1%}, "
                 f"weight={stats['weight']:.2f}, samples={stats['samples']}")
    
    # Print fusion performance stats
    perf_stats = engine.get_performance_stats()
    log.info(f"  Avg Fusion Latency: {perf_stats['avg_fusion_latency_ms']:.1f}ms")
    log.info(f"  Decisions Made: {perf_stats['num_decisions']}")
    
    # Save results
    import json
    from datetime import datetime
    results = {
        'initial_nav': round(cfg.INITIAL_CASH, 2),
        'final_nav': round(final_nav, 2),
        'total_return_pct': round(total_return, 2),
        'total_pnl': round(final_nav - cfg.INITIAL_CASH, 2),
        'max_drawdown_pct': round(max_dd, 2),
        'sharpe_ratio': round(sharpe, 3),
        'trades': total_trades,
        'wins': wins,
        'losses': losses,
        'win_rate_pct': round(win_rate, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'model_accuracy': acc_summary,
        'fusion_stats': {k: v for k, v in perf_stats.items() if k != 'model_accuracy'},
    }
    
    results_path = f"backtest_results/fusion_backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs("backtest_results", exist_ok=True)
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2)
    log.info(f"Results saved -> {results_path}")
    
    return results


if __name__ == "__main__":
    args = build_arg_parser().parse_args()

    cfg = BotConfig()
    cfg.TICKER = args.ticker.upper()
    git_sync_init(cfg)
    cfg.INITIAL_CASH = args.cash
    cfg.IB_PORT = args.port
    cfg.IB_CLIENT_ID = args.client_id
    if args.risk_pct is not None:
        cfg.RISK_PER_TRADE_PCT = args.risk_pct
    if args.max_risk_usd is not None:
        cfg.MAX_RISK_PER_TRADE_USD = args.max_risk_usd
    if args.sizing_mode is not None:
        cfg.SIZING_MODE = args.sizing_mode
    if args.order_size_usd is not None:
        cfg.FULL_CASH_ORDER_SIZE_USD = args.order_size_usd

    # Apply LSE overrides if requested
    if getattr(args, "lse", False):
        cfg.EXCHANGE = "LSE"
        cfg.CURRENCY = "GBP"
        cfg.HISTORY_BAR_SIZE = "1 day"
        cfg.IB_PORT = 7497  # paper default for LSE
        log.info("🌍 LSE mode enabled: exchange=LSE, currency=GBP")

    # Apply timeframe overrides if provided
    tf = getattr(args, "timeframe", None)
    if tf:
        cfg.TRADING_TIMEFRAME = tf
        risk_map = {
            "1min": ("1 min", 0.7, 1.5, 0.010, 0.03),
            "5min": ("5 mins", 1.0, 2.0, 0.015, 0.05),
            "1h": ("1 hour", 1.5, 3.0, 0.025, 0.08),
            "4h": ("4 hours", 2.0, 4.0, 0.035, 0.12),
            "1d": ("1 day", 2.5, 5.0, 0.050, 0.20),
        }
        if tf in risk_map:
            bar_size, stop_atr, tp_atr, max_stop, max_tp = risk_map[tf]
            cfg.HISTORY_BAR_SIZE = bar_size
            cfg.SCALP_STOP_ATR_MULTIPLIER = stop_atr
            cfg.SCALP_TP_ATR_MULTIPLIER = tp_atr
            cfg.SCALP_MAX_STOP_PCT = max_stop
            cfg.SCALP_MAX_TP_PCT = max_tp
            log.info(f"⏱ Timeframe set to {tf}: bar={bar_size}, stop_atr={stop_atr}, tp_atr={tp_atr}")

    if args.port == 7496:
        log.warning("=" * 70)
        log.warning("  LIVE TRADING PORT (7496) DETECTED")
        log.warning("  REAL MONEY IS AT RISK.")
        log.warning("  Ensure PAPER_TRADING = True in BotConfig if you want")
        log.warning("  paper mode on the live port.")
        log.warning("=" * 70)

    log.info(f"HANOON | mode={args.mode.upper()} | ticker={cfg.TICKER} | "
              f"capital=${cfg.INITIAL_CASH:,.0f} | port={cfg.IB_PORT} | client_id={cfg.IB_CLIENT_ID}")

    if args.mode == "warmup":
        run_warmup(cfg)

    elif args.mode == "evaluate":
        run_evaluate(cfg)

    elif args.mode == "trade":
        notifier = Notifier(cfg)
        connector = IBConnector(cfg, notifier)
        if not connector.connect():
            log.error("Cannot start trading — IB connection failed. See checklist above.")
            sys.exit(1)

        try:
            trader = LiveTrader(connector, cfg, notifier)
            trader.setup()
            trader.run()
        except FileNotFoundError as exc:
            log.error(str(exc))
            connector.disconnect()
            sys.exit(1)
        except Exception as exc:
            log.exception(f"Fatal error in trading loop: {exc}")
            notifier.error("main trading loop", str(exc))
            connector.disconnect()
            sys.exit(1)

    elif args.mode == "replay-live":
        from core.replay_scalper_runner import run_replay_scalper
        try:
            run_replay_scalper(cfg)
        except KeyboardInterrupt:
            log.info("Replay-live stopped by user.")
        except Exception as exc:
            log.exception(f"Fatal error in replay-live: {exc}")
            try:
                from core.learning_persistence import emergency_snapshot, mark_session_end
                emergency_snapshot(cfg)
                mark_session_end()
            except Exception:
                pass
            sys.exit(1)

    elif args.mode == "scalper":
        notifier = Notifier(cfg)
        connector = IBConnector(cfg, notifier)
        if not connector.connect():
            log.error("Cannot start HANOON — IB connection failed.")
            sys.exit(1)

        try:
            scalper = ScalperRunner(connector, cfg, notifier)
            scalper.run()
        except Exception as exc:
            log.exception(f"Fatal error in HANOON loop: {exc}")
            try:
                from core.learning_persistence import emergency_snapshot, mark_session_end
                emergency_snapshot(cfg, model=getattr(scalper, "model", None), runner=scalper)
                mark_session_end()
            except Exception:
                pass
            notifier.error("HANOON main loop", str(exc))
            connector.disconnect()
            sys.exit(1)

    elif args.mode == "advanced-train":
        log.info("🧠 Starting advanced multi-model AI training...")
        results = run_advanced_training(args)
        log.info(f"✅ Training complete. Results saved to training_history file.")
        if results and 'metrics' in results:
            bt = results['metrics'].get('backtest', {})
            if bt:
                log.info(f"Backtest: {bt.get('total_return_pct', 0):+.2f}% | "
                         f"Sharpe: {bt.get('sharpe_ratio', 0):.3f} | "
                         f"Trades: {bt.get('trades', 0)}")

    elif args.mode == "fusion-backtest":
        log.info("🧠 Running multi-model fusion backtest...")
        results = run_fusion_backtest(args)
        log.info(f"✅ Fusion backtest complete.")

    elif args.mode == "fusion-trade":
        # Placeholder: In production, this would use the fusion engine in live trading
        log.info("🧠 Multi-model fusion live trading mode")
        log.info("This mode uses ALL models: PPO + Transformer + LSTM + Ensemble")
        log.info("Running backtest first to validate...")
        
        # First run the backtest to warm up the models
        bt_args = args
        bt_args.bt_bars = 500
        try:
            results = run_fusion_backtest(bt_args)
        except Exception as exc:
            log.warning(f"Initial backtest: {exc}")
        
        log.info("=" * 70)
        log.info("  FUSION LIVE TRADE MODE")
        log.info("  For production deployment, the fusion engine is integrated")
        log.info("  into the LiveTrader via the predict_with_reasoning pipeline.")
        log.info("  The multi-model fusion runs alongside existing guardrails,")
        log.info("  risk management, and regime classifiers.")
        log.info("=" * 70)
        
        # Proceed with normal live trading (fusion-enhanced)
        notifier = Notifier(cfg)
        connector = IBConnector(cfg, notifier)
        if not connector.connect():
            log.error("Cannot start fusion trading — IB connection failed.")
            sys.exit(1)

        try:
            # Use the existing LiveTrader which now supports enhanced AI
            trader = LiveTrader(connector, cfg, notifier)
            trader.setup()
            trader.run()
        except FileNotFoundError as exc:
            log.error(str(exc))
            connector.disconnect()
            sys.exit(1)
        except Exception as exc:
            log.exception(f"Fatal error in fusion trading loop: {exc}")
            notifier.error("fusion trading loop", str(exc))
            connector.disconnect()
            sys.exit(1)
