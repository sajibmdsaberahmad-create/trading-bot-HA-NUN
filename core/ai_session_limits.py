#!/usr/bin/env python3
"""
core/ai_session_limits.py — AI-defined session limits (no fixed deploy/risk/pool caps).

When AI_DEFINE_ALL_LIMITS is on, deploy budget, risk/trade, watch pool, and max
positions are computed from equity + pilot rank (Ollama may refine asynchronously).
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.config import BotConfig
from core.notify import log
from core.pilot_mode import ai_full_capital_access, is_ai_unlimited

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner

_LIMITS_PATH = __import__("pathlib").Path("models/ai_session_limits.json")


def should_ai_define_limits(cfg: BotConfig) -> bool:
    """True when sizing/pool/risk should come from AI session logic, not fixed caps."""
    if getattr(cfg, "USE_FIXED_DEPLOY_CAP", False) or getattr(cfg, "USE_FIXED_RISK_CAP", False):
        return False
    env = __import__("os").getenv("AI_DEFINE_ALL_LIMITS", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    return bool(
        getattr(cfg, "AI_DEFINE_ALL_LIMITS", True)
        and getattr(cfg, "AI_FULL_CONTROL", True)
        and is_ai_unlimited(cfg)
    )


def get_session_limits(cfg: BotConfig) -> Dict[str, Any]:
    return dict(getattr(cfg, "_ai_session_limits", None) or {})


def get_session_limit(cfg: BotConfig, key: str, default: Any) -> Any:
    return get_session_limits(cfg).get(key, default)


def get_ai_risk_usd(cfg: BotConfig, account_equity: float = 0.0) -> Optional[float]:
    limits = get_session_limits(cfg)
    if limits.get("risk_per_trade_usd") is not None:
        return float(limits["risk_per_trade_usd"])
    pct = limits.get("risk_per_trade_pct")
    if pct is not None and account_equity > 0:
        return round(account_equity * float(pct), 2)
    return None


def heuristic_session_limits(
    cfg: BotConfig,
    equity: float,
    pilot: Any = None,
) -> Dict[str, Any]:
    """Equity + pilot rank → session limits (instant, no Ollama wait)."""
    eq = max(100.0, float(equity or getattr(cfg, "INITIAL_CASH", 1000)))
    pilot_mult = 1.0
    conf_adj = 1.0
    if pilot is not None:
        try:
            pilot_mult = max(0.5, min(2.0, float(pilot.get_max_position_size())))
            conf_adj = max(0.85, min(1.15, 0.55 / max(0.38, float(pilot.get_confidence_threshold()))))
        except Exception:
            pass

    max_pos = int(max(2, min(20, round((eq / 180.0) * pilot_mult))))
    watch = int(max(8, min(50, round((eq / 35.0) * pilot_mult))))
    risk_pct = float(min(0.10, max(0.015, 0.04 * conf_adj)))
    full_cap = ai_full_capital_access(cfg)
    deploy_pct = 0.95 if full_cap else float(min(0.95, max(0.12, 0.88 / max_pos)))
    cash_reserve = 0.0 if full_cap else float(max(0.0, min(0.12, 0.06 - (pilot_mult - 1.0) * 0.02)))

    return {
        "max_positions": max_pos,
        "watch_pool": watch,
        "risk_per_trade_pct": round(risk_pct, 4),
        "risk_per_trade_usd": round(eq * risk_pct, 2),
        "deploy_pct_per_slot": round(deploy_pct, 4),
        "min_cash_reserve_pct": round(cash_reserve, 4),
        "max_shares_per_trade": int(max(300, min(100_000, eq * 12))),
        "min_lock_score": 0.0,
        "source": "ai_heuristic",
        "equity_basis": round(eq, 2),
    }


def apply_session_limits(cfg: BotConfig, limits: Dict[str, Any]) -> None:
    """Store limits and mirror into cfg fields the rest of the stack reads."""
    cfg._ai_session_limits = dict(limits)
    if "max_positions" in limits:
        cfg.AI_MAX_CONCURRENT_POSITIONS = int(limits["max_positions"])
    if "watch_pool" in limits:
        cfg.AI_MAX_LOCKED_TARGETS = int(limits["watch_pool"])
    if "risk_per_trade_pct" in limits:
        cfg.RISK_PER_TRADE_PCT = float(limits["risk_per_trade_pct"])
    if "min_cash_reserve_pct" in limits:
        cfg.AI_MIN_CASH_RESERVE_PCT = float(limits["min_cash_reserve_pct"])
    if "max_shares_per_trade" in limits:
        cfg.AI_MAX_SHARES_PER_TRADE = int(limits["max_shares_per_trade"])
    if "min_lock_score" in limits:
        cfg.AI_MIN_LOCK_SCORE = float(limits["min_lock_score"])
    try:
        _LIMITS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LIMITS_PATH.write_text(json.dumps(limits, indent=2))
    except Exception:
        pass


def format_limits_log(cfg: BotConfig, equity: float = 0.0) -> str:
    lim = get_session_limits(cfg)
    if not lim:
        return (
            f"Max per trade: fixed | Multi-position: {getattr(cfg, 'MAX_CONCURRENT_POSITIONS', 5)} "
            f"| Watch pool: {getattr(cfg, 'MAX_LOCKED_TARGETS', 5)}"
        )
    eq = float(lim.get("equity_basis") or equity or getattr(cfg, "INITIAL_CASH", 1000))
    risk = get_ai_risk_usd(cfg, eq) or cfg.risk_amount_usd(eq)
    deploy_slot = round(eq * float(lim.get("deploy_pct_per_slot", 0.9)), 0)
    return (
        f"🧠 AI SESSION LIMITS ({lim.get('source', 'ai')}): "
        f"deploy≈${deploy_slot:,.0f}/slot ({lim.get('deploy_pct_per_slot', 0):.0%} of ${eq:,.0f}) | "
        f"risk=${risk:,.2f}/trade ({lim.get('risk_per_trade_pct', 0):.1%}) | "
        f"positions={lim.get('max_positions')} | watch={lim.get('watch_pool')} | "
        f"cash_reserve={lim.get('min_cash_reserve_pct', 0):.0%} | "
        f"fixed_deploy={getattr(cfg, 'USE_FIXED_DEPLOY_CAP', False)} | "
        f"fixed_risk={getattr(cfg, 'USE_FIXED_RISK_CAP', False)}"
    )


def _ollama_refine_limits(runner: "ScalperRunner", base: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    commander = getattr(runner, "ai_commander", None)
    if commander is None:
        return None
    eq = float(base.get("equity_basis", 1000))
    prompt = (
        "Set scalper session limits as JSON for this account. No fixed caps — AI decides.\n"
        f"Equity ${eq:,.0f} | heuristic: {json.dumps(base, default=str)[:400]}\n"
        "Keys: max_positions (2-20), watch_pool (8-50), risk_per_trade_pct (0.01-0.10), "
        "deploy_pct_per_slot (0.10-0.95), min_cash_reserve_pct (0-0.15), min_lock_score (0-30).\n"
        'JSON only: {"max_positions":N,"watch_pool":N,"risk_per_trade_pct":0.04,...,"reason":"brief"}'
    )
    out = commander.think_json(prompt, ttl=120.0, task="session_limits")
    if not out:
        return None
    refined = dict(base)
    for key in (
        "max_positions", "watch_pool", "risk_per_trade_pct",
        "deploy_pct_per_slot", "min_cash_reserve_pct", "min_lock_score",
    ):
        if out.get(key) is not None:
            refined[key] = out[key]
    if "risk_per_trade_pct" in refined:
        refined["risk_per_trade_usd"] = round(eq * float(refined["risk_per_trade_pct"]), 2)
    refined["source"] = "ai_ollama"
    refined["ollama_reason"] = str(out.get("reason", ""))[:200]
    return refined


def bootstrap_ai_session_limits(
    runner: "ScalperRunner",
    *,
    async_ollama: bool = True,
) -> Dict[str, Any]:
    """Apply AI session limits before trading loop starts."""
    cfg = runner.cfg
    if not should_ai_define_limits(cfg):
        return {}

    equity = float(getattr(runner, "account_equity", 0) or getattr(cfg, "INITIAL_CASH", 1000))
    pilot = getattr(runner, "pilot", None)
    limits = heuristic_session_limits(cfg, equity, pilot)
    apply_session_limits(cfg, limits)
    log.info(format_limits_log(cfg, equity))

    if async_ollama and getattr(cfg, "AI_SESSION_LIMITS_OLLAMA", True) and getattr(runner, "ai_commander", None):

        def _worker():
            try:
                refined = _ollama_refine_limits(runner, limits)
                if refined:
                    apply_session_limits(cfg, refined)
                    log.info(format_limits_log(cfg, equity))
            except Exception as exc:
                log.debug(f"Ollama session limits refine: {exc}")

        threading.Thread(target=_worker, name="ai-session-limits", daemon=True).start()

    return limits


def maybe_refresh_session_limits(runner: "ScalperRunner", min_interval_sec: float = 900.0) -> None:
    """Re-tune limits if equity moved materially (e.g. after several trades)."""
    cfg = runner.cfg
    if not should_ai_define_limits(cfg):
        return
    last = float(getattr(cfg, "_ai_limits_refresh_at", 0))
    if time.time() - last < min_interval_sec:
        return
    old_eq = float(get_session_limits(cfg).get("equity_basis", 0))
    equity = float(getattr(runner, "account_equity", 0) or old_eq)
    if old_eq > 0 and abs(equity - old_eq) / old_eq < 0.08:
        return
    pilot = getattr(runner, "pilot", None)
    limits = heuristic_session_limits(cfg, equity, pilot)
    apply_session_limits(cfg, limits)
    cfg._ai_limits_refresh_at = time.time()
    log.info(f"🔄 AI limits refreshed for equity ${equity:,.0f} | {format_limits_log(cfg, equity)}")
