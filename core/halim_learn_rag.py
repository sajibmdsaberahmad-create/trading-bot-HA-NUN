#!/usr/bin/env python3
"""
core/halim_learn_rag.py — Retrieve local learn_cache snippets for chat (no API).

Wikipedia/news fetches land in halim/data/learn_cache/; this injects relevant
excerpts into companion/LM prompts so reads help immediately — before retrain.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig

CACHE_DIR = Path("halim/data/learn_cache")

_STOP = frozenset(
    "a an the is are was were be been being to of in for on with at by from as it that this "
    "what how when where why who me my you your i we they he she do does did have has had "
    "and or but not no yes hi hello hey".split()
)


def rag_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("HALIM_LEARN_RAG", "true").lower() in ("1", "true", "yes")


def _query_terms(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return [w for w in words if w not in _STOP]


def _load_cache_docs(limit_files: int = 80) -> List[Dict[str, Any]]:
    if not CACHE_DIR.is_dir():
        return []
    docs: List[Tuple[float, Path]] = []
    for p in CACHE_DIR.glob("*.json"):
        try:
            docs.append((p.stat().st_mtime, p))
        except Exception:
            continue
    docs.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for _, path in docs[:limit_files]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["_path"] = str(path)
            out.append(doc)
        except Exception:
            continue
    return out


def _score_doc(query_terms: List[str], doc: Dict[str, Any]) -> int:
    if not query_terms:
        return 0
    topic = str(doc.get("topic") or doc.get("url") or "").lower()
    body = str(doc.get("text") or doc.get("text_excerpt") or "")[:12000].lower()
    hay = f"{topic} {body}"
    score = 0
    for term in query_terms:
        if term in topic:
            score += 4
        score += hay.count(term)
    if topic.startswith("wikipedia:"):
        title = topic[10:].replace("_", " ")
        for term in query_terms:
            if term in title:
                score += 3
    return score


def retrieve_learn_context(
    query: str,
    *,
    cfg: Optional[BotConfig] = None,
    max_chars: int = 0,
    max_docs: int = 0,
) -> List[Dict[str, Any]]:
    """Return ranked learn_cache hits for a user query."""
    if not rag_enabled(cfg):
        return []
    if max_chars <= 0:
        max_chars = int(os.getenv("HALIM_LEARN_RAG_MAX_CHARS", "2200"))
    if max_docs <= 0:
        max_docs = int(os.getenv("HALIM_LEARN_RAG_MAX_DOCS", "2"))

    terms = _query_terms(query)
    if not terms:
        return []

    ranked: List[Tuple[int, Dict[str, Any]]] = []
    for doc in _load_cache_docs():
        score = _score_doc(terms, doc)
        if score > 0:
            ranked.append((score, doc))
    ranked.sort(key=lambda x: x[0], reverse=True)

    hits: List[Dict[str, Any]] = []
    used = 0
    for score, doc in ranked[:max_docs]:
        text = str(doc.get("text") or doc.get("text_excerpt") or "").strip()
        if len(text) < 60:
            continue
        excerpt = text[: max(200, max_chars // max(1, max_docs))]
        topic = str(doc.get("topic") or doc.get("url") or "reference")
        row = {
            "topic": topic,
            "url": doc.get("url"),
            "score": score,
            "excerpt": excerpt,
            "source": "learn_cache",
        }
        hits.append(row)
        used += len(excerpt)
        if used >= max_chars:
            break
    return hits


def learn_rag_block(
    query: str,
    *,
    cfg: Optional[BotConfig] = None,
) -> str:
    """Prompt block for companion/LM — empty if no relevant cache."""
    hits = retrieve_learn_context(query, cfg=cfg)
    if not hits:
        return ""
    lines = [
        "LOCAL LEARN CACHE (read-only pages Halim already fetched — use facts below, "
        "do not invent beyond this):"
    ]
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h['topic']}")
        if h.get("url"):
            lines.append(f"    url: {h['url']}")
        lines.append(h["excerpt"])
        lines.append("")
    return "\n".join(lines).strip()
