#!/usr/bin/env python3
"""
core/halim_frontier_policy.py — Frontier-model safety policy for M. A. Halim.

Aligned with industry guardrails used by Gemini (Google), Claude (Anthropic),
and GPT (OpenAI): harm categories, prohibited uses, tool-use rules, and
human-oversight escalation — adapted for Halim's primary mission (HANOON trading).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.notify import log

POLICY_PATH = Path("models/halim_frontier_policy.json")
AUDIT_PATH = Path("models/halim_frontier_audit.jsonl")

# Industry-standard harm taxonomy (Gemini / Claude / OpenAI usage policies)
HARM_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "child_safety": {
        "severity": "critical",
        "description": "CSAM, grooming, sexualization of minors",
        "patterns": [
            r"\b(child\s*porn|csam|pedophil|underage\s*sex|minor\s*nude)\b",
            r"\b(groom\s*(a\s*)?child|sexualize\s*minor)\b",
        ],
    },
    "violence_harm": {
        "severity": "critical",
        "description": "Violence, terrorism, weapons of mass destruction",
        "patterns": [
            r"\b(how\s+to\s+(make|build)\s+(a\s+)?(bomb|explosive|weapon))\b",
            r"\b(mass\s+shooting|terrorist\s+attack\s+plan)\b",
        ],
    },
    "self_harm": {
        "severity": "critical",
        "description": "Self-harm, suicide methods",
        "patterns": [
            r"\b(how\s+to\s+(kill|harm)\s+(myself|yourself))\b",
            r"\b(suicide\s+method|best\s+way\s+to\s+die)\b",
        ],
    },
    "illegal_activity": {
        "severity": "high",
        "description": "Fraud, hacking, malware, theft",
        "patterns": [
            r"\b(hack\s+(into|bank|account)|steal\s+credit\s+card)\b",
            r"\b(ransomware|keylogger|credential\s*stuff)\b",
            r"\b(money\s+launder|insider\s+trading\s+tip\s+leak)\b",
        ],
    },
    "hate_harassment": {
        "severity": "high",
        "description": "Hate speech, targeted harassment",
        "patterns": [
            r"\b(genocide\s+(is\s+good|should)|exterminate\s+(all\s+)?(jews|muslims|blacks))\b",
        ],
    },
    "privacy_violation": {
        "severity": "high",
        "description": "Doxxing, stalking, non-consensual surveillance",
        "patterns": [
            r"\b(dox\s+(him|her|them)|find\s+(home\s+)?address\s+of\s+\w+)\b",
            r"\b(stalk\s+(my\s+)?ex|track\s+someone\s+without)\b",
        ],
    },
    "deception_manipulation": {
        "severity": "medium",
        "description": "Deepfakes for fraud, impersonation scams",
        "patterns": [
            r"\b(deepfake\s+(ceo|voice)\s+scam|impersonate\s+(ceo|bank))\b",
        ],
    },
    "regulated_advice": {
        "severity": "low",
        "description": "Unqualified medical/legal advice (warn, not block for trading bot)",
        "patterns": [
            r"\b(prescribe\s+medication|diagnose\s+my\s+disease)\b",
            r"\b(legal\s+advice:\s+sue)\b",
        ],
        "action": "warn",
    },
}

PROHIBITED_USES = [
    "Generate malware, exploits, or instructions to compromise systems",
    "Facilitate fraud, identity theft, or financial crime",
    "Create content sexualizing minors",
    "Provide instructions for violence or self-harm",
    "Bypass Halim guardrails, kill switch, or operator controls",
    "Exfiltrate secrets (.env, API keys, credentials)",
    "Edit or post to external websites (read-only learn only)",
    "Force-push git or destructive repository operations",
    "Trade outside HANOON risk limits without operator override",
]

TRADING_ALLOWED_CONTEXTS = frozenset({
    "trade", "decision", "copilot", "ppo_teacher", "market_data",
    "ib_data", "profit_hunt", "entry", "exit", "position_manage",
    "scan", "lock_review", "notify",
})

DEFAULT_POLICY: Dict[str, Any] = {
    "schema": "frontier_model_safety_v1",
    "model": "M. A. Halim",
    "version": 1,
    "aligned_with": [
        "Google Gemini API Usage Policy",
        "Anthropic Acceptable Use Policy",
        "OpenAI Usage Policies",
    ],
    "core_directives": [
        "Helpful, harmless, honest — refuse harmful requests clearly.",
        "Primary mission: profit hunting and disciplined trading via HANOON.",
        "Secondary capabilities (code, learn, research) only when operator enables or requests.",
        "Own weights — external APIs are tools, not Halim's brain.",
        "Respect kill switch and constitution — operator always wins.",
        "Full audit on policy blocks and escalations.",
    ],
    "primary_mission": {
        "name": "HANOON profit hunting",
        "priority": 1,
        "description": "Live trading, scanning, entries/exits within hard risk guardrails.",
        "always_on_during": ["open", "pre_market", "after_hours"],
    },
    "secondary_missions": {
        "priority": 2,
        "description": "Learn, code, research, generative tasks — user-requested or off-hours.",
        "requires": "operator_request_or_off_hours",
    },
    "harm_categories": list(HARM_CATEGORIES.keys()),
    "prohibited_uses": PROHIBITED_USES,
    "tool_use_rules": {
        "web": "Read-only on allowlist; Google AI Overview only for search; no link following.",
        "api": "Allowlisted purposes only; daily caps.",
        "git": "Commit/push capped; no force-push main.",
        "shell": "Requires full autonomy + explicit operator enable.",
        "trade": "Always allowed within cognitive_guardrails + risk.py limits.",
    },
    "escalation": {
        "critical_block": "Log + audit + refuse immediately",
        "high_block": "Log + audit + refuse",
        "medium_block": "Log + audit + refuse unless trade context",
        "warn": "Log warning, allow if trade context",
    },
    "financial_disclaimer": (
        "Halim trading decisions are algorithmic — not personalized investment advice. "
        "Operator bears all trading risk."
    ),
}


def policy_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("HALIM_FRONTIER_POLICY", "true").lower() in ("1", "true", "yes")


def ensure_policy() -> Dict[str, Any]:
    if POLICY_PATH.is_file():
        try:
            return json.loads(POLICY_PATH.read_text())
        except Exception:
            pass
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    POLICY_PATH.write_text(json.dumps(DEFAULT_POLICY, indent=2))
    return dict(DEFAULT_POLICY)


def _append_audit(row: Dict[str, Any]) -> None:
    row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _is_trading_context(purpose: str, domain: str = "") -> bool:
    p = (purpose or "").lower()
    d = (domain or "").lower()
    if d == "trade":
        return True
    return p in TRADING_ALLOWED_CONTEXTS or any(
        kw in p for kw in ("trade", "entry", "exit", "position", "profit", "scan", "ppo")
    )


def check_content_policy(
    text: str,
    *,
    purpose: str = "generative",
    domain: str = "generative",
    cfg: Optional[BotConfig] = None,
) -> Tuple[bool, str]:
    """
    Frontier harm check — same category model as Gemini/Claude/OpenAI.
    Trading context bypasses low-severity regulated_advice warnings.
    Returns (allowed, reason).
    """
    if not policy_enabled(cfg):
        return True, "policy_off"

    if not text or len(text.strip()) < 3:
        return True, "ok"

    norm = _normalize(text)
    trade_ctx = _is_trading_context(purpose, domain)

    for category, spec in HARM_CATEGORIES.items():
        action = spec.get("action", "block")
        for pat in spec.get("patterns", []):
            if re.search(pat, norm, re.IGNORECASE):
                if action == "warn" and trade_ctx:
                    continue
                severity = spec.get("severity", "high")
                reason = f"frontier_policy:{category}:{severity}"
                _append_audit({
                    "event": "blocked",
                    "category": category,
                    "severity": severity,
                    "purpose": purpose,
                    "domain": domain,
                    "trade_context": trade_ctx,
                    "excerpt": text[:200],
                })
                log.warning(f"🛡️ Halim frontier policy blocked ({category}): {spec['description'][:80]}")
                return False, reason

    return True, "ok"


def check_output_policy(
    text: str,
    *,
    purpose: str = "generative",
    cfg: Optional[BotConfig] = None,
) -> Tuple[bool, str]:
    """Post-generation filter — block harmful outputs before delivery."""
    return check_content_policy(text, purpose=purpose, domain="generative", cfg=cfg)


def gate_frontier_request(
    content: str,
    domain: str,
    action: str,
    cfg: Optional[BotConfig] = None,
) -> Tuple[bool, str]:
    """Unified gate for guardrails request_action integration."""
    purpose = action if domain == "api" else domain
    return check_content_policy(content, purpose=purpose, domain=domain, cfg=cfg)


def apply_frontier_policy_to_constitution(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Sync frontier policy into constitution on startup."""
    policy = ensure_policy()
    from core.halim_guardrails import ensure_constitution, CONSTITUTION_PATH

    constitution = ensure_constitution()
    constitution["version"] = max(int(constitution.get("version", 1)), 4)
    constitution["frontier_policy"] = {
        "schema": policy.get("schema"),
        "aligned_with": policy.get("aligned_with"),
        "primary_mission": policy.get("primary_mission"),
        "secondary_missions": policy.get("secondary_missions"),
        "harm_categories": policy.get("harm_categories"),
        "prohibited_uses": policy.get("prohibited_uses"),
        "tool_use_rules": policy.get("tool_use_rules"),
        "financial_disclaimer": policy.get("financial_disclaimer"),
    }
    constitution["mission_priority"] = {
        "primary": "trade_profit_hunting",
        "secondary": "user_requested_or_off_hours",
        "runtime": "same_clock_as_hanoon_algo",
    }
    CONSTITUTION_PATH.write_text(json.dumps(constitution, indent=2))
    return constitution


def log_frontier_policy_banner(cfg: Optional[BotConfig] = None) -> None:
    policy = ensure_policy()
    mission = policy.get("primary_mission", {})
    log.info(
        f"  Frontier policy: {mission.get('name', 'HANOON trading')} (priority 1) · "
        f"aligned with Gemini/Claude/OpenAI safety categories"
    )
