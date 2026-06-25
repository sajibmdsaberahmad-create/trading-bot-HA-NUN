#!/usr/bin/env python3
"""
core/telegram_listener.py — Inbound Telegram copilot for HANOON.

Listens for commands, free-text direction, and chart photos from any Telegram
account after secret-phrase verification. Runs in a background thread so the
trading loop is never blocked.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.daily_activity_report import collect_day_report, format_structured_report
from core.market_hours import format_et, get_market_state
from core.notify import log
from core.telegram_auth import is_verified, verification_required, verify_phrase

if TYPE_CHECKING:
    from core.ai_commander import AICommander
    from core.scalper_runner import ScalperRunner

from core.commander_learning import (
    GUIDANCE_PATH,
    format_apply_report,
    load_commander_guidance,
    maybe_auto_apply_from_chat,
    run_commander_learning_cycle,
)
from core.position_intel import (
    format_positions_report,
    format_risk_report,
)
from core.system_status import collect_system_status, format_system_report
from core.ai_telegram import format_outbound_message


class TelegramCommandListener:
    """Poll Telegram getUpdates and route verified chats to the AI copilot."""

    def __init__(
        self,
        cfg: BotConfig,
        runner: Optional["ScalperRunner"] = None,
        ai_commander: Optional["AICommander"] = None,
        think_fn: Optional[Callable[[str], str]] = None,
        vision_fn: Optional[Callable[[str, bytes], str]] = None,
    ):
        self.cfg = cfg
        self.runner = runner
        self.ai_commander = ai_commander
        self.think_fn = think_fn
        self.vision_fn = vision_fn
        self._token = (getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or "").strip().strip("'\"")
        self._offset = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._poll_sec = float(getattr(cfg, "TELEGRAM_POLL_INTERVAL_SEC", 1.5))

    @property
    def enabled(self) -> bool:
        return (
            bool(getattr(self.cfg, "TELEGRAM_LISTEN_ENABLED", True))
            and bool(self._token)
            and bool(getattr(self.cfg, "TELEGRAM_ENABLED", True))
        )

    def start(self) -> None:
        if not self.enabled:
            log.info("Telegram listener: disabled (no token or TELEGRAM_LISTEN_ENABLED=false)")
            return
        if self._thread and self._thread.is_alive():
            return
        if verification_required(self.cfg) and not (getattr(self.cfg, "TELEGRAM_VERIFY_SECRET", "") or "").strip():
            log.warning(
                "Telegram listener: TELEGRAM_VERIFY_SECRET not set — "
                "inbound commands will reject all chats until configured"
            )
        self._stop.clear()
        self._ensure_polling_mode()
        self._verify_bot_token()
        try:
            from core.telegram_auth import register_primary_chat
            register_primary_chat(self.cfg)
        except Exception as exc:
            log.debug(f"Telegram primary chat register: {exc}")
        self._thread = threading.Thread(target=self._poll_loop, name="telegram-listener", daemon=True)
        self._thread.start()
        secret = (getattr(self.cfg, "TELEGRAM_VERIFY_SECRET", "") or "").strip()
        log.info(
            "Telegram listener: active — message bot then /verify <secret> "
            f"(secret {'configured' if secret else 'MISSING'})"
        )

    def _ensure_polling_mode(self) -> None:
        """Remove webhook so getUpdates polling receives messages."""
        try:
            self._api("deleteWebhook", {"drop_pending_updates": False}, timeout=15)
        except Exception as exc:
            log.debug(f"Telegram deleteWebhook: {exc}")

    def _verify_bot_token(self) -> None:
        try:
            resp = self._api("getMe", timeout=15)
            if resp.get("ok"):
                user = resp.get("result", {})
                log.info(f"Telegram bot connected: @{user.get('username', '?')} (id {user.get('id', '?')})")
            else:
                log.warning(f"Telegram getMe failed: {resp}")
        except Exception as exc:
            log.warning(f"Telegram token check failed — inbound chat disabled: {exc}")

    def send_instant(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_to: Optional[int] = None,
    ) -> None:
        """Immediate reply — auth, errors, progress (no Ollama wait)."""
        self.send(chat_id, text, reply_to=reply_to)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def attach_runner(self, runner: "ScalperRunner") -> None:
        self.runner = runner

    def attach_ai(self, ai_commander: "AICommander", think_fn: Callable[[str], str]) -> None:
        self.ai_commander = ai_commander
        self.think_fn = think_fn

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _api(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: int = 35) -> Dict:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        if params:
            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
        else:
            req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def send(self, chat_id: int | str, text: str, *, reply_to: Optional[int] = None) -> None:
        if not text:
            return
        max_len = 4096
        chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
        for chunk in chunks:
            params: Dict[str, Any] = {"chat_id": str(chat_id), "text": chunk}
            if reply_to:
                params["reply_to_message_id"] = reply_to
            try:
                self._api("sendMessage", params, timeout=15)
            except Exception as exc:
                log.warning(f"Telegram reply failed: {exc}")

    def send_ai(
        self,
        chat_id: int | str,
        event: str,
        context: Dict[str, Any],
        fallback: str,
        *,
        reply_to: Optional[int] = None,
        sync: bool = False,
        max_chars: Optional[int] = None,
        instant_first: bool = False,
    ) -> None:
        """AI-compose then deliver. Use instant_first=True to send fallback immediately."""
        if instant_first and fallback:
            self.send(chat_id, fallback, reply_to=reply_to)

        captured_reply_to = reply_to

        def deliver() -> None:
            try:
                text = format_outbound_message(
                    self.cfg,
                    event,
                    context,
                    fallback,
                    ai_commander=self.ai_commander,
                    runner=self.runner,
                    copilot=True,
                    max_chars=max_chars,
                )
            except Exception as exc:
                log.debug(f"send_ai {event}: {exc}")
                text = fallback
            if instant_first:
                if text and text.strip() and text.strip() != (fallback or "").strip():
                    self.send(chat_id, text, reply_to=None)
            else:
                self.send(chat_id, text or fallback, reply_to=captured_reply_to)

        if sync:
            deliver()
        else:
            threading.Thread(target=deliver, name=f"tg-ai-{event}", daemon=True).start()

    def _download_photo(self, file_id: str) -> Optional[bytes]:
        try:
            meta = self._api("getFile", {"file_id": file_id}, timeout=15)
            path = meta.get("result", {}).get("file_path")
            if not path:
                return None
            url = f"https://api.telegram.org/file/bot{self._token}/{path}"
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read()
        except Exception as exc:
            log.debug(f"Telegram photo download: {exc}")
            return None

    # ── Poll loop ────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self._api(
                    "getUpdates",
                    {"offset": self._offset, "timeout": 25, "allowed_updates": json.dumps(["message"])},
                    timeout=40,
                )
                if not result.get("ok", True):
                    log.warning(f"Telegram getUpdates error: {result.get('description', result)}")
                    time.sleep(5.0)
                    continue
                for upd in result.get("result", []):
                    self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
                    msg = upd.get("message")
                    if msg:
                        self._handle_message(msg)
            except urllib.error.URLError as exc:
                log.warning(f"Telegram poll network: {exc}")
                time.sleep(5.0)
            except Exception as exc:
                log.warning(f"Telegram poll error: {exc}")
                time.sleep(2.0)
            if not self._stop.is_set():
                time.sleep(self._poll_sec)

    def _handle_message(self, msg: Dict[str, Any]) -> None:
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            return

        user = msg.get("from", {})
        username = user.get("username", "")
        first_name = user.get("first_name", "")
        reply_id = msg.get("message_id")

        text = (msg.get("text") or msg.get("caption") or "").strip()
        photos = msg.get("photo") or []

        if not is_verified(self.cfg, chat_id):
            log.info(f"Telegram inbound (unverified) chat={chat_id} user=@{username or first_name or '?'}: {text[:80]!r}")
            self._handle_unverified(chat_id, text, username, first_name, reply_id)
            return

        if photos:
            self._handle_photo(chat_id, photos, text, reply_id)
            return

        if text.startswith("/"):
            self._handle_command(chat_id, text, reply_id)
            return

        if text:
            log.info(f"Telegram inbound chat={chat_id} user=@{username or first_name or '?'}: {text[:80]!r}")
            self._handle_free_text(chat_id, text, reply_id)

    def _handle_unverified(
        self,
        chat_id: int,
        text: str,
        username: str,
        first_name: str,
        reply_id: Optional[int],
    ) -> None:
        from core.telegram_auth import secret_configured

        if not secret_configured(self.cfg):
            if text.lower().startswith(("/start", "/help")):
                self.send_instant(
                    chat_id,
                    "🔒 HANOON commander access is locked.\n"
                    "Set TRADING_BOT_TELEGRAM_VERIFY_SECRET on the bot host, then restart.",
                    reply_to=reply_id,
                )
            return

        low = text.lower().strip()
        if low.startswith("/start") or low.startswith("/help"):
            self.send_instant(
                chat_id,
                "🛡 HANOON Commander Copilot\n\n"
                "1. Verify from ANY Telegram account:\n"
                "   /verify hall of fame\n\n"
                "2. Then use /help for commands, or send charts + questions.\n\n"
                "Outbound alerts require verification first.",
                reply_to=reply_id,
            )
            return

        if low.startswith("/verify"):
            parts = text.split(maxsplit=1)
            phrase = parts[1].strip() if len(parts) > 1 else ""
            if verify_phrase(self.cfg, chat_id, phrase, username=username, first_name=first_name):
                self.send_instant(
                    chat_id,
                    "✅ Verified — full commander access unlocked.\n"
                    "Try /help · /daily · /positions · /status · /system",
                    reply_to=reply_id,
                )
            else:
                self.send_instant(
                    chat_id,
                    "🔒 Verification failed.\n"
                    "Send: /verify hall of fame\n"
                    "(use your configured secret phrase)",
                    reply_to=reply_id,
                )
            return

        # Plain-text secret phrase (no /verify prefix)
        secret = (getattr(self.cfg, "TELEGRAM_VERIFY_SECRET", "") or "").strip()
        if text and secret and text.strip() == secret:
            if verify_phrase(self.cfg, chat_id, text, username=username, first_name=first_name):
                self.send_instant(
                    chat_id,
                    "✅ Verified — full commander access unlocked.",
                    reply_to=reply_id,
                )
                return

        self.send_instant(
            chat_id,
            "🔒 Not verified yet.\nSend: /verify hall of fame",
            reply_to=reply_id,
        )

    # ── Verified handlers ────────────────────────────────────────────────

    def _handle_command(self, chat_id: int, text: str, reply_id: Optional[int]) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            help_fallback = (
                "HANOON Commander Copilot commands:\n"
                "/daily /brief /mood /status /positions /risk /system /analyze\n"
                "/exit TICKER /exitall profit|loss|all /guide /improve\n"
                "Send chart photos or free-text e.g. exit AAPL lock profit"
            )
            self.send_ai(chat_id, "help", {"command": cmd}, help_fallback, reply_to=reply_id)
            return

        if cmd == "/daily":
            self._cmd_daily(chat_id, reply_id)
            return
        if cmd == "/brief":
            self._cmd_brief(chat_id, reply_id)
            return
        if cmd == "/mood":
            self._cmd_mood(chat_id, reply_id)
            return
        if cmd == "/status":
            self._cmd_status(chat_id, reply_id)
            return
        if cmd == "/guide":
            if not arg:
                self.send_ai(chat_id, "usage", {"command": "/guide"}, "Usage: /guide tighten stops on penny names", reply_to=reply_id)
                return
            self._store_guidance(chat_id, arg)
            self.send_ai(chat_id, "guide_stored", {"guidance": arg[:500]}, f"Guidance stored: {arg[:500]}", reply_to=reply_id)
            threading.Thread(
                target=self._learning_worker,
                args=(chat_id, arg, reply_id),
                daemon=True,
            ).start()
            return

        if cmd == "/improve":
            self.send_ai(chat_id, "improve_progress", {}, "Building improvement plan from your guidance and today's data…", reply_to=reply_id, max_chars=200)
            threading.Thread(
                target=self._improve_worker,
                args=(chat_id, arg or "commander requested /improve", reply_id),
                daemon=True,
            ).start()
            return

        if cmd == "/positions":
            self._cmd_positions(chat_id, reply_id)
            return
        if cmd == "/risk":
            self._cmd_risk(chat_id, reply_id)
            return
        if cmd == "/system":
            self._cmd_system(chat_id, reply_id)
            return
        if cmd == "/analyze":
            self.send_ai(chat_id, "daily_progress", {"task": "analyze_positions"}, "Reviewing open positions…", reply_to=reply_id, max_chars=160)
            threading.Thread(target=self._analyze_positions_worker, args=(chat_id, reply_id), daemon=True).start()
            return
        if cmd == "/exit":
            self._cmd_exit(chat_id, arg, reply_id)
            return
        if cmd == "/exitall":
            self._cmd_exitall(chat_id, arg or "all", reply_id)
            return

        self.send_instant(chat_id, "Unknown command. Try /help", reply_to=reply_id)

    def _handle_free_text(self, chat_id: int, text: str, reply_id: Optional[int]) -> None:
        exit_match = self._parse_exit_intent(text)
        if exit_match:
            ticker, reason = exit_match
            self._cmd_exit(chat_id, f"{ticker} {reason}".strip(), reply_id)
            return
        self._store_guidance(chat_id, text)
        self.send_instant(chat_id, "🧠 On it — pulling live state…", reply_to=reply_id)
        threading.Thread(
            target=self._ai_reply,
            args=(chat_id, text, reply_id, "commander_chat"),
            daemon=True,
        ).start()

    @staticmethod
    def _parse_exit_intent(text: str) -> Optional[tuple[str, str]]:
        import re
        low = text.lower().strip()
        patterns = [
            r"^(?:exit|close|sell|flatten)\s+([A-Za-z]{1,5})(?:\s+(.+))?$",
            r"^(?:lock\s+profit|take\s+profit|stop\s+out)\s+(?:on\s+)?([A-Za-z]{1,5})(?:\s+(.+))?$",
        ]
        for pat in patterns:
            m = re.match(pat, low)
            if m:
                ticker = m.group(1).upper()
                reason = (m.group(2) or "commander_chat").strip()
                return ticker, reason
        return None

    def _handle_photo(
        self,
        chat_id: int,
        photos: List[Dict],
        caption: str,
        reply_id: Optional[int],
    ) -> None:
        from core.ollama_vision import ensure_vision_model, is_vision_model_present, vision_model_name

        model = vision_model_name(self.cfg)
        if not is_vision_model_present(self.cfg, model):
            ensure_vision_model(self.cfg, background=True)
            self.send_ai(
                chat_id,
                "vision_wait",
                {"model": model},
                f"Vision model {model} is downloading — send the chart again in a few minutes.",
                reply_to=reply_id,
                max_chars=200,
            )
            return
        if not self.vision_fn:
            self.send_ai(chat_id, "vision_unavailable", {}, "Vision analysis unavailable.", reply_to=reply_id, max_chars=200)
            return
        best = max(photos, key=lambda p: p.get("file_size", 0))
        raw = self._download_photo(best.get("file_id", ""))
        if not raw:
            self.send_ai(chat_id, "image_download_fail", {}, "Could not download image.", reply_to=reply_id, max_chars=160)
            return
        if caption:
            self._store_guidance(chat_id, f"[chart] {caption}")
        threading.Thread(
            target=self._vision_reply,
            args=(chat_id, caption or "Review this chart for trading setup and improvements.", raw, reply_id),
            daemon=True,
        ).start()

    def _store_guidance(self, chat_id: int, text: str) -> None:
        GUIDANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chat_id": str(chat_id),
            "text": text[:2000],
        }
        try:
            with open(GUIDANCE_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception as exc:
            log.debug(f"Guidance store: {exc}")

    def _runner_ctx(self) -> Dict[str, Any]:
        r = self.runner
        if not r:
            return {}
        try:
            r._refresh_account_balance()
        except Exception:
            pass
        return {
            "ib_account": round(getattr(r, "account_equity", 0), 2),
            "bot_nav": round(getattr(r, "bot_nav", 0), 2),
            "trades_today": getattr(r, "trades_today", 0),
            "position": getattr(r, "current_ticker", None),
            "shares": getattr(r, "shares", 0),
            "market_state": get_market_state(self.cfg),
            "time_et": format_et(),
        }

    def _cmd_status(self, chat_id: int, reply_id: Optional[int]) -> None:
        ctx = self._runner_ctx()
        if not ctx:
            self.send_ai(chat_id, "runner_unavailable", {}, "Bot runner not attached.", reply_to=reply_id, max_chars=120)
            return
        pos_n = 0
        unreal = 0.0
        if self.runner:
            try:
                intel = self.runner.commander_positions_intel()
                pos_n = intel.get("position_count", 0)
                unreal = intel.get("total_unrealized_pnl", 0)
            except Exception:
                pass
        ctx["open_positions"] = pos_n
        ctx["unrealized_pnl"] = unreal
        fallback = (
            f"LIVE STATUS\nMarket {ctx.get('market_state', '?').upper()} {ctx.get('time_et', '')}\n"
            f"IB ${ctx.get('ib_account', 0):,.2f} NAV ${ctx.get('bot_nav', 0):,.2f}\n"
            f"Trades {ctx.get('trades_today', 0)} · Open {pos_n} · Unrealized ${unreal:+,.2f}\n"
            f"Focus {ctx.get('shares', 0):.0f} {ctx.get('position') or 'flat'}"
        )
        self.send_ai(chat_id, "status", ctx, fallback, reply_to=reply_id)

    def _cmd_positions(self, chat_id: int, reply_id: Optional[int]) -> None:
        if not self.runner:
            self.send_ai(chat_id, "runner_unavailable", {}, "Bot runner not attached.", reply_to=reply_id, max_chars=120)
            return
        try:
            intel = self.runner.commander_positions_intel()
            self.send_ai(
                chat_id,
                "positions",
                {"positions_intel": intel},
                format_positions_report(intel),
                reply_to=reply_id,
            )
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"Positions failed: {exc}", reply_to=reply_id)

    def _cmd_risk(self, chat_id: int, reply_id: Optional[int]) -> None:
        if not self.runner:
            self.send_ai(chat_id, "runner_unavailable", {}, "Bot runner not attached.", reply_to=reply_id, max_chars=120)
            return
        try:
            risk = self.runner.commander_risk_summary()
            self.send_ai(chat_id, "risk", {"risk": risk}, format_risk_report(risk), reply_to=reply_id)
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"Risk report failed: {exc}", reply_to=reply_id)

    def _cmd_system(self, chat_id: int, reply_id: Optional[int]) -> None:
        try:
            status = collect_system_status(self.cfg, self.runner)
            self.send_ai(
                chat_id,
                "system",
                {"system_status": status},
                format_system_report(status),
                reply_to=reply_id,
            )
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"System status failed: {exc}", reply_to=reply_id)

    def _cmd_exit(self, chat_id: int, arg: str, reply_id: Optional[int]) -> None:
        if not self.runner:
            self.send_ai(chat_id, "runner_unavailable", {}, "Bot runner not attached.", reply_to=reply_id, max_chars=120)
            return
        parts = (arg or "").split(maxsplit=1)
        if not parts or not parts[0]:
            self.send_ai(
                chat_id,
                "usage",
                {"command": "/exit"},
                "Usage: /exit TICKER [reason] — e.g. /exit AAPL lock profit",
                reply_to=reply_id,
                max_chars=200,
            )
            return
        ticker = parts[0].upper()
        reason = parts[1] if len(parts) > 1 else "commander_telegram"
        self.send_ai(
            chat_id,
            "exit_progress",
            {"ticker": ticker, "reason": reason},
            f"Exiting {ticker}…",
            reply_to=reply_id,
            max_chars=120,
        )
        threading.Thread(
            target=self._exit_worker,
            args=(chat_id, ticker, reason, reply_id),
            daemon=True,
        ).start()

    def _cmd_exitall(self, chat_id: int, mode: str, reply_id: Optional[int]) -> None:
        if not self.runner:
            self.send_ai(chat_id, "runner_unavailable", {}, "Bot runner not attached.", reply_to=reply_id, max_chars=120)
            return
        mode = (mode or "all").lower().strip()
        if mode not in ("profit", "loss", "all"):
            self.send_ai(chat_id, "usage", {"command": "/exitall"}, "Usage: /exitall profit|loss|all", reply_to=reply_id, max_chars=120)
            return
        self.send_ai(chat_id, "exit_progress", {"mode": mode}, f"Bulk exit ({mode})…", reply_to=reply_id, max_chars=120)
        threading.Thread(
            target=self._exitall_worker,
            args=(chat_id, mode, reply_id),
            daemon=True,
        ).start()

    def _exit_worker(self, chat_id: int, ticker: str, reason: str, reply_id: Optional[int]) -> None:
        try:
            result = self.runner.commander_exit_ticker(ticker, reason)
            if result.get("ok"):
                pnl = result.get("pnl")
                fallback = f"Exited {ticker} @ ${result.get('price', 0):.2f} · {reason}"
                if pnl is not None:
                    fallback += f" · P&L ${pnl:+,.2f}"
                self.send_ai(chat_id, "exit_result", result, fallback, reply_to=reply_id, sync=True)
            else:
                self.send_ai(
                    chat_id,
                    "error",
                    result,
                    f"Exit failed: {result.get('error', 'unknown')}",
                    reply_to=reply_id,
                    sync=True,
                )
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"Exit error: {exc}", reply_to=reply_id, sync=True)

    def _exitall_worker(self, chat_id: int, mode: str, reply_id: Optional[int]) -> None:
        try:
            result = self.runner.commander_exit_filtered(mode, f"commander_exitall_{mode}")
            lines = [f"Bulk exit ({mode}): {result.get('exited', 0)} closed"]
            for r in result.get("results", [])[:8]:
                if r.get("ok"):
                    lines.append(f"OK {r.get('ticker')} @ ${r.get('price', 0):.2f}")
                else:
                    lines.append(f"FAIL {r.get('ticker', '?')}: {r.get('error', '?')}")
            self.send_ai(
                chat_id,
                "exitall_result",
                result,
                "\n".join(lines),
                reply_to=reply_id,
                sync=True,
            )
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"Bulk exit error: {exc}", reply_to=reply_id, sync=True)

    def _analyze_positions_worker(self, chat_id: int, reply_id: Optional[int]) -> None:
        if not self.runner:
            self.send_ai(chat_id, "runner_unavailable", {}, "Bot runner not attached.", reply_to=reply_id, sync=True)
            return
        try:
            intel = self.runner.commander_positions_intel()
            risk = self.runner.commander_risk_summary()
            if not intel.get("positions"):
                self.send_ai(chat_id, "flat_positions", {}, "No open positions to analyze.", reply_to=reply_id, sync=True)
                return
            fallback = (
                f"{format_positions_report(intel, max_positions=20)}\n\n"
                f"{format_risk_report(risk)}"
            )
            self.send_ai(
                chat_id,
                "analyze_positions",
                {"positions_intel": intel, "risk": risk},
                fallback,
                reply_to=reply_id,
                sync=True,
            )
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"Analyze failed: {exc}", reply_to=reply_id, sync=True)

    def _cmd_daily(self, chat_id: int, reply_id: Optional[int]) -> None:
        self.send_ai(chat_id, "daily_progress", {"task": "daily_report"}, "Building full-day activity report…", reply_to=reply_id, max_chars=160)
        threading.Thread(target=self._daily_report_worker, args=(chat_id, reply_id, False), daemon=True).start()

    def _cmd_brief(self, chat_id: int, reply_id: Optional[int]) -> None:
        self.send_ai(chat_id, "daily_progress", {"task": "daily_brief"}, "AI analyzing full day…", reply_to=reply_id, max_chars=160)
        threading.Thread(target=self._daily_report_worker, args=(chat_id, reply_id, True), daemon=True).start()

    def _daily_report_worker(self, chat_id: int, reply_id: Optional[int], ai_brief: bool) -> None:
        try:
            connector = getattr(self.runner, "conn", None) if self.runner else None
            report = collect_day_report(self.cfg, self.runner, connector)
            structured = format_structured_report(report, max_lines=60)
            event = "daily_self_eval" if ai_brief else "daily_report"
            max_chars = int(getattr(self.cfg, "TELEGRAM_DAILY_REPORT_MAX_CHARS", 3800)) if ai_brief else None

            if ai_brief:
                from core.daily_self_evaluation import (
                    collect_self_eval_context,
                    compose_self_evaluation,
                    write_self_evaluation_files,
                )
                ctx = collect_self_eval_context(self.cfg, self.runner, connector)
                prompt_think = self._think
                statement = compose_self_evaluation(ctx, prompt_think, self.cfg)
                paths = write_self_evaluation_files(ctx, statement)
                ai_text = statement
                fallback = ai_text if ai_text and len(ai_text) > 80 else structured
                ctx = {
                    "report": report.get("summary", {}),
                    "trades_count": len(report.get("trades", [])),
                    "self_eval_paths": paths,
                    "sessions": ctx.get("sessions", {}),
                }
            else:
                fallback = structured
                ctx = {"report_summary": report.get("summary", {}), "structured_excerpt": structured[:2000]}

            self.send_ai(chat_id, event, ctx, fallback, reply_to=reply_id, sync=True, max_chars=max_chars)
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"Report failed: {exc}", reply_to=reply_id, sync=True)

    def _cmd_mood(self, chat_id: int, reply_id: Optional[int]) -> None:
        mood = "—"
        message = ""
        if self.runner and getattr(self.runner, "consciousness", None):
            ident = self.runner.consciousness.get_identity()
            mood = ident.get("mood", mood)
            message = ident.get("mood_message", "")
        elif self.runner and getattr(self.runner, "autopilot", None):
            core = getattr(self.runner.autopilot, "core", None)
            if core:
                mood = getattr(core.state, "mood", mood)
        ctx = self._runner_ctx()
        ctx["mood"] = mood
        ctx["mood_message"] = message
        fallback = f"MOOD: {mood}\n{message}\nMarket {ctx.get('market_state', '?').upper()} · {ctx.get('trades_today', 0)} trades today"
        self.send_ai(chat_id, "mood", ctx, fallback, reply_to=reply_id)

    def _think(self, prompt: str) -> str:
        if self.ai_commander and hasattr(self.ai_commander, "compose_telegram"):
            try:
                return (self.ai_commander.compose_telegram(prompt) or "").strip()
            except Exception:
                pass
        if self.think_fn:
            try:
                return (self.think_fn(prompt) or "").strip()
            except Exception:
                pass
        return ""

    def _ai_reply(self, chat_id: int, text: str, reply_id: Optional[int], kind: str) -> None:
        ctx = self._runner_ctx()
        report_snip = ""
        pos_snip = ""
        risk_snip = ""
        try:
            connector = getattr(self.runner, "conn", None) if self.runner else None
            report = collect_day_report(self.cfg, self.runner, connector)
            report_snip = format_structured_report(report, max_lines=25)[:1500]
        except Exception:
            pass
        if self.runner:
            try:
                intel = self.runner.commander_positions_intel()
                pos_snip = format_positions_report(intel, max_positions=6)[:800]
                risk_snip = format_risk_report(self.runner.commander_risk_summary())[:400]
            except Exception:
                pass

        draft = self._think(
            f"Commander said: {text}\n\nLIVE:\n{json.dumps(ctx, default=str)}\n"
            f"POSITIONS:\n{pos_snip}\nRISK:\n{risk_snip}\nACTIVITY:\n{report_snip}"
        )
        fallback = draft or f"Received — noted: {text[:200]}"
        self.send_ai(
            chat_id,
            "commander_chat",
            {
                "commander_message": text,
                "live_state": ctx,
                "positions_excerpt": pos_snip,
                "risk_excerpt": risk_snip,
                "activity_excerpt": report_snip,
                "draft_reply": draft,
            },
            fallback,
            reply_to=reply_id,
            sync=True,
        )
        threading.Thread(
            target=self._learning_worker,
            args=(chat_id, text, None),
            daemon=True,
        ).start()

    def _vision_reply(self, chat_id: int, caption: str, image_bytes: bytes, reply_id: Optional[int]) -> None:
        try:
            analysis = self.vision_fn(caption, image_bytes) if self.vision_fn else ""
        except Exception as exc:
            analysis = f"Vision analysis error: {exc}"
        self.send_ai(
            chat_id,
            "vision_analysis",
            {"caption": caption[:500], "analysis_draft": analysis[:1500]},
            analysis or "Could not analyze image.",
            reply_to=reply_id,
            sync=True,
        )
        note = f"[vision] {caption[:200]} → {analysis[:300]}"
        self._store_guidance(chat_id, note)
        threading.Thread(
            target=self._learning_worker,
            args=(chat_id, note, None),
            daemon=True,
        ).start()

    def _improve_worker(self, chat_id: int, trigger: str, reply_id: Optional[int]) -> None:
        try:
            result = run_commander_learning_cycle(
                self.cfg,
                self.runner,
                think_fn=self._think,
                trigger=trigger,
                apply=True,
            )
            self.send_ai(
                chat_id,
                "improve_result",
                {"learning_result": result},
                format_apply_report(result),
                reply_to=reply_id,
                sync=True,
            )
        except Exception as exc:
            self.send_ai(chat_id, "error", {"error": str(exc)}, f"Improvement cycle failed: {exc}", reply_to=reply_id, sync=True)

    def _learning_worker(self, chat_id: int, trigger: str, reply_id: Optional[int]) -> None:
        """Background: absorb chat into guardrailed self-improvements."""
        try:
            maybe_auto_apply_from_chat(self.cfg, self.runner, trigger, self._think)
            if reply_id is not None:
                result = run_commander_learning_cycle(
                    self.cfg,
                    self.runner,
                    think_fn=self._think,
                    trigger=trigger,
                    apply=True,
                )
                self.send_ai(
                    chat_id,
                    "improve_result",
                    {"learning_result": result, "trigger": trigger[:200]},
                    format_apply_report(result),
                    reply_to=reply_id,
                    sync=True,
                )
        except Exception as exc:
            log.debug(f"Learning worker: {exc}")
