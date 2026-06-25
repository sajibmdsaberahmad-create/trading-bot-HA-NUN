#!/usr/bin/env python3
"""
core/self_improver.py — Self-improvement engine that generates actionable
trading instructions from experience, regime changes, and performance data.

Outputs human-readable guidelines AND machine-usable parameter adjustments.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.experience_buffer import load_all, stats as buffer_stats
from core.notify import log
from core.pilot_experience import PilotExperienceSystem
from core.pattern_memory_bank import PatternMemoryBank

logger = logging.getLogger("SELF_IMPROVER")

MODELS_DIR = Path("models")
GUIDELINES_PATH = MODELS_DIR / "ai_guidelines.txt"
ADJUSTMENTS_PATH = MODELS_DIR / "parameter_adjustments.json"
HISTORY_PATH = MODELS_DIR / "improvement_history.json"


def _load_backtest_returns() -> List[float]:
    returns = []
    results_dir = Path("backtest_results")
    if not results_dir.exists():
        return returns
    for path in results_dir.glob("*.csv"):
        try:
            df = pd.read_csv(path)
            if "total_return_pct" in df.columns:
                returns.extend(df["total_return_pct"].dropna().tolist())
        except Exception:
            continue
    for path in results_dir.glob("*.json"):
        try:
            with open(path, "r") as f:
                payload = json.load(f)
            results = payload.get("results", payload.get("ticker_results", []))
            for r in results:
                ret = r.get("total_return_pct")
                if ret is not None:
                    returns.append(float(ret))
        except Exception:
            continue
    return returns


def _load_performance_returns() -> List[float]:
    path = Path("performance.csv")
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
        # infer per-trade return from portfolio value changes if available
        if "portfolio_value" in df.columns:
            pv = df["portfolio_value"].dropna().values
            if len(pv) > 1:
                returns = ((pv[1:] / pv[:-1]) - 1) * 100
                return returns.tolist()
    except Exception:
        pass
    return []


def _win_rate_from_returns(returns: List[float]) -> float:
    if not returns:
        return 0.5
    wins = sum(1 for r in returns if r > 0)
    return wins / len(returns)


def _analyze_regime_behavior(regime: str, returns: List[float]) -> Dict[str, Any]:
    if not returns:
        return {"regime": regime, "samples": 0, "win_rate": 0.5}
    wr = _win_rate_from_returns(returns)
    avg_ret = float(np.mean(returns))
    vol = float(np.std(returns)) if len(returns) > 1 else 0.0
    return {
        "regime": regime,
        "samples": len(returns),
        "win_rate": round(wr, 3),
        "avg_return": round(avg_ret, 3),
        "volatility": round(vol, 3),
    }


def generate_self_improvement_plan(cfg: BotConfig) -> Dict[str, Any]:
    """
    Produce:
    - human-readable guidelines
    - machine-ready parameter adjustments (safe ranges)
    - improvement history entry
    """
    log.info("🧬 Generating self-improvement plan...")

    buffer = buffer_stats()
    returns_all = _load_backtest_returns() + _load_performance_returns()

    # Existing weights
    weights = {}
    weights_path = MODELS_DIR / "scalper_weights.json"
    if weights_path.exists():
        try:
            with open(weights_path, "r") as f:
                weights = json.load(f)
        except Exception:
            pass

    # Win rate from experience
    buffer_wr = buffer.get("win_rate", 0.5)
    returns_wr = _win_rate_from_returns(returns_all)
    combined_wr = (buffer_wr + returns_wr) / 2.0

    # ----------------------------
    # Parameter adjustment logic (bounds from paper vs live mode)
    # ----------------------------
    from core.param_bounds import effective_param_bounds
    bounds = effective_param_bounds(cfg)
    adjustments: Dict[str, Any] = {}

    def _adj(key: str, current: float, suggested: float, low: float, high: float, reason: str):
        if key in bounds:
            low, high = bounds[key]
        safe = max(low, min(high, suggested))
        if safe != current:
            adjustments[key] = {
                "old": current,
                "new": safe,
                "reason": reason,
                "confidence": round(abs(safe - current) / (high - low), 3),
            }

    # Risk / stops
    _adj("SCALP_STOP_ATR_MULTIPLIER", cfg.SCALP_STOP_ATR_MULTIPLIER,
         cfg.SCALP_STOP_ATR_MULTIPLIER * (0.8 if combined_wr < 0.4 else 1.1 if combined_wr > 0.7 else 1.0),
         0.3, 2.0, "tighten stops when WR low; widen when WR strong")

    _adj("SCALP_TP_ATR_MULTIPLIER", cfg.SCALP_TP_ATR_MULTIPLIER,
         cfg.SCALP_TP_ATR_MULTIPLIER * (1.15 if combined_wr > 0.7 else 0.9 if combined_wr < 0.4 else 1.0),
         0.5, 4.0, "increase targets when WR strong; reduce when WR weak")

    _adj("SCAN_INTERVAL_SECONDS", cfg.SCAN_INTERVAL_SECONDS,
         cfg.SCAN_INTERVAL_SECONDS * (1.3 if combined_wr < 0.4 else 0.8 if combined_wr > 0.7 else 1.0),
         60, 900, "reduce frequency when WR low; increase when WR strong")

    base_trade = float(getattr(cfg, "MAX_TRADE_SIZE_USD", 0) or 0)
    if base_trade <= 0:
        from core.paper_mode import account_equity as resolve_account_equity
        base_trade = resolve_account_equity(cfg) * 0.05
    _adj("MAX_TRADE_SIZE_USD", base_trade,
         base_trade * (0.7 if combined_wr < 0.4 else 1.1 if combined_wr > 0.7 else 1.0),
         100, 5000, "reduce size when WR poor; increase when WR strong")

    _adj("MAX_RISK_PER_TRADE_USD", cfg.MAX_RISK_PER_TRADE_USD,
         cfg.MAX_RISK_PER_TRADE_USD * (0.75 if combined_wr < 0.4 else 1.1 if combined_wr > 0.65 else 1.0),
         15, 100, "tighten per-trade risk when WR low; relax slightly when WR strong")

    _adj("HARD_STOP_USD", cfg.HARD_STOP_USD,
         cfg.HARD_STOP_USD * (0.9 if combined_wr < 0.4 else 1.0),
         25, 100, "tighter hard stop when WR weak")

    _adj("CONFIDENCE_THRESHOLD", cfg.CONFIDENCE_THRESHOLD,
         cfg.CONFIDENCE_THRESHOLD + (0.05 if combined_wr < 0.4 else -0.03 if combined_wr > 0.7 else 0),
         0.35, 0.90, "raise entry bar when WR low; lower when WR strong")

    # Adjust heuristic weights from market regime context
    if weights:
        factor = 1.0 + (combined_wr - 0.5) * 0.4
        for k in weights:
            if k.startswith("_"):
                continue
            weights[k] = max(0.5, min(weights.get(k, 1.0) * factor, 50.0))
        weights["_meta"] = {
            "train_timestamp": datetime.utcnow().isoformat(),
            "buffer_total": buffer.get("total", 0),
            "buffer_win_rate": round(buffer_wr, 3),
            "returns_win_rate": round(returns_wr, 3),
            "combined_win_rate": round(combined_wr, 3),
        }
        weights_path.parent.mkdir(exist_ok=True)
        with open(weights_path, "w") as f:
            json.dump(weights, f, indent=2)
        log.info(f"🧠 Updated rule weights from combined WR={combined_wr:.1%}")

    # ----------------------------
    # Human-readable guidelines
    # ----------------------------
    lines: List[str] = []
    lines.append(f"🧭 SELF-IMPROVEMENT GUIDELINES | {datetime.utcnow().isoformat()}")
    lines.append(f"Combined win rate: {combined_wr:.1%} (buffer={buffer_wr:.1%}, returns={returns_wr:.1%})")

    if combined_wr < 0.4:
        lines.append("• Win rate below 40%: tighten SCALP_STOP_ATR_MULTIPLIER and SCALP_TP_ATR_MULTIPLIER")
        lines.append("• Reduce SCAN_INTERVAL_SECONDS to avoid overtrading")
        lines.append("• Lower MAX_TRADE_SIZE_USD to preserve capital")
        lines.append("• Add 5-min confirmation before entry to filter noise")
    elif combined_wr > 0.7:
        lines.append("• Win rate strong: increase MAX_TRADE_SIZE_USD within risk limits")
        lines.append("• Consider widening SCALP_TP_ATR_MULTIPLIER to capture larger trends")
        lines.append("• Maintain current SCALP_STOP_ATR_MULTIPLIER but monitor for degradation")
    else:
        lines.append("• Win rate stable: maintain current parameters")
        lines.append("• Avoid multiple parameter changes simultaneously; isolate effects")

    if buffer.get("avg_reward", 0) < 0:
        lines.append("• Average reward is negative — review exit logic and trailing-stop settings")

    try:
        from core.commander_learning import load_commander_guidance
        notes = load_commander_guidance(6)
        if notes:
            lines.append("• Commander guidance (recent):")
            for n in notes[-4:]:
                lines.append(f"  - {n[:120]}")
    except Exception:
        pass

    if adjustments:
        lines.append("• Recommended parameter adjustments:")
        for k, v in adjustments.items():
            lines.append(f"  - {k}: {v['old']} -> {v['new']} (reason: {v['reason']})")

    # Apply adjustments to config file automatically
    _apply_adjustments(cfg, adjustments)

    guidelines = "\n".join(lines)
    with open(GUIDELINES_PATH, "w") as f:
        f.write(guidelines)
        f.write("\n")

    with open(ADJUSTMENTS_PATH, "w") as f:
        json.dump(adjustments, f, indent=2)

    # History
    history = []
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH, "r") as f:
                history = json.load(f)
        except Exception:
            pass
    history.append({
        "timestamp": datetime.utcnow().isoformat(),
        "win_rate": combined_wr,
        "buffer_win_rate": buffer_wr,
        "returns_win_rate": returns_wr,
        "adjustments": adjustments,
        "guidelines_summary": guidelines[:300],
    })
    with open(HISTORY_PATH, "w") as f:
        json.dump(history[-100:], f, indent=2)

    log.info("🧬 Self-improvement plan generated and applied.")
    return {
        "guidelines": guidelines,
        "adjustments": adjustments,
        "win_rate": combined_wr,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _apply_adjustments(cfg: BotConfig, adjustments: Dict[str, Any]):
    """Apply numeric adjustments in-memory — clamped to learning bounds."""
    from core.param_bounds import clamp_param_value, is_tunable, normalize_param

    for key, meta in adjustments.items():
        param = normalize_param(key)
        if not is_tunable(param, cfg) or not hasattr(cfg, param):
            continue
        old = getattr(cfg, param)
        raw_new = meta.get("new") if isinstance(meta, dict) else meta
        clamped, ok, _ = clamp_param_value(param, raw_new, current=old, cfg=cfg)
        if not ok:
            continue
        try:
            if isinstance(old, int) and not isinstance(old, bool):
                new = int(round(float(clamped)))
            else:
                new = float(clamped)
        except (TypeError, ValueError):
            continue
        if new != old:
            setattr(cfg, param, new)
            log.info(f"⚙️ Self-improvement: {param} {old} -> {new}")


def generate_guidelines_text() -> str:
    """Return latest guidelines file content for notifications."""
    if GUIDELINES_PATH.exists():
        return GUIDELINES_PATH.read_text()
    return "No guidelines available. Run generate_self_improvement_plan() first."


def generate_veteran_progression_plan(cfg: BotConfig) -> Dict[str, Any]:
    """
    Generate adjustments based on veteran level and skill points.
    Higher-level pilots get access to more aggressive parameters.
    """
    lines: List[str] = []
    lines.append(f"🧭 VETERAN PROGRESSION PLAN | {datetime.utcnow().isoformat()}")
    
    # Load current veteran status
    pilot = PilotExperienceSystem(cfg)
    status = pilot.get_veteran_status()
    level = status["level"]
    xp = status["total_xp"]
    skill_mods = pilot.get_skill_modifiers()
    
    adjustments: Dict[str, Any] = {}
    
    lines.append(f"Current Rank: {level} ({xp} XP)")
    lines.append(f"Skill Points: {status['skill_points']}")
    
    # Level-based parameter adjustments
    if level == "Cadet":
        lines.append("• Cadet restrictions: Conservative entry threshold")
        lines.append("• Learning phase: High confidence required (85%)")
        adjustments["CONFIDENCE_THRESHOLD"] = {"old": cfg.CONFIDENCE_THRESHOLD, "new": 0.85, "reason": "Cadet level - high caution"}
        
    elif level == "Rookie":
        lines.append("• Rookie phase: Building confidence, moderate threshold")
        lines.append("• Access to basic pattern matching enabled")
        adjustments["CONFIDENCE_THRESHOLD"] = {"old": cfg.CONFIDENCE_THRESHOLD, "new": 0.75, "reason": "Rookie level - building confidence"}
        
    elif level == "Aviator":
        lines.append("• Aviator status: Full strategy access unlocked")
        lines.append("• Position sizing at 1x multiplier")
        lines.append("• Confidence threshold: 65%")
        adjustments["CONFIDENCE_THRESHOLD"] = {"old": cfg.CONFIDENCE_THRESHOLD, "new": 0.65, "reason": "Aviator level - full access"}
        
    elif level == "Ace":
        lines.append("• Ace status: Elite trader privileges")
        lines.append("• Can adjust advanced parameters (TP/SL multipliers)")
        lines.append("• Confidence threshold: 55% (aggressive)")
        adjustments["CONFIDENCE_THRESHOLD"] = {"old": cfg.CONFIDENCE_THRESHOLD, "new": 0.55, "reason": "Ace level - aggressive trading"}
        
    elif level == "Veteran":
        lines.append("• Veteran status: Master trader")
        lines.append("• Full autonomy: Can modify core strategies")
        lines.append("• Confidence threshold: 45% (expert)")
        adjustments["CONFIDENCE_THRESHOLD"] = {"old": cfg.CONFIDENCE_THRESHOLD, "new": 0.45, "reason": "Veteran level - expert confidence"}
    
    # Skill-based modifiers
    for skill, modifier in skill_mods.items():
        if modifier > 1.2:
            lines.append(f"• {skill} is highly developed ({modifier:.1f}x) — leverage this strength")
    
    # Apply adjustments
    for key, meta in adjustments.items():
        if hasattr(cfg, key):
            setattr(cfg, key, meta["new"])
            log.info(f"⚙️ Veteran progression: {key} -> {meta['new']}")
    
    guidelines = "\n".join(lines)
    
    # Save to file
    with open(GUIDELINES_PATH, "w") as f:
        f.write(guidelines)
        f.write("\n")
    
    return {
        "veteran_level": level,
        "xp": xp,
        "adjustments": adjustments,
        "skill_modifiers": skill_mods,
    }