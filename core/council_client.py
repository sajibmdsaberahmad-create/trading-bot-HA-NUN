#!/usr/bin/env python3
"""
core/council_client.py — Cloud LLM council (Groq primary, Gemini fallback).

Replaces local Ollama for live trading decisions, notifications, and chart vision.
Uses stdlib HTTP only — no extra SDK required.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.human_cognition import get_system_prompt
from core.notify import log


class CouncilClient:
    """Groq + Google Gemini HTTP client for HANOON council."""

    GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._last_call_time = 0.0
        self._call_count = 0
        self._error_count = 0
        self._groq_429_until = 0.0
        self._gemini_429_until = 0.0
        self._system_prompt = get_system_prompt(cfg)

    # ── Config helpers ────────────────────────────────────────────────────

    def enabled(self) -> bool:
        if not getattr(self.cfg, "COUNCIL_ENABLED", True):
            return False
        if not getattr(self.cfg, "GENERATIVE_THINKING_ENABLED", True):
            return False
        return bool(self.groq_key() or self.gemini_key())

    def groq_key(self) -> str:
        return (getattr(self.cfg, "GROQ_API_KEY", "") or "").strip()

    def gemini_key(self) -> str:
        return (
            getattr(self.cfg, "GEMINI_API_KEY", "")
            or getattr(self.cfg, "GOOGLE_API_KEY", "")
            or ""
        ).strip()

    def vision_available(self) -> bool:
        if not getattr(self.cfg, "LIVE_CHART_VISION_ENABLED", False):
            opportunistic = getattr(self.cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False)
            if not opportunistic:
                return False
        return bool(self.gemini_key())

    def _timeout(self, *, notify: bool = False) -> int:
        if notify:
            return int(getattr(self.cfg, "COUNCIL_NOTIFY_TIMEOUT_SEC", 12))
        return int(getattr(self.cfg, "COUNCIL_TIMEOUT_SEC", 12))

    def _max_tokens(self, *, notify: bool = False) -> int:
        if notify:
            return int(getattr(self.cfg, "COUNCIL_NOTIFY_MAX_TOKENS", 120))
        return int(getattr(self.cfg, "COUNCIL_MAX_TOKENS", 384))

    def _temperature(self) -> float:
        return float(getattr(self.cfg, "COUNCIL_TEMPERATURE", 0.55))

    def _min_interval(self) -> float:
        return float(getattr(self.cfg, "COUNCIL_MIN_CALL_INTERVAL_SEC", 0.5))

    def _groq_model(self, *, fast: bool = False) -> str:
        if fast:
            return getattr(self.cfg, "GROQ_MODEL_FAST", "llama-3.1-8b-instant")
        return getattr(self.cfg, "GROQ_MODEL", "llama-3.3-70b-versatile")

    def _gemini_model(self, *, vision: bool = False) -> str:
        if vision:
            return getattr(self.cfg, "GEMINI_VISION_MODEL", "gemini-2.5-flash")
        return getattr(self.cfg, "GEMINI_MODEL", "gemini-2.5-flash")

    # ── Public API (OllamaBrain-compatible) ─────────────────────────────

    def decide_call(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[str]:
        """Priority path for live entry/exit/position council."""
        if not self.enabled():
            return None
        from core.council_budget import (
            PURPOSE_DECISION,
            record_council_api_call,
            should_use_council_api,
        )
        ok, reason = should_use_council_api(self.cfg, PURPOSE_DECISION)
        if not ok:
            log.debug(f"Council decide skipped: {reason}")
            return None
        text = self._complete(
            prompt, system=system, model=model, priority=True, purpose=PURPOSE_DECISION,
        )
        if text:
            record_council_api_call(PURPOSE_DECISION)
        return text

    def compose_notification(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        purpose: str = "notify",
        event_type: Optional[str] = None,
        copilot: bool = False,
    ) -> Optional[str]:
        if not self.enabled():
            return None
        from core.council_budget import record_council_api_call, should_use_council_api
        ok, reason = should_use_council_api(
            self.cfg, purpose, event_type=event_type, copilot=copilot,
        )
        if not ok:
            log.debug(f"Council notify skipped ({event_type or purpose}): {reason}")
            return None
        notify_system = system or (
            "You are HANOON — an autonomous AI trading pilot briefing your commander on Telegram. "
            "Write short, sharp, analytical messages with exact numbers. First-person pilot voice. "
            "Sound alive and intentional — never robotic canned templates."
        )
        text = self._complete(
            prompt,
            system=notify_system,
            priority=False,
            notify=True,
            fast=True,
            purpose=purpose,
        )
        if text:
            record_council_api_call(purpose)
        return text

    def daily_digest_call(self, prompt: str, *, day_str: str = "") -> Optional[str]:
        """Single end-of-day statement — one API call per day after close."""
        if not self.enabled():
            return None
        from core.council_budget import (
            PURPOSE_DAILY_DIGEST,
            claim_daily_digest_slot,
            record_council_api_call,
            should_use_council_api,
        )
        ok, reason = should_use_council_api(self.cfg, PURPOSE_DAILY_DIGEST)
        if not ok:
            log.debug(f"Daily digest skipped: {reason}")
            return None
        if day_str:
            ok_slot, slot_reason = claim_daily_digest_slot(self.cfg, day_str)
            if not ok_slot:
                log.debug(f"Daily digest slot: {slot_reason}")
                return None
        system = (
            "You are HANOON trading pilot AI. Write one end-of-day self-evaluation "
            "for your commander: headline P&L, what happened, what you learned, "
            "what improves tomorrow. First-person, exact numbers, plain text."
        )
        text = self._complete(
            prompt,
            system=system,
            priority=False,
            notify=False,
            fast=False,
            purpose=PURPOSE_DAILY_DIGEST,
        )
        if text:
            record_council_api_call(PURPOSE_DAILY_DIGEST)
        return text

    def analyze_image(
        self,
        prompt: str,
        image_bytes: bytes,
        system: Optional[str] = None,
        *,
        trading_context: bool = False,
    ) -> Optional[str]:
        if not image_bytes or not self.gemini_key():
            return None
        from core.council_budget import (
            PURPOSE_DECISION,
            PURPOSE_NOTIFY,
            record_council_api_call,
            should_use_council_api,
        )
        purpose = PURPOSE_DECISION if trading_context else PURPOSE_NOTIFY
        if not trading_context and not getattr(self.cfg, "COUNCIL_CHART_VISION_API_ENABLED", False):
            log.debug("Chart vision API disabled — use council entry context or enable flag")
            return None
        ok, reason = should_use_council_api(self.cfg, purpose, force=trading_context)
        if not ok:
            log.debug(f"Council vision skipped: {reason}")
            return None
        use_system = system or (
            "You are HANOON trading pilot AI reviewing an intraday scalp chart. "
            "Identify setup, trend, volume clues, risk levels, and concrete improvements. "
            "Be specific and concise."
        )
        full_prompt = f"{use_system}\n\n{prompt}" if use_system else prompt
        text = self._gemini_vision(full_prompt, image_bytes)
        if text:
            record_council_api_call(purpose)
        return text

    def health_check(self) -> Dict[str, Any]:
        if not self.enabled():
            return {
                "status": "disabled",
                "groq": bool(self.groq_key()),
                "gemini": bool(self.gemini_key()),
            }
        providers = []
        if self.groq_key():
            providers.append(f"groq:{self._groq_model()}")
        if self.gemini_key():
            providers.append(f"gemini:{self._gemini_model()}")
        return {
            "status": "healthy",
            "providers": providers,
            "calls": self._call_count,
            "errors": self._error_count,
        }

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _rate_ok(self, *, priority: bool) -> bool:
        if priority:
            return True
        elapsed = time.time() - self._last_call_time
        if self._last_call_time > 0 and elapsed < self._min_interval():
            return False
        return True

    def _complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        priority: bool = False,
        notify: bool = False,
        fast: bool = False,
        purpose: str = "decision",
    ) -> Optional[str]:
        if not self._rate_ok(priority=priority):
            return None

        use_system = system or self._system_prompt
        backends = self._backend_order()
        errors: List[str] = []

        for backend in backends:
            if backend == "groq" and not self.groq_key():
                continue
            if backend == "gemini" and not self.gemini_key():
                continue
            if backend == "groq" and time.time() < self._groq_429_until:
                continue
            if backend == "gemini" and time.time() < self._gemini_429_until:
                continue

            groq_model = model or self._groq_model(fast=fast)
            text, err = (
                self._groq_chat(prompt, use_system, groq_model, notify=notify)
                if backend == "groq"
                else self._gemini_chat(prompt, use_system, notify=notify)
            )
            if text:
                if not priority:
                    self._last_call_time = time.time()
                self._call_count += 1
                return text
            if err:
                errors.append(f"{backend}:{err}")

        if errors:
            log.debug(f"Council all backends failed: {' | '.join(errors[:3])}")
        return None

    def _backend_order(self) -> List[str]:
        mode = str(getattr(self.cfg, "COUNCIL_BACKEND", "groq")).lower().strip()
        if mode == "gemini":
            return ["gemini", "groq"]
        if mode == "groq":
            order = ["groq", "gemini"]
        else:
            order = ["groq", "gemini"]
        # Skip providers in 429 cooldown
        now = time.time()
        filtered = []
        for b in order:
            if b == "groq" and self.groq_key() and now < self._groq_429_until:
                continue
            if b == "gemini" and self.gemini_key() and now < self._gemini_429_until:
                continue
            filtered.append(b)
        return filtered or order

    def _groq_chat(
        self,
        prompt: str,
        system: str,
        model: str,
        *,
        notify: bool = False,
    ) -> Tuple[Optional[str], str]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature(),
            "max_tokens": self._max_tokens(notify=notify),
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.groq_key()}",
        }
        try:
            start = time.time()
            body = self._post_json(self.GROQ_URL, payload, headers, self._timeout(notify=notify))
            elapsed = (time.time() - start) * 1000
            text = self._extract_openai_text(body)
            if text:
                log.debug(f"Groq {model}: {elapsed:.0f}ms | {len(text)} chars")
                return text, ""
            return None, "empty_response"
        except urllib.error.HTTPError as exc:
            self._error_count += 1
            if exc.code == 429:
                self._groq_429_until = time.time() + 30.0
                log.warning("Groq rate limit (429) — falling back to Gemini")
                return None, "429"
            return None, f"http_{exc.code}"
        except Exception as exc:
            self._error_count += 1
            return None, str(exc)[:80]

    def _gemini_chat(
        self,
        prompt: str,
        system: str,
        *,
        notify: bool = False,
    ) -> Tuple[Optional[str], str]:
        model = self._gemini_model()
        url = f"{self.GEMINI_BASE}/{model}:generateContent?key={self.gemini_key()}"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self._temperature(),
                "maxOutputTokens": self._max_tokens(notify=notify),
            },
        }
        headers = {"Content-Type": "application/json"}
        try:
            start = time.time()
            body = self._post_json(url, payload, headers, self._timeout(notify=notify))
            elapsed = (time.time() - start) * 1000
            text = self._extract_gemini_text(body)
            if text:
                log.debug(f"Gemini {model}: {elapsed:.0f}ms | {len(text)} chars")
                return text, ""
            return None, "empty_response"
        except urllib.error.HTTPError as exc:
            self._error_count += 1
            if exc.code == 429:
                self._gemini_429_until = time.time() + 45.0
                log.warning("Gemini rate limit (429)")
                return None, "429"
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            log.warning(f"Gemini HTTP {exc.code}: {detail}")
            return None, f"http_{exc.code}"
        except Exception as exc:
            self._error_count += 1
            return None, str(exc)[:80]

    def _gemini_vision(self, prompt: str, image_bytes: bytes) -> Optional[str]:
        model = self._gemini_model(vision=True)
        url = f"{self.GEMINI_BASE}/{model}:generateContent?key={self.gemini_key()}"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                ],
            }],
            "generationConfig": {
                "temperature": 0.35,
                "maxOutputTokens": int(getattr(self.cfg, "COUNCIL_VISION_MAX_TOKENS", 512)),
            },
        }
        headers = {"Content-Type": "application/json"}
        try:
            start = time.time()
            body = self._post_json(url, payload, headers, int(getattr(
                self.cfg, "COUNCIL_VISION_TIMEOUT_SEC", 25,
            )))
            elapsed = (time.time() - start) * 1000
            text = self._extract_gemini_text(body)
            if text:
                log.debug(f"Gemini vision {model}: {elapsed:.0f}ms")
                return text
        except Exception as exc:
            self._error_count += 1
            log.debug(f"Gemini vision failed: {exc}")
        return None

    @staticmethod
    def _post_json(
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout: int,
    ) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _extract_openai_text(body: Dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "").strip()

    @staticmethod
    def _extract_gemini_text(body: Dict[str, Any]) -> str:
        candidates = body.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        texts = [str(p.get("text", "")) for p in parts if p.get("text")]
        return "\n".join(texts).strip()


_client_cache: Optional[CouncilClient] = None


def get_council_client(cfg: BotConfig) -> CouncilClient:
    global _client_cache
    if _client_cache is None or _client_cache.cfg is not cfg:
        _client_cache = CouncilClient(cfg)
    return _client_cache
