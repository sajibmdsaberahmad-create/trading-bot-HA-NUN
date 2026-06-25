#!/usr/bin/env python3
"""
core/ollama_vision.py — Quantized llava / vision models for chart decisions.

Tier defaults:
  compact     → llava-phi3:3.8b (~3GB, fits 8GB sometimes)
  balanced    → llava:7b-v1.6-mistral-q4_K_M (Q4)
  standard+   → llava:7b-v1.6-mistral-q4_K_M or full llava
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.request
from typing import Optional

from core.config import BotConfig
from core.notify import log

_pull_lock = threading.Lock()
_pull_started = False

# Quantized llava variants — smallest first
VISION_MODEL_BY_TIER: dict[str, str] = {
    "compact": "llava-phi3:3.8b",
    "balanced": "llava:7b-v1.6-mistral-q4_K_M",
    "standard": "llava:7b-v1.6-mistral-q4_K_M",
    "performance": "llava:13b-v1.6-vicuna-q4_K_M",
}

VISION_FALLBACK_CHAIN = (
    "llava-phi3:3.8b",
    "llava:7b-v1.6-mistral-q4_K_M",
    "moondream",
    "llava",
    "llava:latest",
)


def resolve_vision_model(cfg: BotConfig) -> str:
    """Env override → RAM tier quantized default → first installed fallback."""
    explicit = (getattr(cfg, "OLLAMA_VISION_MODEL", "") or os.getenv("OLLAMA_VISION_MODEL", "") or "").strip()

    from core.ram_tier import detect_ram_tier

    tier = getattr(cfg, "RAM_TIER", "") or detect_ram_tier()
    tier_model = VISION_MODEL_BY_TIER.get(tier, "llava-phi3:3.8b")

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if tier_model not in candidates:
        candidates.append(tier_model)
    for c in VISION_FALLBACK_CHAIN:
        if c not in candidates:
            candidates.append(c)

    for candidate in candidates:
        if is_vision_model_present(cfg, candidate):
            return installed_vision_tag(cfg, candidate) or candidate

    return installed_vision_tag(cfg, explicit) or explicit or tier_model


def installed_vision_tag(cfg: BotConfig, model: str) -> Optional[str]:
    """Map llava → llava:latest for Ollama API."""
    target = (model or "").strip()
    if not target:
        return None
    installed = _list_models(cfg)
    full_tags = [n for n in installed if ":" in n]
    if target in full_tags:
        return target
    base = target.split(":")[0]
    for tag in full_tags:
        if tag.split(":")[0] == base:
            return tag
    return None


def vision_model_name(cfg: BotConfig) -> str:
    return resolve_vision_model(cfg)


def _list_models(cfg: BotConfig) -> list[str]:
    host = getattr(cfg, "OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = []
        for m in data.get("models", []):
            name = m.get("name", "")
            if name:
                names.append(name)
                names.append(name.split(":")[0])
        return names
    except Exception:
        pass

    if not shutil.which("ollama"):
        return []
    try:
        out = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0:
            return []
        names = []
        for line in out.stdout.splitlines()[1:]:
            part = line.split()[0] if line.strip() else ""
            if part:
                names.append(part)
                names.append(part.split(":")[0])
        return names
    except Exception:
        return []


def is_vision_model_present(cfg: BotConfig, model: Optional[str] = None) -> bool:
    target = (model or resolve_vision_model(cfg)).strip()
    base = target.split(":")[0]
    installed = _list_models(cfg)
    return any(
        n == target or n == base or n.startswith(f"{base}:")
        for n in installed
    )


def stop_vision_model(cfg: BotConfig, model: Optional[str] = None) -> None:
    """Unload vision model from RAM after one-shot read."""
    if not shutil.which("ollama"):
        return
    name = (model or resolve_vision_model(cfg)).strip()
    try:
        subprocess.run(
            ["ollama", "stop", name],
            capture_output=True,
            timeout=12,
        )
        log.debug(f"Vision model unloaded: {name}")
    except Exception as exc:
        log.debug(f"Vision stop {name}: {exc}")


def prepare_for_vision_call(cfg: BotConfig) -> None:
    """
    On 8GB, briefly unload the text LLM so quantized llava fits.
    Text model reloads automatically on the next council ring.
    """
    if not getattr(cfg, "OLLAMA_VISION_SWAP_TEXT_MODEL", True):
        return
    from core.ram_tier import detect_ram_tier

    if detect_ram_tier() not in ("compact", "balanced"):
        return
    if not shutil.which("ollama"):
        return
    try:
        from core.ollama_models import active_text_model

        text = active_text_model(cfg)
    except Exception:
        text = (getattr(cfg, "OLLAMA_MODEL", "") or "").strip()
    if not text:
        return
    try:
        subprocess.run(["ollama", "stop", text], capture_output=True, timeout=10)
        log.debug(f"Text model paused for vision slot: {text}")
    except Exception:
        pass


def ensure_vision_model(cfg: BotConfig, *, background: bool = True) -> bool:
    """Return True if vision model is installed (or pull was started)."""
    global _pull_started

    if not getattr(cfg, "OLLAMA_ENABLED", True):
        log.debug("Vision model check skipped — Ollama disabled")
        return False

    model = resolve_vision_model(cfg)
    if is_vision_model_present(cfg, model):
        log.info(f"✅ Ollama vision model ready: {model}")
        return True

    if not shutil.which("ollama"):
        log.warning(
            f"Vision model {model} not found and ollama CLI missing — "
            "chart review via Telegram will be limited"
        )
        return False

    with _pull_lock:
        if _pull_started:
            return False
        _pull_started = True

    def _pull():
        log.info(f"📥 Pulling quantized vision model {model}...")
        try:
            proc = subprocess.run(
                ["ollama", "pull", model],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if proc.returncode == 0 and is_vision_model_present(cfg, model):
                log.info(f"✅ Vision model {model} installed — chart decisions enabled")
            else:
                err = (proc.stderr or proc.stdout or "pull failed")[:300]
                log.warning(f"Vision model pull failed for {model}: {err}")
        except Exception as exc:
            log.warning(f"Vision model pull error ({model}): {exc}")
        finally:
            global _pull_started
            with _pull_lock:
                _pull_started = False

    if background:
        threading.Thread(target=_pull, name="ollama-vision-pull", daemon=True).start()
        log.info(f"📥 Vision model {model} pull started in background")
        return False

    _pull()
    return is_vision_model_present(cfg, model)
