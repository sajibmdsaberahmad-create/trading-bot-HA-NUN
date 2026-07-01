#!/usr/bin/env python3
"""Sanity checks for cloud/local LLM responses on structured trading tasks."""

from __future__ import annotations


def cjk_char_ratio(text: str) -> float:
    """Share of CJK characters — detects Chinese/Japanese drift in English prompts."""
    if not text:
        return 0.0
    cjk = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF:
            cjk += 1
    return cjk / max(len(text), 1)


def response_looks_english(text: str, *, max_cjk_ratio: float = 0.05) -> bool:
    return cjk_char_ratio(text) <= max_cjk_ratio


def sanitize_log_text(text: str, max_len: int = 200) -> str:
    """ASCII-safe one-liner for logs when LLM output is unusable."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if not response_looks_english(raw):
        return "[non-English LLM response discarded]"
    return raw[:max_len]
