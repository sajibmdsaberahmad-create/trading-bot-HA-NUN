#!/usr/bin/env python3
"""
Standalone Halim Telegram chat — works without HANOON/replay running.

Double-click START_HALIM.command or: PYTHONPATH=. python core/halim_telegram_standalone.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_halim = ROOT / "halim"
if _halim.is_dir() and str(_halim) not in sys.path:
    sys.path.insert(0, str(_halim))

from core.config import BotConfig
from core.notify import log
from core.telegram_auth import is_verified, secret_configured, verify_phrase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class HalimTelegramBot:
    """Lightweight Telegram poller — Halim chat only (no trading commands)."""

    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self._token = (
            getattr(cfg, "TELEGRAM_BOT_TOKEN", "")
            or os.getenv("TRADING_BOT_TELEGRAM_TOKEN", "")
            or os.getenv("TELEGRAM_BOT_TOKEN", "")
        ).strip()
        if not self._token:
            raise RuntimeError("TRADING_BOT_TELEGRAM_TOKEN not set")
        self._poll_sec = float(getattr(cfg, "TELEGRAM_POLL_INTERVAL_SEC", 1.5))
        self._offset = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._maint_thread: Optional[threading.Thread] = None

    def _maintenance_loop(self) -> None:
        """Standalone: learn, export gold, maybe auto-retrain LM (off-hours)."""
        interval = float(os.getenv("HALIM_STANDALONE_MAINT_SEC", "7200"))
        while not self._stop.wait(interval):
            if self._stop.is_set():
                break
            try:
                from core.halim_web_learn import fetch_wikipedia_summary
                topics = os.getenv(
                    "HALIM_LEARN_TOPICS",
                    "wiki:Stock_market,wiki:Algorithmic_trading,wiki:Volatility",
                ).split(",")
                topic = topics[int(time.time()) % max(1, len(topics))].strip()
                if topic.startswith("wiki:"):
                    fetch_wikipedia_summary(topic[5:], self.cfg)
                from core.halim_action_learn import export_action_gold
                from core.halim_auto_lm import schedule_auto_retrain
                r = export_action_gold(include_learn_cache=True)
                sched = schedule_auto_retrain(r, self.cfg, trigger="standalone_maint")
                if sched.get("scheduled"):
                    log.info("🧠 Halim standalone: auto-LM retrain scheduled")
            except Exception as exc:
                log.debug(f"Halim standalone maintenance: {exc}")

    def start_maintenance(self) -> None:
        if os.getenv("HALIM_STANDALONE_MAINT", "true").lower() not in ("1", "true", "yes"):
            return
        self._maint_thread = threading.Thread(
            target=self._maintenance_loop,
            name="halim-standalone-maint",
            daemon=True,
        )
        self._maint_thread.start()

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
        chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
        for chunk in chunks:
            params: Dict[str, Any] = {"chat_id": str(chat_id), "text": chunk}
            if reply_to:
                params["reply_to_message_id"] = reply_to
            try:
                self._api("sendMessage", params, timeout=15)
            except Exception as exc:
                log.warning(f"Halim Telegram send failed: {exc}")

    def start(self) -> None:
        try:
            me = self._api("getMe", timeout=10)
            if me.get("ok"):
                name = me.get("result", {}).get("username", "?")
                log.info(f"Halim Telegram bot connected: @{name}")
            else:
                log.warning(f"Telegram getMe failed: {me}")
        except Exception as exc:
            log.warning(f"Telegram token check failed: {exc}")
        self._thread = threading.Thread(target=self._poll_loop, name="halim-tg", daemon=False)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def run_forever(self) -> None:
        self.start()
        self.start_maintenance()
        try:
            while not self._stop.is_set():
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self._api(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": 25,
                        "allowed_updates": json.dumps(["message"]),
                    },
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

        if not is_verified(self.cfg, chat_id):
            self._handle_unverified(chat_id, text, username, first_name, reply_id)
            return

        if text.startswith("/"):
            self._handle_command(chat_id, text, reply_id)
            return

        if text:
            log.info(f"Halim TG chat={chat_id} @{username or first_name}: {text[:80]!r}")
            self._handle_chat(chat_id, text, reply_id)

    def _handle_unverified(
        self,
        chat_id: int,
        text: str,
        username: str,
        first_name: str,
        reply_id: Optional[int],
    ) -> None:
        if not secret_configured(self.cfg):
            if text.lower().startswith(("/start", "/help")):
                self.send(
                    chat_id,
                    "🔒 Halim chat is locked.\n"
                    "Set TRADING_BOT_TELEGRAM_VERIFY_SECRET on the host, then restart.",
                    reply_to=reply_id,
                )
            return

        low = text.lower().strip()
        if low.startswith("/start") or low.startswith("/help"):
            self.send(
                chat_id,
                "🧠 M. A. Halim — Personal OS companion\n\n"
                "1. Verify from any Telegram account:\n"
                "   /verify hall of fame\n\n"
                "2. Then chat freely — Halim runs even when trading is off.\n\n"
                "Commands: /halim · /status · /help",
                reply_to=reply_id,
            )
            return

        if low.startswith("/verify"):
            parts = text.split(maxsplit=1)
            phrase = parts[1].strip() if len(parts) > 1 else ""
            if verify_phrase(self.cfg, chat_id, phrase, username=username, first_name=first_name):
                self.send(
                    chat_id,
                    "✅ Verified — Halim chat unlocked.\n"
                    "Say hello, or try /status · /halim",
                    reply_to=reply_id,
                )
            else:
                self.send(
                    chat_id,
                    "🔒 Verification failed.\n"
                    "Send: /verify hall of fame\n"
                    "(use your configured secret phrase)",
                    reply_to=reply_id,
                )

    def _handle_command(self, chat_id: int, text: str, reply_id: Optional[int]) -> None:
        cmd = text.split()[0].lower().split("@")[0]
        if cmd in ("/start", "/help"):
            self.send(
                chat_id,
                "🧠 Halim standalone chat\n\n"
                "/halim — who is Halim\n"
                "/status — brain + server health\n"
                "/help — this message\n\n"
                "Or send any message — Halim replies via toddler LM + council teacher.",
                reply_to=reply_id,
            )
        elif cmd == "/halim":
            self._handle_chat(
                chat_id,
                "Introduce yourself briefly as M. A. Halim — my personal AI companion.",
                reply_id,
            )
        elif cmd == "/status":
            self.send(chat_id, self._format_status(), reply_to=reply_id)
        else:
            self.send(chat_id, f"Unknown command {cmd}. Try /help", reply_to=reply_id)

    def _format_status(self) -> str:
        lines = ["🧠 Halim status"]
        try:
            from halim.client import health, status as remote_status

            url = os.getenv("HALIM_SERVER_URL", "http://127.0.0.1:8765")
            if health(url, timeout=1.0):
                st = remote_status(url, timeout=2.0) or {}
                phase = st.get("phase") or st.get("halim_phase") or "?"
                backend = st.get("lm_backend") or st.get("backend") or "?"
                lines.append(f"Server: ✅ {url}")
                lines.append(f"Phase: {phase} · backend: {backend}")
            else:
                lines.append("Server: ❌ not running (start START_HALIM.command)")
        except Exception as exc:
            lines.append(f"Server: ? ({exc})")
        try:
            from halim.engine import collect_status

            inline = collect_status()
            mode = inline.get("mode") or inline.get("phase") or "active"
            lines.append(f"Inline engine: {mode}")
        except Exception:
            pass
        lines.append("Trading algo: off (standalone Halim chat)")
        return "\n".join(lines)

    def _handle_chat(self, chat_id: int, text: str, reply_id: Optional[int]) -> None:
        self.send(chat_id, "🧠 Halim is thinking…", reply_to=reply_id)

        def work() -> None:
            try:
                from core.halim_chat import halim_chat

                r = halim_chat(text, purpose="commander_chat", cfg=self.cfg)
                out = (r.get("text") or "").strip()
                if not out:
                    out = "Halim couldn't reply right now — is serve running? Check logs/halim_serve.log"
                src = r.get("source", "")
                if src and src not in ("companion", "halim_native"):
                    out = f"{out}\n\n— via {src}"
                self.send(chat_id, out)
            except Exception as exc:
                log.warning(f"Halim chat error: {exc}")
                self.send(chat_id, f"Halim error: {exc}")

        threading.Thread(target=work, name="halim-chat", daemon=True).start()


def main() -> None:
    try:
        from core.env_secrets import bootstrap_env

        bootstrap_env(str(ROOT))
    except Exception:
        pass
    if (ROOT / ".env").is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env")
        except ImportError:
            pass

    cfg = BotConfig()
    log.info("Halim standalone Telegram — waiting for messages (Ctrl+C to stop)")
    HalimTelegramBot(cfg).run_forever()


if __name__ == "__main__":
    main()
