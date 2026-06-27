"""Halim client — optional HTTP to local server; short timeout, never blocks trading."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from halim.protocol import DEFAULT_HOST, DEFAULT_PORT


def server_url() -> Optional[str]:
    mode = os.getenv("HALIM_SERVER", "auto").lower()
    if mode in ("0", "false", "off", "disabled"):
        return None
    if mode in ("1", "true", "on", "required"):
        return os.getenv("HALIM_SERVER_URL", f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    # auto
    url = os.getenv("HALIM_SERVER_URL", f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    if health(url, timeout=0.4):
        return url
    return None


def _request(
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 2.0,
    base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    base = (base_url or os.getenv("HALIM_SERVER_URL", f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")).rstrip("/")
    url = f"{base}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def health(base_url: Optional[str] = None, timeout: float = 1.0) -> bool:
    r = _request("GET", "/health", timeout=timeout, base_url=base_url)
    return bool(r and r.get("ok"))


def status(base_url: Optional[str] = None, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    return _request("GET", "/v1/status", timeout=timeout, base_url=base_url)


def complete(
    prompt: str,
    *,
    purpose: str = "reasoning",
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    url = server_url()
    if not url:
        return None
    t = timeout if timeout is not None else float(os.getenv("HALIM_INFERENCE_TIMEOUT_SEC", "90"))
    return _request(
        "POST",
        "/v1/complete",
        body={"prompt": prompt[:8000], "purpose": purpose},
        timeout=t,
        base_url=url,
    )


def record_action(
    capability: str,
    action: str,
    *,
    input_text: str = "",
    output_text: str = "",
    source: str = "hanoon_client",
    timeout: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """POST action to server — Halim learns by doing even when reasoning is remote."""
    url = server_url()
    if not url:
        return None
    return _request(
        "POST",
        "/v1/record",
        body={
            "capability": capability,
            "action": action,
            "input": input_text[:8000],
            "output": output_text[:8000],
            "source": source,
        },
        timeout=timeout,
        base_url=url,
    )


def export_gold(timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    url = server_url()
    if not url:
        return None
    return _request("POST", "/v1/export", body={}, timeout=timeout, base_url=url)


def runtime_info(base_url: Optional[str] = None, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    return _request("GET", "/v1/runtime", timeout=timeout, base_url=base_url)
