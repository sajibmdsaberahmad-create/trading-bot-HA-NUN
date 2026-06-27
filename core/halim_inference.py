#!/usr/bin/env python3
"""
core/halim_inference.py — Bridge to Halim server (optional, non-blocking).

Design:
  • REFLEX (PPO, proxy, weights) — always inline in HANOON, never HTTP
  • REASONING (future LM) — optional halim serve, short timeout, fallback to council/API
  • Server off or slow? Trading continues unchanged
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig
from core.notify import log

_halim_path_done = False


def _ensure_halim_package() -> bool:
    global _halim_path_done
    if _halim_path_done:
        return True
    root = Path(__file__).resolve().parents[1] / "halim"
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _halim_path_done = True
    try:
        import halim.engine  # noqa: F401
        return True
    except ImportError:
        return False


def inference_mode(cfg: Optional[BotConfig] = None) -> str:
    """inline | server | hybrid"""
    if not _ensure_halim_package():
        return "inline"
    try:
        from halim.client import server_url
        from halim.engine import reasoning_available

        if server_url() and reasoning_available():
            return "hybrid"
        if server_url():
            return "server"
    except Exception:
        pass
    return "inline"


def local_status(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    if not _ensure_halim_package():
        return {"ok": False, "reason": "halim_package_missing"}
    try:
        from halim.engine import collect_status
        os.environ.setdefault("HALIM_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
        return collect_status()
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:120]}


def try_reasoning_complete(
    prompt: str,
    *,
    purpose: str = "reasoning",
    cfg: Optional[BotConfig] = None,
) -> Tuple[Optional[str], str]:
    """
    Optional slow-path completion via Halim server.
    Returns (text_or_none, source) where source is halim_server | unavailable.
    Never raises; timeout HALIM_INFERENCE_TIMEOUT_SEC (chat ~90s, other ~30s).
    """
    if os.getenv("HALIM_REASONING_VIA_SERVER", "auto").lower() in ("0", "false", "off"):
        return None, "disabled"

    if not _ensure_halim_package():
        return None, "unavailable"

    chat_purposes = frozenset({
        "chat", "commander_chat", "dialogue", "companion", "copilot",
    })
    default_timeout = float(os.getenv("HALIM_INFERENCE_TIMEOUT_SEC", "90"))
    if purpose in chat_purposes:
        timeout = float(os.getenv("HALIM_CHAT_INFERENCE_TIMEOUT_SEC", str(default_timeout)))
    else:
        timeout = float(os.getenv("HALIM_REASONING_TIMEOUT_SEC", str(min(default_timeout, 30.0))))

    try:
        from halim.client import complete, server_url

        if os.getenv("HALIM_REASONING_VIA_SERVER", "auto").lower() == "auto" and not server_url():
            inline = _try_inline_lm(prompt, purpose=purpose)
            if inline[0]:
                return inline

        url = server_url() or os.getenv("HALIM_SERVER_URL")
        if not url:
            inline = _try_inline_lm(prompt, purpose=purpose)
            if inline[0]:
                return inline
            return None, "unavailable"

        out = complete(prompt, purpose=purpose, timeout=timeout)
        if out and out.get("ok") and out.get("text"):
            return str(out["text"]), "halim_server"
    except Exception as exc:
        log.debug(f"Halim server complete: {exc}")

    if os.getenv("HALIM_INLINE_LM_FALLBACK", "true").lower() in ("1", "true", "yes"):
        inline = _try_inline_lm(prompt, purpose=purpose)
        if inline[0]:
            return inline

    return None, "unavailable"


def _try_inline_lm(prompt: str, *, purpose: str = "reasoning") -> Tuple[Optional[str], str]:
    """Load toddler LM in-process when serve is down."""
    try:
        from halim.engine import complete_reasoning, reasoning_available

        os.environ.setdefault("HALIM_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
        if not reasoning_available():
            return None, "unavailable"
        result = complete_reasoning(prompt, purpose=purpose)
        if result.get("ok") and result.get("text"):
            return str(result["text"]), str(result.get("source", "halim_lm"))
    except Exception as exc:
        log.debug(f"Halim inline LM: {exc}")
    return None, "unavailable"


def log_inference_banner(cfg: Optional[BotConfig] = None) -> None:
    """Startup line — does not block."""
    mode = inference_mode(cfg)
    st = local_status(cfg)
    phase = st.get("phase", "?")
    pairs = st.get("dataset_pairs", 0)
    prof = (st.get("device_profile") or {}).get("profile", "?")
    reasoning = (st.get("reasoning") or {}).get("enabled", False)

    if mode == "inline":
        log.info(
            f"  Halim engine: inline reflex (PPO+proxy) · phase={phase} · "
            f"dataset={pairs} · device={prof} · server=off (optional later)"
        )
    else:
        log.info(
            f"  Halim engine: {mode} · phase={phase} · dataset={pairs} · "
            f"reasoning_lm={reasoning} · server=up"
        )
