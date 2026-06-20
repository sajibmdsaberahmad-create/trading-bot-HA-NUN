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
  3. Message your new bot once (anything), then visit:
     https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your numeric chat_id in the response JSON.
  4. Export both as environment variables before launching the bot:
       export TRADING_BOT_TELEGRAM_TOKEN="123456:ABC-..."
       export TRADING_BOT_TELEGRAM_CHAT_ID="987654321"
"""

import logging
import os
import smtplib
import sys
import urllib.request
import urllib.parse
import urllib.error
import json
from email.mime.text import MIMEText
from typing import Optional

from core.config import BotConfig


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def build_logger(log_path: str = "trading_bot.log") -> logging.Logger:
    """
    Build the project-wide logger. Writes to both stdout and a rotating
    log file. ib_insync's own chatty network logs are suppressed to
    WARNING so they don't drown out the bot's own status lines.
    """
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    logger = logging.getLogger("TradingBot")
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

        self.telegram_token = cfg.TELEGRAM_BOT_TOKEN
        self.telegram_chat  = cfg.TELEGRAM_CHAT_ID
        self.telegram_ready = bool(
            cfg.TELEGRAM_ENABLED and self.telegram_token and self.telegram_chat
        )

        self.email_host = cfg.EMAIL_SMTP_HOST
        self.email_port = cfg.EMAIL_SMTP_PORT
        self.email_from = cfg.EMAIL_FROM
        self.email_to   = cfg.EMAIL_TO
        self.email_pass = cfg.EMAIL_PASSWORD
        self.email_ready = bool(
            cfg.EMAIL_ENABLED and self.email_host and self.email_from
            and self.email_to and self.email_pass
        )

        if cfg.TELEGRAM_ENABLED and not self.telegram_ready:
            log.warning(
                "Telegram notifications enabled in config but TOKEN/CHAT_ID "
                "are not set in .env. Telegram alerts are OFF until set. "
                "See docs/MOMENTUM_STRATEGY_GUIDE.md."
            )
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

    # ── Public send methods (one per event type) ───────────────────────────

    def trade_opened(self, side: str, ticker: str, qty: float, price: float,
                      stop_price: float, target_price: float, risk_usd: float):
        msg = (
            f"🟢 TRADE OPENED\n"
            f"{side} {qty:.2f} {ticker} @ ${price:.2f}\n"
            f"Stop: ${stop_price:.2f}  |  Target: ${target_price:.2f}\n"
            f"Risking: ${risk_usd:.2f}"
        )
        if self.cfg.NOTIFY_ON_TRADE_OPEN:
            self._send_all(msg)

    def trade_closed(self, ticker: str, qty: float, price: float,
                      pnl_usd: float, pnl_pct: float, reason: str):
        emoji = "✅" if pnl_usd >= 0 else "🔴"
        msg = (
            f"{emoji} TRADE CLOSED ({reason})\n"
            f"{qty:.2f} {ticker} @ ${price:.2f}\n"
            f"P&L: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)"
        )
        if self.cfg.NOTIFY_ON_TRADE_CLOSE:
            self._send_all(msg)

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

    def info(self, text: str):
        """Generic low-priority notification (startup, shutdown, etc)."""
        self._send_all(text)

    # ── Internal fan-out ─────────────────────────────────────────────────────

    def _send_all(self, message: str):
        log.info(f"NOTIFY │ {message.splitlines()[0]}")
        if self.telegram_ready:
            self._send_telegram(message)
        if self.email_ready:
            self._send_email(message)

    def _send_telegram(self, message: str):
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.telegram_chat,
                "text": message,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.URLError as exc:
            log.warning(f"Telegram send failed (network): {exc}")
        except Exception as exc:
            log.warning(f"Telegram send failed: {exc}")

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
