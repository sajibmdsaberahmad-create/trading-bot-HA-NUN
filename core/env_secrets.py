#!/usr/bin/env python3
"""
core/env_secrets.py — Encrypted .env vault for cross-device sync (private repo).

Plaintext .env never goes to git. Instead:
  secrets/hanoon.env.enc  — encrypted env (safe to push to private GitHub)
  secrets/sync.key        — Fernet key (private repo only)

New device: git pull → start_hanoon → auto-decrypts to .env (no re-typing secrets).
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from pathlib import Path
from typing import Optional, Tuple

from core.notify import log

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
ENC_PATH = REPO_ROOT / "secrets" / "hanoon.env.enc"
KEY_PATH = REPO_ROOT / "secrets" / "sync.key"
SECRETS_DIR = REPO_ROOT / "secrets"


def _enabled() -> bool:
    return os.getenv("ENV_SYNC_ENABLED", "true").lower() in ("1", "true", "yes")


def _fernet():
    try:
        from cryptography.fernet import Fernet
        return Fernet
    except ImportError:
        return None


def _ensure_key() -> bytes:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists():
        raw = KEY_PATH.read_bytes().strip()
        if raw:
            return raw
    Fernet = _fernet()
    if Fernet is None:
        raise RuntimeError("cryptography package required — pip install cryptography")
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    KEY_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    log.info("Created env sync key (secrets/sync.key) — will sync via private git repo")
    return key


def _load_key() -> Optional[bytes]:
    if not KEY_PATH.exists():
        return None
    return KEY_PATH.read_bytes().strip() or None


def encrypt_env_to_vault(force: bool = False) -> bool:
    """Encrypt .env → secrets/hanoon.env.enc (never pushes plaintext .env)."""
    if not _enabled():
        return False
    if not ENV_PATH.exists():
        return False
    Fernet = _fernet()
    if Fernet is None:
        log.debug("env_secrets: cryptography not installed — skip encrypt")
        return False

    plain = ENV_PATH.read_bytes()
    if not force and ENC_PATH.exists():
        try:
            if ENC_PATH.stat().st_mtime >= ENV_PATH.stat().st_mtime:
                return True
        except OSError:
            pass

    key = _ensure_key()
    token = Fernet(key).encrypt(plain)
    ENC_PATH.write_bytes(token)
    log.debug("Encrypted .env → secrets/hanoon.env.enc")
    return True


def decrypt_vault_to_env(force: bool = False) -> bool:
    """Decrypt secrets/hanoon.env.enc → .env if vault exists."""
    if not _enabled():
        return False
    if not ENC_PATH.exists():
        return False
    if ENV_PATH.exists() and not force:
        return True

    key = _load_key()
    if not key:
        log.warning("secrets/hanoon.env.enc exists but secrets/sync.key missing — git pull first")
        return False

    Fernet = _fernet()
    if Fernet is None:
        log.warning("pip install cryptography to decrypt env vault")
        return False

    try:
        plain = Fernet(key).decrypt(ENC_PATH.read_bytes())
    except Exception as exc:
        log.warning(f"Env vault decrypt failed: {exc}")
        return False

    ENV_PATH.write_bytes(plain)
    ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    log.info("Restored .env from encrypted vault (secrets/hanoon.env.enc)")
    return True


def bootstrap_env(repo_root: Optional[str] = None) -> Tuple[bool, str]:
    """
    Startup hook: decrypt vault if needed, else refresh vault from .env.
    Returns (ok, message).
    """
    global REPO_ROOT, ENV_PATH, ENC_PATH, KEY_PATH, SECRETS_DIR
    if repo_root:
        REPO_ROOT = Path(repo_root).resolve()
        ENV_PATH = REPO_ROOT / ".env"
        ENC_PATH = REPO_ROOT / "secrets" / "hanoon.env.enc"
        KEY_PATH = REPO_ROOT / "secrets" / "sync.key"
        SECRETS_DIR = REPO_ROOT / "secrets"

    if not _enabled():
        return True, "ENV_SYNC disabled"

    if ENV_PATH.exists():
        encrypt_env_to_vault()
        return True, "Loaded local .env (vault updated)"

    if decrypt_vault_to_env():
        return True, "Restored .env from encrypted vault"

    if (REPO_ROOT / ".env.example").exists():
        return False, "No .env — git pull for secrets vault or copy .env.example"

    return False, "No .env or secrets/hanoon.env.enc"


def vault_paths_for_git() -> list[str]:
    """Paths to include in git sync (encrypted only)."""
    out = []
    if ENC_PATH.exists():
        out.append(str(ENC_PATH.relative_to(REPO_ROOT)))
    if KEY_PATH.exists() and os.getenv("ENV_SYNC_PUSH_KEY", "true").lower() in ("1", "true", "yes"):
        out.append(str(KEY_PATH.relative_to(REPO_ROOT)))
    return out
