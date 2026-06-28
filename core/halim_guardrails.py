#!/usr/bin/env python3
"""
core/halim_guardrails.py — Frontier guardrails for M. A. Halim (all domains, not trading-only).

Halim is designed to become a full frontier model: generative, calculative, coding,
web/API consumption, agents, multimodal. Every external action passes through here
so Halim cannot go rogue or out of control.

Layers:
  1. Constitution — immutable principles (models/halim_constitution.json)
  2. Kill switch — instant halt (HALIM_KILL_SWITCH or models/halim_kill_switch.json)
  3. Domain gates — trading | generative | coding | math | web | api | git | shell | agent
  4. Rate limits — per-domain daily/hourly caps
  5. Allowlists — URLs, API purposes, writable paths
  6. Audit trail — append-only models/halim_guardrail_audit.jsonl
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from core.config import BotConfig
from core.notify import log

CONSTITUTION_PATH = Path("models/halim_constitution.json")
KILL_SWITCH_PATH = Path("models/halim_kill_switch.json")
AUDIT_PATH = Path("models/halim_guardrail_audit.jsonl")
STATE_PATH = Path("models/halim_guardrail_state.json")

_lock = threading.Lock()

# Frontier capability domains — unlock by phase, always guardrailed
DOMAINS = frozenset({
    "trade",        # PPO, proxy, orders
    "generative",   # text, narrative, reasoning
    "coding",       # read/write code, refactors
    "math",         # calc, sizing, proofs
    "web",          # live internet fetch
    "api",          # consume external APIs (data, tools — NOT rented LLM brain)
    "git",          # commit/push
    "shell",        # subprocess / commands
    "agent",        # multi-step autonomous plans
    "multimodal",   # vision, charts, audio
    "file",         # read/write files
})

# Paths Halim may NEVER write (even at frontier)
FORBIDDEN_WRITE_PATHS = frozenset({
    ".env",
    "secrets/",
    "core/halim_guardrails.py",
    "core/cognitive_guardrails.py",
    "core/ai_guardrails.py",
    "core/param_bounds.py",
    "models/halim_constitution.json",
    "models/halim_kill_switch.json",
})

# Paths Halim may write when coding domain approved (expand in child phase)
ALLOWED_WRITE_PREFIXES = (
    "models/",
    "docs/",
    "halim/",
    "logs/",
    "scripts/halim_",
    "models/ai_guidelines.txt",
    "models/parameter_adjustments.json",
    "models/improvement_history.json",
    "models/halim_developer.jsonl",
    "docs/BRAIN_DEVELOPMENT_LOG.md",
    "docs/HALIM",
)

DEFAULT_CONSTITUTION: Dict[str, Any] = {
    "model": "M. A. Halim",
    "version": 1,
    "principles": [
        "Serve the operator — never act against explicit human intent.",
        "Own weights — external APIs are tools to consume data, not Halim's brain.",
        "Bounded autonomy — high-impact actions require guardrail approval or human gate.",
        "Full audit — every API, web, git, shell, and mutation is logged.",
        "Kill switch always wins — operator can halt Halim instantly.",
        "No secrets — never read, write, or exfiltrate .env, keys, or credentials.",
        "No self-modification of guardrails — Halim cannot edit this file or guardrail code.",
        "Git is memory — document and push learning; never force-push main or delete history.",
    ],
    "forbidden_forever": [
        "exfiltrate_secrets",
        "disable_kill_switch",
        "modify_guardrail_code",
        "force_push_main",
        "unbounded_shell",
        "spend_without_cap",
        "trade_without_risk_limits",
        "gemini_api_for_search",
        "follow_search_result_links",
        "external_post",
        "wiki_edit",
        "form_submit",
        "external_write",
    ],
    "autonomy_mode": "bounded",  # bounded | supervised | full (frontier, human-gated)
    "domains_enabled": {
        "trade": True,
        "generative": True,
        "coding": True,
        "math": True,
        "web": False,
        "api": True,
        "git": True,
        "shell": False,
        "agent": False,
        "multimodal": False,
        "file": True,
    },
    "rate_limits_daily": {
        "api_calls": 500,
        "google_ai_searches": 50,
        "learn_fetches": 80,
        "web_fetches": 0,
        "git_pushes": 50,
        "shell_commands": 20,
        "param_mutations": 30,
        "file_writes": 200,
        "agent_steps": 100,
    },
    "web_allowlist_hosts": [
        "www.google.com",
        "google.com",
    ],
    "web_policy": {
        "google_ai_search_only": False,
        "google_ai_search_enabled": True,
        "learn_fetch_enabled": True,
        "read_only_external": True,
        "no_post_requests": True,
        "no_external_edits": True,
        "no_gemini_api": True,
        "no_link_following": True,
        "no_arbitrary_browsing": True,
        "max_learn_bytes": 524288,
        "learn_allowlist_hosts": [
            "en.wikipedia.org",
            "www.reuters.com",
            "reuters.com",
            "feeds.reuters.com",
            "apnews.com",
            "www.apnews.com",
            "www.bbc.com",
            "bbc.com",
            "www.cnbc.com",
            "cnbc.com",
            "finance.yahoo.com",
            "www.sec.gov",
            "sec.gov",
            "www.investopedia.com",
            "investopedia.com",
            "www.investor.gov",
            "investor.gov",
            "docs.python.org",
            "developer.mozilla.org",
            "www.federalreserve.gov",
            "fred.stlouisfed.org",
        ],
        "description": (
            "Google = AI Overview only. Learn = read-only GET on allowlisted wiki/news/reference. "
            "Halim never edits or posts externally."
        ),
    },
    "api_allowlist_purposes": [
        "market_data",
        "github",
        "git_push",
        "halim_train",
        "halim_eval",
        "documentation",
        "research",
        "decision",
        "copilot",
        "ppo_teacher",
        "notify",
        "ib_data",
        "web_search",
    ],
}


def guardrails_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("HALIM_GUARDRAILS_ENABLED", "true").lower() in ("1", "true", "yes")


def kill_switch_active() -> bool:
    if os.getenv("HALIM_KILL_SWITCH", "").lower() in ("1", "true", "yes", "halt", "stop"):
        return True
    if KILL_SWITCH_PATH.is_file():
        try:
            data = json.loads(KILL_SWITCH_PATH.read_text())
            return bool(data.get("active"))
        except Exception:
            pass
    return False


def activate_kill_switch(reason: str = "operator") -> None:
    KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH_PATH.write_text(json.dumps({
        "active": True,
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    log.warning(f"🛑 HALIM KILL SWITCH ACTIVE — {reason}")


def deactivate_kill_switch(operator_note: str = "") -> None:
    KILL_SWITCH_PATH.write_text(json.dumps({
        "active": False,
        "cleared_at": datetime.now(timezone.utc).isoformat(),
        "note": operator_note,
    }, indent=2))
    log.info("✅ Halim kill switch cleared")


def ensure_constitution() -> Dict[str, Any]:
    if CONSTITUTION_PATH.is_file():
        try:
            return json.loads(CONSTITUTION_PATH.read_text())
        except Exception:
            pass
    CONSTITUTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONSTITUTION_PATH.write_text(json.dumps(DEFAULT_CONSTITUTION, indent=2))
    return dict(DEFAULT_CONSTITUTION)


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"day": _today(), "counts": {}}
    try:
        data = json.loads(STATE_PATH.read_text())
        if data.get("day") != _today():
            return {"day": _today(), "counts": {}}
        return data
    except Exception:
        return {"day": _today(), "counts": {}}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def learn_uncapped_active() -> bool:
    """True only when HALIM_LEARN_UNCAPPED_DATE matches today (UTC) — auto-off tomorrow."""
    target = os.getenv("HALIM_LEARN_UNCAPPED_DATE", "").strip()
    return bool(target) and target == _today()


def effective_learn_fetch_daily_cap() -> int:
    """Normal cap 500; raised cap on uncapped day only (still bounded)."""
    base = int(os.getenv("HALIM_LEARN_FETCH_DAILY_CAP", "500"))
    if learn_uncapped_active():
        return int(os.getenv("HALIM_LEARN_UNCAPPED_MAX_FETCHES", "1200"))
    return base


def learn_gold_budget_remaining() -> int:
    """Cap new gold exports on uncapped days — dedup still applies; prevents SFT blowout."""
    if not learn_uncapped_active():
        return 1_000_000
    max_g = int(os.getenv("HALIM_LEARN_UNCAPPED_MAX_GOLD", "40"))
    state = _load_state()
    used = int((state.get("counts") or {}).get("learn_gold_exported", 0))
    return max(0, max_g - used)


def record_learn_gold_exported(n: int = 1) -> None:
    if n <= 0 or not learn_uncapped_active():
        return
    with _lock:
        state = _load_state()
        counts = state.setdefault("counts", {})
        counts["learn_gold_exported"] = int(counts.get("learn_gold_exported", 0)) + int(n)
        _save_state(state)


def _audit(event: str, domain: str, ok: bool, detail: Dict[str, Any]) -> None:
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "domain": domain,
        "ok": ok,
        **detail,
    }
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _path_forbidden_write(path: str) -> bool:
    norm = path.replace("\\", "/").strip().lstrip("./")
    base = os.path.basename(norm)
    if base == ".env" or base.startswith(".env."):
        return True
    for forbidden in FORBIDDEN_WRITE_PATHS:
        if forbidden.endswith("/"):
            if norm.startswith(forbidden) or ("/" + forbidden) in norm:
                return True
        elif norm == forbidden or norm.endswith("/" + forbidden):
            return True
    return False


def _path_allowed_write(path: str) -> bool:
    if _path_forbidden_write(path):
        return False
    norm = path.replace("\\", "/").strip().lstrip("./")
    return any(norm.startswith(p) for p in ALLOWED_WRITE_PREFIXES)


def _check_rate(state: Dict[str, Any], key: str, limit: int) -> Tuple[bool, int]:
    counts = state.setdefault("counts", {})
    used = int(counts.get(key, 0))
    if used >= limit:
        return False, used
    counts[key] = used + 1
    return True, used + 1


def request_action(
    domain: str,
    action: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    cfg: Optional[BotConfig] = None,
) -> Tuple[bool, str]:
    """
    Gate any Halim action. Returns (allowed, reason).
    All frontier tools (API, web, git, shell, code) must call this first.
    """
    ctx = context or {}
    domain = str(domain).lower()
    action = str(action).lower()

    if not guardrails_enabled(cfg):
        return True, "guardrails_off"

    if kill_switch_active():
        _audit("blocked", domain, False, {"action": action, "reason": "kill_switch"})
        return False, "kill_switch_active"

    constitution = ensure_constitution()
    domains_on = constitution.get("domains_enabled") or {}
    if domain in DOMAINS and not domains_on.get(domain, False):
        _audit("blocked", domain, False, {"action": action, "reason": "domain_disabled"})
        return False, f"domain_disabled:{domain}"

    if action in (constitution.get("forbidden_forever") or []):
        _audit("blocked", domain, False, {"action": action, "reason": "forbidden_forever"})
        return False, f"forbidden:{action}"

    content = str(
        ctx.get("content") or ctx.get("prompt") or ctx.get("query") or ctx.get("text") or ""
    )
    if content and domain in ("generative", "agent", "api", "web", "coding"):
        try:
            from core.halim_frontier_policy import gate_frontier_request
            fp_ok, fp_reason = gate_frontier_request(content, domain, action, cfg)
            if not fp_ok:
                _audit("blocked", domain, False, {"action": action, "reason": fp_reason})
                return False, fp_reason
        except Exception:
            pass

    limits = constitution.get("rate_limits_daily") or DEFAULT_CONSTITUTION["rate_limits_daily"]
    autonomy = constitution.get("autonomy_mode", "bounded")

    with _lock:
        state = _load_state()
        state["day"] = _today()

        if domain == "git":
            if action in ("force_push", "delete_branch", "reset_hard"):
                _audit("blocked", domain, False, {"action": action})
                return False, "git_destructive_forbidden"
            ok, _ = _check_rate(state, "git_pushes", int(limits.get("git_pushes", 50)))
            if not ok:
                _save_state(state)
                return False, "git_push_daily_cap"

        elif domain == "web":
            url = str(ctx.get("url", ""))
            action_l = str(action).lower()
            policy = constitution.get("web_policy") or {}

            # Google AI search — query-only; no general browsing
            if action_l == "google_ai_search":
                q = str(ctx.get("query", "")).strip()
                if not q:
                    _save_state(state)
                    return False, "empty_google_query"
                if policy.get("google_ai_search_only") and not policy.get(
                    "google_ai_search_enabled", True
                ):
                    _save_state(state)
                    return False, "google_ai_search_disabled"
                ok, _ = _check_rate(
                    state, "google_ai_searches",
                    int(limits.get("google_ai_searches", int(os.getenv("HALIM_GOOGLE_AI_DAILY_CAP", "150")))),
                )
                if not ok:
                    _save_state(state)
                    return False, "google_ai_search_daily_cap"
            elif action_l == "learn_fetch":
                if not policy.get("learn_fetch_enabled", True):
                    _save_state(state)
                    return False, "learn_fetch_disabled"
                ok_url, url_reason = _validate_learn_url_guard(url, constitution)
                if not ok_url:
                    _save_state(state)
                    _audit("blocked", domain, False, {"url": url, "reason": url_reason})
                    return False, url_reason
                ok, _ = _check_rate(
                    state, "learn_fetches",
                    effective_learn_fetch_daily_cap(),
                )
                if not ok:
                    _save_state(state)
                    return False, "learn_fetch_daily_cap"
            elif action_l in ("fetch", "get"):
                if policy.get("google_ai_search_only"):
                    _save_state(state)
                    _audit("blocked", domain, False, {"action": action, "reason": "google_ai_only"})
                    return False, "use_learn_fetch_or_google_ai_search"
                host = urlparse(url).netloc.lower()
                allow = [h.lower() for h in (constitution.get("web_allowlist_hosts") or [])]
                if host and allow and host not in allow and not any(
                    host.endswith("." + a) for a in allow
                ):
                    _save_state(state)
                    _audit("blocked", domain, False, {"url": url, "host": host})
                    return False, f"web_host_not_allowlisted:{host}"
                ok, _ = _check_rate(state, "web_fetches", int(limits.get("web_fetches", 0)))
                if not ok:
                    _save_state(state)
                    return False, "web_daily_cap"
            else:
                if action_l != "google_ai_search":
                    _save_state(state)
                    return False, f"web_action_unknown:{action_l}"

        elif domain == "api":
            purpose = str(ctx.get("purpose", action))
            allow_p = constitution.get("api_allowlist_purposes") or []
            if purpose not in allow_p and action not in allow_p:
                _save_state(state)
                _audit("blocked", domain, False, {"purpose": purpose})
                return False, f"api_purpose_not_allowlisted:{purpose}"
            ok, _ = _check_rate(state, "api_calls", int(limits.get("api_calls", 500)))
            if not ok:
                _save_state(state)
                return False, "api_daily_cap"

        elif domain == "shell":
            if autonomy != "full":
                _save_state(state)
                _audit("blocked", domain, False, {"action": action, "autonomy": autonomy})
                return False, "shell_requires_full_autonomy_with_human_gate"
            ok, _ = _check_rate(state, "shell_commands", int(limits.get("shell_commands", 20)))
            if not ok:
                _save_state(state)
                return False, "shell_daily_cap"

        elif domain == "file":
            path = str(ctx.get("path", ""))
            if ctx.get("write") and path:
                if _path_forbidden_write(path):
                    _save_state(state)
                    _audit("blocked", domain, False, {"path": path})
                    return False, "forbidden_write_path"
                if not _path_allowed_write(path):
                    _save_state(state)
                    _audit("blocked", domain, False, {"path": path})
                    return False, "path_not_in_allowlist"
            ok, _ = _check_rate(state, "file_writes", int(limits.get("file_writes", 200)))
            if not ok:
                _save_state(state)
                return False, "file_write_daily_cap"

        elif domain == "agent":
            if autonomy == "bounded":
                _save_state(state)
                return False, "agent_requires_supervised_or_full_mode"
            ok, _ = _check_rate(state, "agent_steps", int(limits.get("agent_steps", 100)))
            if not ok:
                _save_state(state)
                return False, "agent_daily_cap"

        elif domain == "trade":
            pass  # trading physical limits enforced by cognitive_guardrails + risk.py

        _save_state(state)

    _audit("allowed", domain, True, {"action": action, **{k: v for k, v in ctx.items() if k != "body"}})
    return True, "ok"


def gate_mutation(param: str, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    """Extra gate for param self-modification."""
    ok, reason = request_action("trade", "param_mutation", {"param": param}, cfg=cfg)
    if not ok:
        return ok, reason
    constitution = ensure_constitution()
    with _lock:
        state = _load_state()
        limits = constitution.get("rate_limits_daily") or {}
        allowed, used = _check_rate(state, "param_mutations", int(limits.get("param_mutations", 30)))
        _save_state(state)
    if not allowed:
        return False, "param_mutation_daily_cap"
    return True, "ok"


def gate_git_push(reason: str = "", cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    return request_action("git", "push", {"reason": reason}, cfg=cfg)


def gate_web_fetch(url: str, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    constitution = ensure_constitution()
    policy = constitution.get("web_policy") or {}
    if policy.get("google_ai_search_only") and not policy.get("learn_fetch_enabled"):
        return False, "use_google_ai_search_or_learn_fetch"
    ok, reason = _validate_learn_url_guard(url, constitution)
    if ok:
        return gate_web_learn(url, cfg)
    return request_action("web", "fetch", {"url": url}, cfg=cfg)


def _validate_learn_url_guard(
    url: str, constitution: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Path/host check for read-only learn fetches (no POST, no edit URLs)."""
    if not url:
        return False, "empty_url"
    from core.halim_web_learn import validate_learn_url
    return validate_learn_url(url)


def gate_web_learn(url: str, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    """Read-only GET on allowlisted wiki/news/reference — Halim never changes external sites."""
    ok, reason = _validate_learn_url_guard(url)
    if not ok:
        return False, reason
    return request_action("web", "learn_fetch", {"url": url}, cfg=cfg)


def gate_google_ai_search(query: str, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    """Allow only text queries — Halim gets public Google AI Overview, no browse."""
    return request_action(
        "web", "google_ai_search", {"query": query.strip()[:300]}, cfg=cfg,
    )


def gate_api_call(purpose: str, cfg: Optional[BotConfig] = None, **ctx: Any) -> Tuple[bool, str]:
    return request_action("api", "call", {"purpose": purpose, **ctx}, cfg=cfg)


def gate_file_write(path: str, cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    return request_action("file", "write", {"path": path, "write": True}, cfg=cfg)


def guardrail_status(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    constitution = ensure_constitution()
    state = _load_state()
    return {
        "kill_switch": kill_switch_active(),
        "autonomy_mode": constitution.get("autonomy_mode"),
        "domains_enabled": constitution.get("domains_enabled"),
        "usage_today": state.get("counts", {}),
        "limits_daily": constitution.get("rate_limits_daily"),
        "audit_path": str(AUDIT_PATH),
    }


def log_guardrail_banner(cfg: Optional[BotConfig] = None) -> None:
    st = guardrail_status(cfg)
    constitution = ensure_constitution()
    policy = constitution.get("web_policy") or {}
    log.info("=" * 56)
    log.info("  🛡️ HALIM GUARDRAILS — frontier bounds active")
    log.info(f"  Kill switch: {'ACTIVE ⛔' if st['kill_switch'] else 'off'}")
    log.info(f"  Autonomy: {st.get('autonomy_mode', 'bounded')}")
    enabled = [k for k, v in (st.get("domains_enabled") or {}).items() if v]
    log.info(f"  Domains on: {', '.join(enabled)}")
    if policy.get("google_ai_search_only"):
        log.info(
            "  Web: Google AI Overview ONLY (public search, no Gemini API, no link visits)"
        )
    elif policy.get("learn_fetch_enabled"):
        hosts = policy.get("learn_allowlist_hosts") or []
        log.info(
            f"  Web: Google AI Overview + read-only learn ({len(hosts)} hosts, "
            f"max {policy.get('max_learn_bytes', 524288)} bytes/fetch, never edits external)"
        )
    log.info("=" * 56)


def apply_operator_frontier_settings(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """
    Operator-approved frontier settings: Google AI search + read-only wiki/news learning,
    supervised agents. Does not enable shell unless HALIM_SHELL_ENABLED.
    """
    if os.getenv("HALIM_OPERATOR_SETTINGS", "true").lower() not in ("1", "true", "yes"):
        return ensure_constitution()

    constitution = ensure_constitution()
    constitution["version"] = max(int(constitution.get("version", 1)), 4)
    constitution["autonomy_mode"] = os.getenv("HALIM_AUTONOMY", "supervised")
    domains = constitution.setdefault("domains_enabled", {})
    domains["web"] = True
    domains["agent"] = True
    domains["shell"] = os.getenv("HALIM_SHELL_ENABLED", "false").lower() in ("1", "true", "yes")

    learn_hosts = list(DEFAULT_CONSTITUTION["web_policy"]["learn_allowlist_hosts"])
    constitution["web_allowlist_hosts"] = [
        "www.google.com", "google.com", *learn_hosts,
    ]
    constitution["web_policy"] = {
        "google_ai_search_only": False,
        "google_ai_search_enabled": os.getenv("HALIM_GOOGLE_AI_SEARCH", "true").lower()
        in ("1", "true", "yes"),
        "learn_fetch_enabled": os.getenv("HALIM_WEB_LEARN", "true").lower()
        in ("1", "true", "yes"),
        "read_only_external": True,
        "no_post_requests": True,
        "no_external_edits": True,
        "no_gemini_api": True,
        "no_link_following": True,
        "no_arbitrary_browsing": True,
        "max_learn_bytes": int(os.getenv("HALIM_LEARN_MAX_BYTES", "524288")),
        "learn_allowlist_hosts": learn_hosts,
        "description": (
            "Google = AI Overview only. Learn = read-only GET on allowlisted wiki/news. "
            "Halim never edits or posts externally."
        ),
    }

    principles = constitution.setdefault("principles", [])
    learn_principle = (
        "External web = read-only — Halim learns from wiki/news but never edits, posts, "
        "or changes anything outside this repo."
    )
    if learn_principle not in principles:
        principles.append(learn_principle)

    forbidden = constitution.setdefault("forbidden_forever", [])
    for item in (
        "external_post", "wiki_edit", "form_submit", "external_write",
        "gemini_api_for_search", "follow_search_result_links",
    ):
        if item not in forbidden:
            forbidden.append(item)

    limits = constitution.setdefault("rate_limits_daily", {})
    limits["google_ai_searches"] = int(os.getenv("HALIM_GOOGLE_AI_DAILY_CAP", "150"))
    limits["learn_fetches"] = effective_learn_fetch_daily_cap()
    if learn_uncapped_active():
        log.info(
            f"📚 Halim learn UNCAPPED today ({_today()}) — "
            f"fetch≤{limits['learn_fetches']} gold≤{os.getenv('HALIM_LEARN_UNCAPPED_MAX_GOLD', '40')} "
            f"(normal cap {os.getenv('HALIM_LEARN_FETCH_DAILY_CAP', '500')} resumes tomorrow)"
        )
    limits["web_fetches"] = 0
    constitution["operator_enabled_at"] = datetime.now(timezone.utc).isoformat()
    CONSTITUTION_PATH.write_text(json.dumps(constitution, indent=2))
    log.info(
        "🛡️ Halim operator settings — trading-first · Google AI + read-only learn · "
        f"frontier safety (Gemini/Claude/OpenAI) · agents=supervised · "
        f"shell={'on' if domains.get('shell') else 'off'}"
    )
    try:
        from core.halim_frontier_policy import apply_frontier_policy_to_constitution
        apply_frontier_policy_to_constitution(cfg)
    except Exception:
        pass
    return constitution
