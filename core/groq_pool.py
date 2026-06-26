#!/usr/bin/env python3
"""
core/groq_pool.py — Round-robin Groq API keys for higher TPM/RPM headroom.

Set GROQ_API_KEY plus GROQ_API_KEY_2 (or comma-separated GROQ_API_KEYS).
On 429 the pool tries the next key before falling back to Gemini.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from core.config import BotConfig
from core.notify import log

_lock = threading.Lock()
_pools: Dict[int, "GroqKeyPool"] = {}


def parse_groq_keys(cfg: BotConfig) -> List[str]:
    """Unique non-empty Groq keys from env (primary + extras)."""
    seen: set = set()
    keys: List[str] = []

    def add(raw: str) -> None:
        for part in str(raw or "").replace(";", ",").split(","):
            k = part.strip().strip("'\"")
            if k and k not in seen:
                seen.add(k)
                keys.append(k)

    add(getattr(cfg, "GROQ_API_KEY", ""))
    add(getattr(cfg, "GROQ_API_KEYS", ""))
    import os
    add(os.getenv("GROQ_API_KEY_2", ""))
    add(os.getenv("GROQ_API_KEY_3", ""))
    return keys


def groq_key_count(cfg: BotConfig) -> int:
    return len(parse_groq_keys(cfg))


class GroqKeyPool:
    """Thread-safe round-robin across multiple Groq accounts."""

    def __init__(self, keys: List[str]):
        self._keys = list(keys)
        self._idx = 0
        self._clients: Dict[str, Any] = {}
        self._cooldown_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return len(self._keys)

    def keys_masked(self) -> List[str]:
        return [f"gsk_...{k[-4:]}" if len(k) > 8 else "***" for k in self._keys]

    def _available_keys(self) -> List[str]:
        now = time.time()
        return [k for k in self._keys if now >= self._cooldown_until.get(k, 0.0)]

    def next_key(self) -> Optional[str]:
        with self._lock:
            if not self._keys:
                return None
            now = time.time()
            for _ in range(len(self._keys)):
                key = self._keys[self._idx % len(self._keys)]
                self._idx += 1
                if now >= self._cooldown_until.get(key, 0.0):
                    return key
            return None

    def mark_rate_limited(self, key: str, *, cooldown_sec: float = 45.0) -> None:
        with self._lock:
            self._cooldown_until[key] = time.time() + cooldown_sec
        suffix = key[-4:] if len(key) > 4 else "?"
        log.warning(f"Groq key ...{suffix} rate-limited — cooldown {cooldown_sec:.0f}s")

    def client_for(self, key: str) -> Any:
        if key not in self._clients:
            from groq import Groq
            self._clients[key] = Groq(api_key=key)
        return self._clients[key]

    def all_exhausted(self) -> bool:
        return not self._available_keys()


def get_groq_pool(cfg: BotConfig) -> Optional[GroqKeyPool]:
    keys = parse_groq_keys(cfg)
    if not keys:
        return None
    pool_id = id(cfg)
    with _lock:
        pool = _pools.get(pool_id)
        if pool is None or pool.size != len(keys) or pool._keys != keys:
            pool = GroqKeyPool(keys)
            _pools[pool_id] = pool
        return pool
