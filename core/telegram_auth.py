#!/usr/bin/env python3
"""
core/telegram_auth.py — Secret-phrase verification for inbound Telegram commands.

Any Telegram account may message the bot, but only chat IDs that have verified
with TRADING_BOT_TELEGRAM_VERIFY_SECRET can issue commands or receive briefings.
"""

from __future__ import annotations

import hmac
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

_lock = threading.Lock()


def _store_path(cfg: BotConfig) -> Path:
    return Path(getattr(cfg, "TELEGRAM_VERIFIED_STORE", "models/telegram_verified.json"))


def _load_store(cfg: BotConfig) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return {"verified": {}}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("verified"), dict):
            return data
    except Exception:
        pass
    return {"verified": {}}


def _save_store(cfg: BotConfig, data: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def verification_required(cfg: BotConfig) -> bool:
    """Inbound Telegram always requires verification when listening is enabled."""
    return True


def secret_configured(cfg: BotConfig) -> bool:
    return bool((getattr(cfg, "TELEGRAM_VERIFY_SECRET", "") or "").strip())


def is_verified(cfg: BotConfig, chat_id: int | str) -> bool:
    if not secret_configured(cfg):
        return False
    key = str(chat_id)
    with _lock:
        store = _load_store(cfg)
        rec = store.get("verified", {}).get(key)
    return bool(rec)


def verify_phrase(
    cfg: BotConfig,
    chat_id: int | str,
    phrase: str,
    *,
    username: str = "",
    first_name: str = "",
) -> bool:
    """Return True if phrase matches and chat is now verified."""
    secret = (getattr(cfg, "TELEGRAM_VERIFY_SECRET", "") or "").strip()
    if not secret:
        return False

    candidate = (phrase or "").strip()
    if candidate.lower().startswith("/verify"):
        parts = candidate.split(maxsplit=1)
        candidate = parts[1].strip() if len(parts) > 1 else ""

    if not candidate or not hmac.compare_digest(candidate, secret):
        return False

    key = str(chat_id)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        store = _load_store(cfg)
        verified = store.setdefault("verified", {})
        verified[key] = {
            "verified_at": now,
            "username": username or "",
            "first_name": first_name or "",
        }
        _save_store(cfg, store)
    log.info(
        f"Telegram verified chat_id={key} user=@{username or first_name or '?'} "
        f"(total commanders: {len(verified)})"
    )
    return True


def list_verified(cfg: BotConfig) -> List[Dict[str, Any]]:
    with _lock:
        store = _load_store(cfg)
        out = []
        for chat_id, rec in store.get("verified", {}).items():
            out.append({"chat_id": chat_id, **rec})
        return out


def outbound_chat_ids(cfg: BotConfig) -> set[str]:
    """
    Chat IDs that may receive outbound Telegram alerts.

    Default: verified commanders only (any account after /verify SECRET).
    Legacy single-chat mode: also includes TELEGRAM_CHAT_ID when
    TELEGRAM_VERIFIED_ONLY_OUTBOUND=false.
    """
    ids: set[str] = set()
    for rec in list_verified(cfg):
        cid = str(rec.get("chat_id", "")).strip()
        if cid:
            ids.add(cid)
    if getattr(cfg, "TELEGRAM_VERIFIED_ONLY_OUTBOUND", True):
        return ids
    legacy = (getattr(cfg, "TELEGRAM_CHAT_ID", "") or "").strip()
    if legacy:
        ids.add(legacy)
    return ids


def telegram_bot_ready(cfg: BotConfig) -> bool:
    """Bot token configured and Telegram notifications enabled."""
    return bool(
        getattr(cfg, "TELEGRAM_ENABLED", True)
        and (getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    )


def send_telegram_to_chat(token: str, chat_id: str | int, message: str, *, timeout: int = 15) -> bool:
    """Deliver one message chunk to a single chat ID."""
    if not token or not message or chat_id is None:
        return False
    import urllib.error
    import urllib.parse
    import urllib.request

    max_len = 4096
    try:
        for i in range(0, len(message), max_len):
            chunk = message[i:i + max_len]
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": str(chat_id),
                "text": chunk,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
        return True
    except Exception as exc:
        log.debug(f"Telegram send to {chat_id}: {exc}")
        return False


def fanout_telegram(cfg: BotConfig, message: str, *, token: Optional[str] = None) -> int:
    """Send message to every verified commander chat. Returns delivery count."""
    if not message or not telegram_bot_ready(cfg):
        return 0
    tok = (token or getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not tok:
        return 0
    targets = outbound_chat_ids(cfg)
    if not targets:
        return 0
    sent = 0
    for cid in targets:
        if send_telegram_to_chat(tok, cid, message):
            sent += 1
    return sent


def revoke_chat(cfg: BotConfig, chat_id: int | str) -> bool:
    key = str(chat_id)
    with _lock:
        store = _load_store(cfg)
        verified = store.get("verified", {})
        if key not in verified:
            return False
        del verified[key]
        _save_store(cfg, store)
    return True
