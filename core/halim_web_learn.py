#!/usr/bin/env python3
"""
core/halim_web_learn.py — Read-only learning from allowlisted Wikipedia, news, and reference sites.

Halim may READ public articles for learning gold — never edit, post, login, or change anything
on external sites. Strict guardrails + full audit. Content saved locally for Halim training only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

JOURNAL_PATH = Path("models/halim_web_learn.jsonl")
MONITOR_PATH = Path("models/halim_web_monitor.jsonl")
CACHE_DIR = Path("halim/data/learn_cache")

_USER_AGENT = (
    "Mozilla/5.0 (compatible; MAHalimLearn/1.0; +read-only-learning; no-edit)"
)
_MAX_BYTES = int(os.getenv("HALIM_LEARN_MAX_BYTES", "524288"))  # 512 KB

# URL path fragments that imply write/login — always blocked
_FORBIDDEN_PATH_FRAGMENTS = (
    "/login", "/signin", "/signup", "/register", "/subscribe",
    "/action=edit", "action=edit", "title=Special:", "title=Talk:", "oldid=",
    "/wp-admin", "/post?", "/comment", "/cart", "/checkout",
    "/api/", "/graphql",
)


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("HALIM_WEB_LEARN", "true").lower() in ("1", "true", "yes")


def _load_learn_hosts(cfg: Optional[BotConfig] = None) -> List[str]:
    from core.halim_guardrails import ensure_constitution
    c = ensure_constitution()
    policy = c.get("web_policy") or {}
    hosts = policy.get("learn_allowlist_hosts") or c.get("web_allowlist_hosts") or []
    return [h.lower() for h in hosts if h and "google.com" not in h.lower()]


def _append(path: Path, row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def validate_learn_url(url: str, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    """Read-only URL validation — allowlisted host + safe path only."""
    if not url or not url.startswith(("http://", "https://")):
        return False, "invalid_url_scheme"
    if url.lower().startswith("http://"):
        return False, "https_required"

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host_bare = host[4:]
    else:
        host_bare = host

    allow = _load_learn_hosts(cfg)
    if not allow:
        return False, "no_learn_hosts_configured"

    host_ok = host in allow or host_bare in allow or any(
        host == h or host.endswith("." + h) or host_bare == h.replace("www.", "")
        for h in allow
    )
    if not host_ok:
        return False, f"host_not_in_learn_allowlist:{host}"

    path_q = (parsed.path + "?" + parsed.query).lower()
    for frag in _FORBIDDEN_PATH_FRAGMENTS:
        if frag.lower() in path_q:
            return False, f"forbidden_path:{frag}"

    if "wikipedia.org" in host:
        if not parsed.path.startswith("/wiki/"):
            return False, "wikipedia_wiki_path_only"
        title = parsed.path[len("/wiki/"):]
        if not title or title.startswith("Special:") or title.startswith("Talk:"):
            return False, "wikipedia_namespace_blocked"

    return True, "ok"


def _html_to_text(html: str, max_chars: int = 12000) -> str:
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    html = re.sub(r"(?is)<nav[^>]*>.*?</nav>", " ", html)
    html = re.sub(r"(?is)<footer[^>]*>.*?</footer>", " ", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _fetch_read_only(url: str, timeout: float = 20.0) -> Tuple[str, str]:
    """GET only. Redirects must stay on same host family."""
    parsed = urllib.parse.urlparse(url)
    orig_host = parsed.netloc.lower()

    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        if resp.status >= 400:
            raise urllib.error.HTTPError(url, resp.status, "", resp.headers, None)
        final = resp.geturl()
        final_host = urllib.parse.urlparse(final).netloc.lower()
        if orig_host not in final_host and final_host not in orig_host:
            ok, _ = validate_learn_url(final)
            if not ok:
                raise ValueError(f"redirect_blocked:{final_host}")
        raw = resp.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raw = raw[:_MAX_BYTES]
        return raw.decode("utf-8", errors="replace"), final


def fetch_learn_page(
    url: str,
    cfg: Optional[BotConfig] = None,
    *,
    topic: str = "",
    save_cache: bool = True,
) -> Dict[str, Any]:
    """
    Read one allowlisted page for learning. READ-ONLY — Halim never changes remote content.
    """
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return {"ok": False, "reason": "HALIM_WEB_LEARN_disabled", "url": url}

    ok, reason = validate_learn_url(url, cfg)
    if not ok:
        _append(JOURNAL_PATH, {"ok": False, "url": url, "reason": reason, "topic": topic})
        return {"ok": False, "reason": reason, "url": url}

    from core.halim_guardrails import gate_web_learn

    gok, greason = gate_web_learn(url, cfg)
    if not gok:
        return {"ok": False, "reason": greason, "url": url}

    try:
        html, final_url = _fetch_read_only(url)
    except urllib.error.HTTPError as exc:
        out = {"ok": False, "url": url, "reason": f"http_{exc.code}"}
        _append(JOURNAL_PATH, out)
        _append(MONITOR_PATH, {**out, "event": "learn_fetch_failed"})
        return out
    except Exception as exc:
        out = {"ok": False, "url": url, "reason": str(exc)[:120]}
        _append(JOURNAL_PATH, out)
        return out

    text = _html_to_text(html)
    if len(text) < 80:
        out = {
            "ok": False, "url": url, "final_url": final_url,
            "reason": "insufficient_text_extracted",
        }
        _append(JOURNAL_PATH, out)
        return out

    host = urllib.parse.urlparse(final_url).netloc
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    out = {
        "ok": True,
        "url": url,
        "final_url": final_url,
        "host": host,
        "topic": topic or url,
        "text_chars": len(text),
        "text_excerpt": text[:2000],
        "content_hash": content_hash,
        "read_only": True,
        "external_changed": False,
        "method": "GET",
        "links_followed": 0,
    }

    if save_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", content_hash)
        cache_file = CACHE_DIR / f"{safe}.json"
        cache_file.write_text(json.dumps({
            "url": final_url,
            "host": host,
            "topic": topic,
            "text": text[:50000],
            "fetched_at": out.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "hash": content_hash,
        }, indent=2))

    _append(JOURNAL_PATH, {k: v for k, v in out.items() if k != "text_excerpt"})
    _append(MONITOR_PATH, {
        "event": "learn_fetch_ok",
        "host": host,
        "url": final_url,
        "chars": len(text),
        "hash": content_hash,
        "read_only": True,
    })

    log.info(
        f"📚 Halim learn READ {host} — {len(text)} chars (read-only, monitored, hash={content_hash})"
    )

    try:
        from core.halim_action_learn import record_action
        record_action(
            "read_understand",
            "learn_fetch",
            input_text=f"{topic or url}\n{final_url}",
            output_text=text[:2000],
            outcome="ok",
            source="web_learn",
            meta={"host": host, "hash": content_hash},
            cfg=cfg,
        )
    except Exception:
        pass

    if os.getenv("HALIM_LEARN_NOTIFY", "").lower() in ("1", "true", "yes"):
        try:
            from core.brain_notify import notify_brain_development
            notify_brain_development(
                cfg, "brain_evolution",
                {"summary": f"Learn fetch {host} {len(text)} chars", "stage": "learn"},
                journal=False,
            )
        except Exception:
            pass

    return out


def fetch_wikipedia_summary(title: str, cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Convenience: read one Wikipedia article (English)."""
    t = title.strip().replace(" ", "_")
    t = urllib.parse.quote(t, safe="/_()")
    url = f"https://en.wikipedia.org/wiki/{t}"
    return fetch_learn_page(url, cfg, topic=f"wikipedia:{title}")
