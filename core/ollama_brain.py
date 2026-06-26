#!/usr/bin/env python3
"""
core/ollama_brain.py — DEPRECATED shim.

Local Ollama was removed. Import from council_brain instead.
"""

from core.council_brain import (  # noqa: F401
    CouncilBrain,
    OllamaBrain,
    create_council_brain,
    create_ollama_brain,
)

__all__ = [
    "CouncilBrain",
    "OllamaBrain",
    "create_council_brain",
    "create_ollama_brain",
]
