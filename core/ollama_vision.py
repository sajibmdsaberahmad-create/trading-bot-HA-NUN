#!/usr/bin/env python3
"""
core/ollama_vision.py — Ensure Ollama vision model (llava) is available for chart review.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Optional

from core.config import BotConfig
from core.notify import log

_pull_lock = threading.Lock()
_pull_started = False


def vision_model_name(cfg: BotConfig) -> str:
    return (getattr(cfg, "OLLAMA_VISION_MODEL", None) or "llava").strip() or "llava"


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
    target = (model or vision_model_name(cfg)).strip()
    base = target.split(":")[0]
    installed = _list_models(cfg)
    return any(
        n == target or n == base or n.startswith(f"{base}:")
        for n in installed
    )


def ensure_vision_model(cfg: BotConfig, *, background: bool = True) -> bool:
    """
    Return True if vision model is installed (or pull was started).
    """
    global _pull_started

    if not getattr(cfg, "OLLAMA_ENABLED", True):
        log.debug("Vision model check skipped — Ollama disabled")
        return False

    model = vision_model_name(cfg)
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
        log.info(f"📥 Pulling Ollama vision model {model} (Telegram chart review)...")
        try:
            proc = subprocess.run(
                ["ollama", "pull", model],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if proc.returncode == 0 and is_vision_model_present(cfg, model):
                log.info(f"✅ Vision model {model} installed — chart review enabled")
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
