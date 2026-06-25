#!/usr/bin/env python3
"""
core/trader_directives.py — Your trading ideas → AI prompts + guardrailed learning.

Edit models/trader_directives.txt directly, or send via Telegram:
  /direct <your instruction>
  /guide <same>
  any free-text message (after /verify)

Directives are injected into every Ollama council prompt and can trigger
bounded parameter mutations via commander_learning (max 3 per cycle).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.notify import log

DIRECTIVES_JSONL = Path("models/trader_directives.jsonl")
DIRECTIVES_TXT = Path("models/trader_directives.txt")
MAX_ACTIVE = 16


def append_directive(text: str, source: str = "user", *, chat_id: str = "") -> None:
    """Store a trader instruction — survives restarts, feeds all AI prompts."""
    text = (text or "").strip()
    if not text or len(text) < 4:
        return
    DIRECTIVES_JSONL.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "chat_id": str(chat_id),
        "text": text[:2500],
        "active": True,
    }
    try:
        with open(DIRECTIVES_JSONL, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as exc:
        log.debug(f"Directive jsonl: {exc}")
    _refresh_active_txt()
    log.info(f"📋 Trader directive stored ({source}): {text[:120]}")


def _load_records(limit: int = 80) -> List[Dict[str, Any]]:
    if not DIRECTIVES_JSONL.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in DIRECTIVES_JSONL.read_text().splitlines()[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return out


def load_active_directives(limit: int = MAX_ACTIVE) -> List[str]:
    """Most recent active directive texts for prompt injection."""
    texts: List[str] = []
    for rec in reversed(_load_records(limit * 3)):
        if not rec.get("active", True):
            continue
        t = str(rec.get("text", "")).strip()
        if t and t not in texts:
            texts.append(t)
        if len(texts) >= limit:
            break
    if DIRECTIVES_TXT.exists():
        try:
            manual = DIRECTIVES_TXT.read_text().strip()
            if manual and not texts:
                for line in manual.splitlines():
                    line = line.strip().lstrip("•-* ")
                    if line and not line.startswith("#"):
                        texts.append(line)
        except Exception:
            pass
    return texts


def directives_prompt_block(limit: int = 8) -> str:
    notes = load_active_directives(limit)
    if not notes:
        return ""
    bullets = "\n".join(f"  • {n[:400]}" for n in notes[-limit:])
    return (
        "TRADER DIRECTIVES (highest priority — follow within risk guardrails):\n"
        f"{bullets}\n"
        "Interpret: predict profit probability before entry; identify fakeouts fast; "
        "only enter when upside odds clear; fakeout fade plays OK when math supports.\n"
    )


def _refresh_active_txt() -> None:
    notes = load_active_directives(MAX_ACTIVE)
    lines = [
        "# Trader directives — edit here or use Telegram /direct",
        f"# Updated {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    lines.extend(f"• {n}" for n in notes)
    try:
        DIRECTIVES_TXT.write_text("\n".join(lines) + "\n")
    except Exception as exc:
        log.debug(f"Directive txt refresh: {exc}")


def seed_default_directives_if_empty() -> None:
    if DIRECTIVES_JSONL.exists() and DIRECTIVES_JSONL.stat().st_size > 50:
        return
    default = (
        "Do not blindly enter every volume spike. Estimate profit_probability and "
        "direction before entry. Identify fakeouts quickly — skip chase entries when "
        "fade_risk is high. Fakeout fade plays are allowed when micro predicts a bounce "
        "with tight stop. Prefer small consistent wins over noisy entries."
    )
    append_directive(default, source="seed")


def maybe_apply_directive_mutations(
    cfg,
    directive_text: str,
    think_fn=None,
) -> Dict[str, Any]:
    """
    Route directive through commander learning for guardrailed param tweaks.
    Called async from Telegram — never blocks trading loop.
    """
    from core.commander_learning import run_commander_learning_cycle

    trigger = f"[trader_directive] {directive_text[:1500]}"
    try:
        return run_commander_learning_cycle(
            cfg, None, think_fn=think_fn, trigger=trigger, apply=True,
        )
    except Exception as exc:
        log.debug(f"Directive mutation: {exc}")
        return {"applied": 0, "error": str(exc)}
