#!/usr/bin/env python3
"""
core/ollama_models.py — DEPRECATED shim (local Ollama removed).
"""

from __future__ import annotations

from core.config import BotConfig


def sync_text_model(cfg: BotConfig) -> str:
    return getattr(cfg, "GROQ_MODEL", "llama-3.3-70b-versatile")


def ensure_text_model(cfg: BotConfig) -> str:
    return sync_text_model(cfg)


def resolve_text_model(cfg: BotConfig) -> str:
    return sync_text_model(cfg)


def active_text_model(cfg: BotConfig) -> str:
    return sync_text_model(cfg)


def installed_model_tag(cfg: BotConfig, model: str) -> str:
    return model or sync_text_model(cfg)


def text_model_startup_warnings(cfg: BotConfig) -> list:
    warnings = []
    if not getattr(cfg, "GROQ_API_KEY", "") and not getattr(cfg, "GEMINI_API_KEY", ""):
        warnings.append("No GROQ_API_KEY or GEMINI_API_KEY — council disabled")
    return warnings
