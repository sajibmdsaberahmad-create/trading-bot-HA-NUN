#!/usr/bin/env python3
"""
main.py — Entry point. See docs/LAUNCH_GUIDE.md for full setup and
docs/TRAINING_GUIDE.md for how the PPO agent is trained and tuned.

QUICK START
  python main.py --mode warmup            # train once (~20-40 min)
  python main.py --mode trade             # paper trade live
  python main.py --mode evaluate          # offline backtest, no orders placed

Full docs: docs/LAUNCH_GUIDE.md, docs/TRAINING_GUIDE.md, docs/ARCHITECTURE.md
"""

import argparse
import sys

from core.config import BotConfig
from core.notify import log, Notifier
from core.connector import IBConnector
from core.runners import run_warmup, run_evaluate
from core.trader import LiveTrader
from core.scalper_runner import ScalperRunner
from core.git_sync import init as git_sync_init, push_startup


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="PPO Momentum Trading Bot — IB Gateway Edition v3.0 (risk-managed + scalper)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
QUICK START:
  1. python main.py --mode warmup
  2. python main.py --mode trade
  3. python main.py --mode scalper          # NEW: institutional penny stock scalper

EXAMPLES:
  python main.py --mode warmup --ticker QQQ --cash 1000
  python main.py --mode trade
  python main.py --mode scalper             # Penny stock scalper (default mode)
  python main.py --mode scalper --mode ppo  # Use PPO agent instead
  python main.py --mode evaluate --ticker SPY
  python main.py --mode trade --port 7496   # LIVE — real money, be careful
        """,
    )
    parser.add_argument("--mode", choices=["warmup", "trade", "evaluate", "scalper"], required=True,
                         help="warmup: train PPO | trade: live paper/live | evaluate: backtest | scalper: inst momentum")
    parser.add_argument("--algo", choices=["ppo", "scalper"], default=None,
                         help="Override TRADING_MODE: ppo (original) or scalper (new institutional scalper)")
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
    return parser


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

    if args.port == 7496:
        log.warning("=" * 70)
        log.warning("  LIVE TRADING PORT (7496) DETECTED")
        log.warning("  REAL MONEY IS AT RISK.")
        log.warning("  Ensure PAPER_TRADING = True in BotConfig if you want")
        log.warning("  paper mode on the live port.")
        log.warning("=" * 70)

    log.info(f"Starting | mode={args.mode.upper()} | ticker={cfg.TICKER} | "
              f"capital=${cfg.INITIAL_CASH:,.0f} | port={cfg.IB_PORT} | client_id={cfg.IB_CLIENT_ID}")
    push_startup(args.mode, cfg.TICKER)

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

    elif args.mode == "scalper":
        notifier = Notifier(cfg)
        connector = IBConnector(cfg, notifier)
        if not connector.connect():
            log.error("Cannot start scalper — IB connection failed.")
            sys.exit(1)

        try:
            scalper = ScalperRunner(connector, cfg, notifier)
            scalper.run()
        except Exception as exc:
            log.exception(f"Fatal error in scalper loop: {exc}")
            notifier.error("main scalper loop", str(exc))
            connector.disconnect()
            sys.exit(1)
