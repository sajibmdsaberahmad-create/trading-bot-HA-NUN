#!/usr/bin/env python3
"""
core/halim_google_ai_search.py — Google AI-mode search answers ONLY (no Gemini API, no browsing).

Halim may "google" a topic and receive the public AI Overview blurb shown on Google Search
(AI mode) — like a human typing in the search box and reading only that Gemini-generated
summary box. Halim does NOT:
  - use Gemini / Google AI API keys
  - follow links or visit result pages
  - read arbitrary websites
  - browse Google beyond one search?q= request per query
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import BotConfig
from core.notify import log

JOURNAL_PATH = Path("models/halim_google_search.jsonl")

# Public Google Search — AI-oriented layout hint (no API key)
_GOOGLE_SEARCH_BASE = "https://www.google.com/search"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("HALIM_GOOGLE_AI_SEARCH", "true").lower() in ("1", "true", "yes")


def _append_journal(row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _build_search_url(query: str) -> str:
    """Single allowed URL shape — Google search results page only."""
    q = query.strip()[:300]
    params = {
        "q": q,
        "hl": "en",
        "gl": "us",
        # Hints AI-heavy SERP where available (region/account may vary)
        "udm": "50",
    }
    return f"{_GOOGLE_SEARCH_BASE}?{urllib.parse.urlencode(params)}"


def _validate_query(query: str) -> Optional[str]:
    q = (query or "").strip()
    if not q:
        return "empty_query"
    if len(q) > 300:
        return "query_too_long"
    low = q.lower()
    if "http://" in low or "https://" in low or "www." in low:
        return "query_must_be_text_not_url"
    if any(c in q for c in ("<", ">", "\x00")):
        return "invalid_characters"
    return None


def _extract_ai_overview(html: str) -> str:
    """
    Parse only the AI Overview / generative snippet from Google SERP HTML.
    No link following — text extraction from this single response only.
    """
    if not html:
        return ""

    # Embedded JSON fragments (common in AI overview payloads)
    for pattern in (
        r'"AI Overview"[^}]{0,2000}?"text"\s*:\s*"((?:\\.|[^"\\])*)"',
        r'"type"\s*:\s*"AIOverview"[^}]{0,3000}?"snippet"\s*:\s*"((?:\\.|[^"\\])*)"',
        r'data-attrid="wa:/description"[^>]*>([^<]{20,2000})</',
        r'class="[^"]*LGOjhe[^"]*"[^>]*>([^<]{20,3000})</',
        r'class="[^"]*PZPZlf[^"]*"[^>]*>([^<]{20,3000})</',
        r'aria-label="AI Overview"[^>]*>([\s\S]{50,4000}?)</div>',
    ):
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            text = m.group(1)
            text = re.sub(r"<[^>]+>", " ", text)
            text = text.replace("\\n", "\n").replace('\\"', '"')
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) >= 40:
                return text[:4000]

    # Fallback: look for visible "AI Overview" section heading nearby text
    idx = html.find("AI Overview")
    if idx >= 0:
        chunk = html[idx : idx + 8000]
        texts = re.findall(r">([^<>]{40,800})<", chunk)
        for t in texts:
            clean = re.sub(r"\s+", " ", t).strip()
            if len(clean) >= 60 and not clean.startswith("http"):
                return clean[:4000]

    return ""


def _fetch_google_search_page(url: str, timeout: float = 15.0) -> str:
    """One HTTP GET to google.com/search only — no redirects off google.com."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() not in ("www.google.com", "google.com"):
        raise ValueError("only_google_search_allowed")
    if not parsed.path.startswith("/search"):
        raise ValueError("only_search_path_allowed")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        final = resp.geturl()
        final_host = urllib.parse.urlparse(final).netloc.lower()
        if final_host not in ("www.google.com", "google.com"):
            raise ValueError(f"redirect_off_google_blocked:{final_host}")
        raw = resp.read()
        return raw.decode("utf-8", errors="replace")


def query_google_ai_answer(
    query: str,
    cfg: Optional[BotConfig] = None,
) -> Dict[str, Any]:
    """
    Ask Google Search AI-mode for a public overview answer (no Gemini API).

    Returns dict with keys: ok, query, answer, source, pages_visited (always 0 or 1 google search)
    """
    cfg = cfg or BotConfig()
    if not _enabled(cfg):
        return {"ok": False, "reason": "HALIM_GOOGLE_AI_SEARCH_disabled"}

    err = _validate_query(query)
    if err:
        return {"ok": False, "reason": err, "query": query}

    from core.halim_guardrails import gate_google_ai_search

    ok, reason = gate_google_ai_search(query, cfg)
    if not ok:
        return {"ok": False, "reason": reason, "query": query}

    url = _build_search_url(query)
    try:
        html = _fetch_google_search_page(url)
    except urllib.error.HTTPError as exc:
        out = {"ok": False, "query": query, "reason": f"http_{exc.code}", "url": url}
        _append_journal(out)
        return out
    except Exception as exc:
        out = {"ok": False, "query": query, "reason": str(exc)[:120], "url": url}
        _append_journal(out)
        return out

    answer = _extract_ai_overview(html)
    if not answer:
        out = {
            "ok": False,
            "query": query,
            "reason": "ai_overview_not_found",
            "hint": (
                "Google may not show AI Overview for this query/bot, or layout changed. "
                "Halim did not visit any other page."
            ),
            "url": url,
            "pages_visited": 1,
            "links_followed": 0,
            "gemini_api_used": False,
        }
        _append_journal(out)
        return out

    out = {
        "ok": True,
        "query": query,
        "answer": answer,
        "source": "google_search_ai_overview_public",
        "gemini_api_used": False,
        "pages_visited": 1,
        "links_followed": 0,
        "url": url,
    }
    _append_journal(out)
    log.info(f"🔍 Halim Google AI search — q={query[:60]!r} → {len(answer)} chars (no browse)")
    return out
