"""Halim protocol — versioned API for any device, now and future."""

from __future__ import annotations

PROTOCOL_VERSION = "1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MODEL_NAME = "M. A. Halim"
SHORT_NAME = "Halim"

# Fast path: never route through server (microseconds)
REFLEX_COMPONENTS = ("ppo", "proxy", "scalper_weights")

# Slow path: optional server LM (milliseconds–seconds)
REASONING_COMPONENTS = ("halim_lm", "narrative", "council_text")

# Capability domains — learn by action, unlock by phase (see halim/capabilities.py)
CAPABILITY_IDS = (
    "trade_reflex",
    "enter_skip",
    "text_compose",
    "decision_text",
    "read_understand",
    "reasoning",
    "chart_read",
    "chat",
    "code_generate",
    "file_generate",
    "image_generate",
    "image_understand",
    "math_solve",
    "agent_orchestrate",
)

PHASES = ("newborn", "toddler", "child", "adult", "frontier")

# Halim is an ACTIVE model — learns and writes owned assets (never Ollama-style inference-only)
RUNTIME_TYPE = "active"
INFERENCE_ONLY_FORBIDDEN = True
