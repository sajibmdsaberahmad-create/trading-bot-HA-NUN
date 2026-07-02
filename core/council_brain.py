#!/usr/bin/env python3
"""
core/council_brain.py — Cloud council brain (Groq + Gemini).

Drop-in replacement for the former local LLM layer.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.config import BotConfig
from core.council_client import CouncilClient, get_council_client
from core.notify import log


class CouncilBrain:
    """Facade used by CognitiveCore, AICommander, and scalper startup."""

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._client = get_council_client(cfg)

    @property
    def config(self):
        """Legacy shim — some callers read .config.enabled."""
        return self

    @property
    def enabled(self) -> bool:
        return self._client.enabled()

    def decide_call(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[str]:
        return self._client.decide_call(prompt, system=system, model=model)

    def compose_notification(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        purpose: str = "notify",
        event_type: Optional[str] = None,
        copilot: bool = False,
    ) -> Optional[str]:
        return self._client.compose_notification(
            prompt,
            system=system,
            purpose=purpose,
            event_type=event_type,
            copilot=copilot,
        )

    def analyze_image(
        self,
        prompt: str,
        image_bytes: bytes,
        system: Optional[str] = None,
        *,
        trading_context: bool = False,
    ) -> Optional[str]:
        return self._client.analyze_image(
            prompt, image_bytes, system=system, trading_context=trading_context,
        )

    def think(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        return self.decide_call(prompt, system=system)

    def health_check(self) -> Dict[str, Any]:
        return self._client.health_check()

    def explain_decision(self, *args, **kwargs) -> str:
        prompt = kwargs.get("prompt") or (args[0] if args else "")
        return self.decide_call(str(prompt)) or ""

    def summarize_journal(self, trades: list) -> str:
        if not trades:
            return "No trades to summarize."
        lines = []
        for t in trades[-20:]:
            if isinstance(t, dict):
                lines.append(
                    f"{t.get('ticker', '?')} pnl={t.get('pnl_usd', t.get('pnl', 0))}"
                )
        prompt = (
            "Summarize this intraday scalp session in 3-5 bullet points for the pilot journal.\n"
            + "\n".join(lines)
        )
        return self.decide_call(prompt) or "Journal summary unavailable."


def create_council_brain(cfg: BotConfig) -> Optional[CouncilBrain]:
    if not getattr(cfg, "COUNCIL_ENABLED", True):
        log.info("Council brain disabled")
        return None
    brain = CouncilBrain(cfg)
    health = brain.health_check()
    if health.get("status") != "healthy":
        log.warning(f"⚠️ Council brain not ready: {health}")
        return None
    providers = health.get("providers") or []
    log.info(
        f"✅ Council brain active | backend={getattr(cfg, 'COUNCIL_BACKEND', 'groq')} | "
        f"providers={', '.join(providers) or 'none'}"
    )
    return brain

