#!/usr/bin/env python3
"""
core/notify.py — Logging setup and multi-channel notifications.

Every important event (trade opened/closed, stop triggered, risk halt,
reconnect, error, daily summary) flows through the Notifier class so you
get the same alert on the terminal, in the log file, AND on your phone
via Telegram, no matter which mode the bot is running in.

WHY TELEGRAM
Telegram's Bot API is free, requires no approval process, delivers
push notifications instantly to iOS/Android/desktop, and uses a plain
HTTPS POST — meaning it works identically whether the bot is on your
Mac or on a headless Linux VPS. No SMTP server, no app passwords.

SETUP (see docs/LAUNCH_GUIDE.md for the full walkthrough):
  1. Message @BotFather on Telegram, send /newbot, follow prompts.
  2. Copy the bot token it gives you.
  3. Export TRADING_BOT_TELEGRAM_TOKEN before launching the bot.
  4. Message the bot from ANY Telegram account and verify:
       /verify YOUR_SECRET_PHRASE
     Verified chat IDs receive all alerts (no fixed chat_id required).
  Optional legacy: TRADING_BOT_TELEGRAM_CHAT_ID + TELEGRAM_VERIFIED_ONLY_OUTBOUND=false
"""

import logging
import os
import smtplib
import sys
from pathlib import Path
import urllib.request
import urllib.parse
import urllib.error
import json
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional, Dict, Any

from core.config import BotConfig
from core.market_hours import MARKET_TZ


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

class ETFormatter(logging.Formatter):
    """Log timestamps always in US Eastern (NYSE clock)."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, MARKET_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S ET")


def resolve_hanoon_log_path() -> str:
    """
    Single canonical log file: logs/HANOON.log (override via HANOON_LOG_PATH).
    Merges legacy ./HANOON.log into logs/ once on first resolve.
    """
    env = (os.getenv("HANOON_LOG_PATH") or os.getenv("LOG_PATH") or "").strip()
    if env:
        p = Path(env).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p.resolve())

    root = Path.cwd()
    canonical = root / "logs" / "HANOON.log"
    legacy = root / "HANOON.log"
    canonical.parent.mkdir(parents=True, exist_ok=True)

    if legacy.is_file() and not legacy.is_symlink():
        try:
            if not canonical.exists() or legacy.stat().st_mtime >= canonical.stat().st_mtime:
                with open(legacy, "r", encoding="utf-8", errors="replace") as src:
                    tail = src.read()
                if tail:
                    with open(canonical, "a", encoding="utf-8") as dst:
                        if canonical.stat().st_size > 0 and not tail.startswith("\n"):
                            dst.write("\n")
                        dst.write(tail)
            legacy.rename(legacy.with_name(f"HANOON.log.migrated.{int(legacy.stat().st_mtime)}"))
        except OSError:
            pass

    try:
        link = root / "HANOON.log"
        if not link.exists() and not link.is_symlink():
            link.symlink_to(Path("logs") / "HANOON.log")
    except OSError:
        pass

    return str(canonical.resolve())


def build_logger(log_path: Optional[str] = None) -> logging.Logger:
    """
    Build the project-wide logger. Writes to both stdout and a rotating
    log file. ib_insync's own chatty network logs are suppressed to
    WARNING so they don't drown out the bot's own status lines.
    """
    if log_path is None:
        log_path = resolve_hanoon_log_path()
    fmt = ETFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S ET",
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    logger = logging.getLogger("HANOON")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(sh)

    logging.getLogger("ib_insync").setLevel(logging.WARNING)
    return logger


log = build_logger()


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFIER
# ─────────────────────────────────────────────────────────────────────────────

class Notifier:
    """
    Fan-out notifications to every enabled channel.

    All sends are best-effort and non-blocking-safe: a failed Telegram
    call (e.g. no internet on the VPS for a moment) is logged but never
    crashes the trading loop. Trading logic must never depend on a
    notification succeeding.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._ai_composer = None

        from core.telegram_auth import outbound_chat_ids, telegram_bot_ready

        self.telegram_token = cfg.TELEGRAM_BOT_TOKEN
        self.telegram_chat  = cfg.TELEGRAM_CHAT_ID  # legacy optional
        self.telegram_ready = telegram_bot_ready(cfg)

        self.email_host = cfg.EMAIL_SMTP_HOST
        self.email_port = cfg.EMAIL_SMTP_PORT
        self.email_from = cfg.EMAIL_FROM
        self.email_to   = cfg.EMAIL_TO
        self.email_pass = cfg.EMAIL_PASSWORD
        self.email_ready = bool(
            cfg.EMAIL_ENABLED and self.email_host and self.email_from
            and self.email_to and self.email_pass
        )

        if cfg.TELEGRAM_ENABLED and not self.telegram_token:
            log.warning(
                "Telegram notifications enabled but TRADING_BOT_TELEGRAM_TOKEN "
                "is not set. Telegram alerts are OFF until configured."
            )
        elif cfg.TELEGRAM_ENABLED and self.telegram_ready and not outbound_chat_ids(cfg):
            log.info(
                "Telegram: no verified commanders yet — message the bot from any account "
                "and send /verify YOUR_SECRET_PHRASE to receive alerts."
            )
        try:
            from core.telegram_auth import register_primary_chat
            if register_primary_chat(cfg):
                log.info("Telegram: primary chat_id from .env registered for outbound alerts")
        except Exception:
            pass
        if cfg.EMAIL_ENABLED and not self.email_ready:
            log.warning(
                "Email notifications enabled in config but SMTP env vars "
                "are incomplete. Email alerts are OFF until set."
            )

        if self.telegram_ready:
            log.info("Notifications: Telegram ✓")
        if self.email_ready:
            log.info("Notifications: Email ✓")
        if not self.telegram_ready and not self.email_ready:
            log.info("Notifications: console/log file only (no Telegram/email configured)")

    def attach_ai_brain(self, ai_commander=None, autopilot=None, consciousness=None, pilot=None):
        """Wire Ollama composer after AI subsystems initialize."""
        if not getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True):
            return
        try:
            from core.ai_notifier import TelegramAIComposer
            self._ai_composer = TelegramAIComposer(
                self.cfg,
                ai_commander=ai_commander,
                autopilot=autopilot,
                consciousness=consciousness,
                pilot=pilot,
            )
            log.info("Notifications: AI Telegram composer ✓")
        except Exception as exc:
            log.debug(f"AI notifier attach skipped: {exc}")

    def smart(self, event_type: str, context: Dict[str, Any], fallback: str):
        """AI-crafted Telegram alert with structured fallback."""
        from core.ai_notifier import send_smart_telegram
        send_smart_telegram(self, event_type, context, fallback)

    # ── Public send methods (one per event type) ───────────────────────────

    def trade_opened(self, side: str, ticker: str, qty: float, price: float,
                      stop_price: float, target_price: float, risk_usd: float):
        bal = getattr(self.cfg, "_latest_account_balance", None)
        fallback = (
            f"🟢 TRADE OPENED\n"
            f"{side} {qty:.2f} {ticker} @ ${price:.2f}\n"
            f"Stop: ${stop_price:.2f}  |  Target: ${target_price:.2f}\n"
            f"Risking: ${risk_usd:.2f}"
            + (f"\nAccount: ${bal:,.2f}" if bal else "")
        )
        if not self.cfg.NOTIFY_ON_TRADE_OPEN:
            return
        ctx = {
            "ticker": ticker, "shares": qty, "entry": price, "price": price,
            "stop": stop_price, "target": target_price, "risk_usd": risk_usd,
            "side": side,
        }
        if getattr(self, "_ai_composer", None) and getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True):
            self.smart("trade_opened", ctx, fallback)
        else:
            self._send_all(fallback)

    def trade_closed(self, ticker: str, qty: float, price: float,
                      pnl_usd: float, pnl_pct: float, reason: str):
        emoji = "✅" if pnl_usd >= 0 else "🔴"
        bal = getattr(self.cfg, "_latest_account_balance", None)
        fallback = (
            f"{emoji} TRADE CLOSED ({reason})\n"
            f"{qty:.2f} {ticker} @ ${price:.2f}\n"
            f"P&L: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)"
            + (f"\nAccount: ${bal:,.2f}" if bal else "")
        )
        if not self.cfg.NOTIFY_ON_TRADE_CLOSE:
            return
        ctx = {
            "ticker": ticker, "shares": qty, "price": price, "exit": price,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "reason": reason,
            "result": "win" if pnl_usd >= 0 else "loss",
        }
        if getattr(self, "_ai_composer", None) and getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True):
            self.smart("trade_closed", ctx, fallback)
        else:
            self._send_all(fallback)

    def stop_triggered(self, kind: str, ticker: str, trigger_price: float, detail: str = ""):
        msg = f"⛔ {kind.upper()} TRIGGERED — {ticker} @ ${trigger_price:.2f}\n{detail}"
        if self.cfg.NOTIFY_ON_STOP_TRIGGER:
            self._send_all(msg)

    def risk_halt(self, reason: str):
        msg = f"🛑 TRADING HALTED\n{reason}"
        if self.cfg.NOTIFY_ON_RISK_HALT:
            self._send_all(msg)

    def reconnect_event(self, success: bool, attempt: int = 0):
        if success:
            msg = "🔌 Reconnected to IB Gateway successfully."
        else:
            msg = f"⚠️ Reconnect attempt {attempt} failed — retrying…"
        if self.cfg.NOTIFY_ON_RECONNECT:
            self._send_all(msg)

    def error(self, context: str, detail: str):
        msg = f"❗ ERROR in {context}\n{detail}"
        if self.cfg.NOTIFY_ON_ERROR:
            self._send_all(msg)

    def daily_summary(self, summary_text: str):
        msg = f"📊 DAILY SUMMARY\n{summary_text}"
        if self.cfg.NOTIFY_DAILY_SUMMARY:
            self._send_all(msg)

    def info(self, text: str, event_type: str = "info", context: Optional[Dict[str, Any]] = None,
             skip_compose: bool = False):
        """Generic notification — AI-composed when composer is attached."""
        msg = text
        if (
            not skip_compose
            and self._ai_composer
            and getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True)
            and getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True)
        ):
            msg = self._ai_composer.compose(event_type, context or {}, text)
        self._send_all(msg)

    def warning(self, text: str):
        """Warning notification (market closed, reconnect, etc)."""
        msg = text
        if (
            getattr(self, "_ai_composer", None)
            and getattr(self.cfg, "AI_TELEGRAM_ALL_OUTBOUND", True)
            and getattr(self.cfg, "AI_TELEGRAM_NOTIFICATIONS", True)
        ):
            msg = self._ai_composer.compose_outbound("warning", {"message": text}, text)
        log.warning(f"NOTIFY │ {msg.splitlines()[0]}")
        if self.telegram_ready:
            self._send_telegram(msg)
        if self.email_ready:
            self._send_email(msg)

    # ── Internal fan-out ─────────────────────────────────────────────────────

    def _send_all(self, message: str):
        log.info(f"NOTIFY │ {message.splitlines()[0]}")
        if self.telegram_ready:
            self._send_telegram(message)
        if self.email_ready:
            self._send_email(message)

    def _send_telegram(self, message: str):
        from core.telegram_auth import fanout_telegram
        if fanout_telegram(self.cfg, message, token=self.telegram_token) == 0:
            log.debug("Telegram: no verified recipients — message logged only")

    def _send_email(self, message: str):
        try:
            mime = MIMEText(message)
            mime["Subject"] = "Trading Bot Alert"
            mime["From"] = self.email_from
            mime["To"] = self.email_to

            with smtplib.SMTP(self.email_host, self.email_port, timeout=15) as server:
                server.starttls()
                server.login(self.email_from, self.email_pass)
                server.sendmail(self.email_from, [self.email_to], mime.as_string())
        except Exception as exc:
            log.warning(f"Email send failed: {exc}")

    # ── Advanced AI model notifications ──────────────────────────────────────

    def fusion_decision(self, decision, price: float, ticker: str):
        """Rich notification for multi-model fusion decisions."""
        models_str = []
        for m in decision.model_predictions:
            action_name = ['HOLD', 'BUY', 'SELL'][m.action]
            models_str.append(f"  {m.model_name:12s}: {action_name} ({m.confidence:.0%})")
        
        models_block = "\n".join(models_str)
        weights_block = "\n".join(f"  {k:12s}: w={v:.2f}" for k, v in decision.model_weights.items())
        
        msg = (
            f"🧠 FUSION DECISION: {decision.action_name}\n"
            f"  Ticker: {ticker} @ ${price:.2f}\n"
            f"  Confidence: {decision.confidence:.0%}\n"
            f"  Method: {decision.fusion_method}\n"
            f"── Model Votes ──\n{models_block}\n"
            f"── Weights ──\n{weights_block}\n"
            f"📝 {decision.reasoning[:200]}"
        )
        log.info(f"FUSION │ {decision.action_name} ({decision.confidence:.0%}) "
                 f"| {len(decision.model_predictions)} models | {decision.reasoning[:80]}…")
        if self.telegram_ready:
            self._send_telegram(msg)
    
    def model_accuracy_update(self, accuracy_summary: dict):
        """Weekly accuracy report of all ensemble models."""
        lines = [f"  {k:15s}: acc={v['accuracy']:.1%}, w={v['weight']:.2f}, n={v['samples']}" 
                 for k, v in accuracy_summary.items()]
        msg = "📈 MODEL ACCURACY REPORT\n" + "\n".join(lines)
        log.info(f"ACCURACY │ {len(accuracy_summary)} models tracked")
        if self.telegram_ready:
            self._send_telegram(msg)

    def training_progress(self, model_name: str, epoch: int, loss: float, val_loss: float = None):
        """Training progress update during advanced training pipeline."""
        val = f" | val={val_loss:.4f}" if val_loss else ""
        msg = f"🏋️ TRAINING {model_name}\nEpoch {epoch} | loss={loss:.4f}{val}"
        log.info(f"TRAIN │ {model_name} epoch {epoch}: loss={loss:.4f}{val}")
        # Send only every 10 epochs to avoid spam
        if epoch % 10 == 0 and self.telegram_ready:
            self._send_telegram(msg)

    def training_complete(self, model_name: str, elapsed_s: float, metrics: dict = None):
        """Training complete notification."""
        metrics_block = ""
        if metrics:
            metrics_block = "\n" + "\n".join(f"  {k}: {v}" for k, v in metrics.items())
        msg = (f"✅ TRAINING COMPLETE: {model_name}\n"
               f"  Elapsed: {elapsed_s:.0f}s{metrics_block}")
        log.info(f"TRAIN │ {model_name} complete in {elapsed_s:.0f}s")
        if self.telegram_ready:
            self._send_telegram(msg)

    def backtest_result(self, results: dict):
        """Comprehensive backtest result notification."""
        msg = (
            f"📊 BACKTEST RESULTS\n"
            f"  Return: {results.get('total_return_pct', 0):+.2f}%\n"
            f"  Final NAV: ${results.get('final_nav', 0):,.2f}\n"
            f"  Sharpe: {results.get('sharpe_ratio', 0):.3f}\n"
            f"  Max DD: {results.get('max_drawdown_pct', 0):.2f}%\n"
            f"  Trades: {results.get('trades', 0)} ({results.get('win_rate_pct', 0):.0f}% WR)\n"
            f"  Profit Factor: {results.get('profit_factor', 0):.2f}"
        )
        # Add model accuracy if available
        if 'model_accuracy' in results and results['model_accuracy']:
            acc_lines = "\n".join(f"  {k}: {v['accuracy']:.1%}" for k, v in results['model_accuracy'].items())
            msg += f"\n── Model Accuracy ──\n{acc_lines}"
        
        log.info(f"BACKTEST │ Return: {results.get('total_return_pct', 0):+.2f}% | "
                 f"Sharpe: {results.get('sharpe_ratio', 0):.3f} | "
                 f"{results.get('trades', 0)} trades")
        if self.telegram_ready:
            self._send_telegram(msg)

    def ai_state(self, ticker: str, mode: str, models_active: int, equity: float):
        """Periodic AI system state summary."""
        msg = (
            f"🤖 AI SYSTEM STATUS\n"
            f"  Mode: {mode.upper()}\n"
            f"  Active Models: {models_active}\n"
            f"  Ticker: {ticker}\n"
            f"  Equity: ${equity:,.2f}"
        )
        log.info(f"STATUS │ {mode} | {models_active} models | ${equity:,.2f}")
        if self.telegram_ready:
            self._send_telegram(msg)
