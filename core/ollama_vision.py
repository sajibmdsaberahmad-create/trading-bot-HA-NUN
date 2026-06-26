#!/usr/bin/env python3
"""
core/ollama_vision.py — DEPRECATED shim (local llava removed).

Chart vision uses Gemini multimodal via council_client.
"""

from __future__ import annotations

from core.config import BotConfig
from core.council_client import get_council_client


def vision_model_name(cfg: BotConfig) -> str:
    return getattr(cfg, "GEMINI_VISION_MODEL", "gemini-2.5-flash")


def is_vision_model_present(cfg: BotConfig, model: str = "") -> bool:
    return get_council_client(cfg).vision_available()


def resolve_vision_model(cfg: BotConfig) -> str:
    return vision_model_name(cfg)


def ensure_vision_model(cfg: BotConfig) -> str:
    return vision_model_name(cfg)


def prepare_for_vision_call(cfg: BotConfig) -> None:
    """No-op — cloud vision needs no local model swap."""


def stop_vision_model(cfg: BotConfig) -> None:
    """No-op — cloud vision needs no unload."""


def installed_vision_tag(cfg: BotConfig, model: str) -> str:
    return model or vision_model_name(cfg)
