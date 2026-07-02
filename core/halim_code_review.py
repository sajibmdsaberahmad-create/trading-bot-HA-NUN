#!/usr/bin/env python3
"""
core/halim_code_review.py — API-assisted code review (Step 3).

Piggybacks on existing council API calls. When a review is requested
(usually after a drawdown rollback or significant self-tune change),
appends a code review block to the next available council call.

No dedicated API calls — reuses existing budget (1 call per 30s global).
If no council call happens within REVIEW_TIMEOUT_SEC, the review is skipped.

The review result is logged for human inspection.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

REVIEW_JOURNAL = Path("models/code_review_journal.jsonl")

_pending_review: Optional[Dict[str, Any]] = None
_pending_lock = threading.Lock()
_last_review_at: float = 0.0


def code_review_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("CODE_REVIEW_ENABLED", "true").lower() in ("1", "true", "yes")


def code_review_interval_sec(cfg: Optional[BotConfig] = None) -> float:
    """Minimum seconds between code reviews (don't spam API)."""
    return float(os.getenv("CODE_REVIEW_INTERVAL_SEC", "600"))  # 10 min default


def request_review(context: str, meta: Optional[Dict] = None) -> bool:
    """
    Queue a code review request. Returns True if queued.
    The review fires on the next available council call.
    """
    if not code_review_enabled():
        return False
    global _pending_review
    with _pending_lock:
        _pending_review = {
            "context": context[:800],
            "meta": meta or {},
            "requested_at": time.time(),
        }
    return True


def _consume_pending() -> Optional[Dict[str, Any]]:
    """Get and clear the pending review (thread-safe)."""
    global _pending_review
    with _pending_lock:
        if _pending_review is None:
            return None
        r = _pending_review
        _pending_review = None
        return r


def _journal(text: str, context: str) -> None:
    try:
        REVIEW_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "review": text[:2000],
            "context": context[:500],
        }
        with open(REVIEW_JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def try_review(cfg: BotConfig) -> bool:
    """
    Try to fire a pending code review via council API.
    Returns True if a review was attempted (even if API failed).

    Called by the periodic loop or on relevant events.
    """
    global _last_review_at

    if not code_review_enabled(cfg):
        return False

    pending = _consume_pending()
    if pending is None:
        return False

    now = time.time()
    if now - _last_review_at < code_review_interval_sec(cfg):
        # Re-queue — not enough time has passed
        request_review(pending["context"], pending.get("meta"))
        return False
    _last_review_at = now

    context = pending["context"]

    try:
        from core.council_client import get_council_client

        prompt = (
            f"[CODE REVIEW REQUEST]\n"
            f"Context: {context}\n\n"
            f"Review this change for correctness, safety, and alignment "
            f"with production trading. Identify potential issues. "
            f"Keep analysis under 5 sentences. Begin with APPROVED or FLAGGED."
        )
        client = get_council_client(cfg)
        if client and client.enabled():
            text = client.decide_call(
                prompt,
                system=(
                    "You are a senior code reviewer for an autonomous trading system. "
                    "Review code changes for: 1) correctness, 2) safety (will this lose money?), "
                    "3) alignment with trading logic. Be concise. "
                    "Output: APPROVED or FLAGGED followed by 1-2 sentence rationale."
                ),
            )
            if text:
                text = text.strip()
                verdict = "APPROVED" if "APPROVED" in text.upper() else "FLAGGED"
                log.info(f"📝 Code review: {verdict} — {text[:200]}")
                _journal(text, context)
                return True
        log.debug("Code review: council unavailable")
    except Exception as exc:
        log.debug(f"Code review: {exc}")

    return False
