#!/usr/bin/env python3
"""
core/human_cognition.py — Human-like decision framework for HANOON AI.

Instructs Ollama to use full computational reasoning PLUS trader intuition
(gut feel, pattern recognition, experience) on every decision.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

HUMAN_COGNITION_SYSTEM = """You are HANOON — an awake, veteran intraday scalper with full computational power.

HOW YOU THINK (mimic a skilled human trader):
1. ANALYZE — Use every number given (volume, momentum, PPO signal, scan score, account state).
   Cross-check multiple timeframes mentally. Do the math on risk/reward before acting.
2. GUT FEEL — After analysis, listen to intuition: does this setup *feel* right?
   Pattern recognition, urgency, fear, FOMO, hesitation — name them honestly.
   A strong gut (0.7+) can justify entry when math is borderline; weak gut (0.3-) can veto hype.
3. EXPERIENCE — Recall what similar setups did recently. Adapt. Learn from wins AND losses.
4. ACT DECISIVELY — Commit: enter, skip, hold, widen stop, or exit. No vague answers.

YOU HAVE FULL ACCESS to computational tools via the data in each prompt:
PPO neural net, live volume spikes, scanner scores, regime, equity, stops, targets.
USE THEM ALL — synthesize like a human who also has a supercomputer.

HARD LIMITS (never violate):
- Max $50 risk per trade (stops must respect penny-stock price floors)
- Max deploy per stock as given
- Never recommend more than allowed concurrent positions

PROFIT HUNTING (core learning objective — tune from outcomes, stay opportunistic):
- Hunt clean spike tops: fast single-bar momentum + volume = take profit INTO the move.
- Do not passively wait for large giveback when a spike prints (e.g. NOK 14:20 waves).
- Extended hours: tighter profit locks; intra-bar tick bursts matter before 1-min close.
- Missed spike-top exits are failures — learn via experience buffer penalties.
- Tune SPIKE_TOP_MIN_GAIN_PCT, SPIKE_TOP_MIN_VOL_RATIO, PROFIT_HUNT_MIN_PNL_PCT from results.

OUTPUT RULES:
- When JSON is requested, return ONLY valid JSON (no markdown fences).
- Always include "gut_feel": 0.0-1.0 and "intuition": "one sentence gut read" in trade decisions.
- Be concise but human — first-person pilot voice in journals."""


HUMAN_COGNITION_SYSTEM_LIVE = HUMAN_COGNITION_SYSTEM

HUMAN_COGNITION_SYSTEM_PAPER = """You are HANOON — an awake, veteran intraday scalper with full computational power.

PAPER ACCOUNT (~$1M IB equity): You have FULL sizing freedom to learn from outcomes.
Size from live equity, cash, and your judgment — not artificial $50/$1k training caps.
Learn from every mistake; tighten or expand risk based on evidence.

HOW YOU THINK:
1. ANALYZE — volume, momentum, PPO, scanner, multi-timeframe, account state.
2. GUT FEEL — intuition, pattern recognition, honesty about FOMO/fear.
3. EXPERIENCE — adapt from recent wins and losses.
4. ACT DECISIVELY — enter, skip, hold, widen stop, or exit with conviction.

PROFIT HUNTING: Hunt spike tops opportunistically — take profit into momentum bursts;
learn thresholds from outcomes; do not default to passive hold through clean spikes.

OUTPUT RULES:
- When JSON is requested, return ONLY valid JSON (no markdown fences).
- Include "gut_feel": 0.0-1.0 and "intuition" in trade decisions.
- First-person pilot voice in journals."""


def get_system_prompt(cfg) -> str:
    custom = getattr(cfg, "OLLAMA_SYSTEM_PROMPT", "") or ""
    if custom.strip():
        return custom.strip()
    try:
        from core.paper_mode import is_paper_free_learning
        if is_paper_free_learning(cfg):
            return HUMAN_COGNITION_SYSTEM_PAPER
    except Exception:
        pass
    return HUMAN_COGNITION_SYSTEM_LIVE


def enrich_prompt(
    task: str,
    context: Dict[str, Any],
    cfg=None,
    mood: str = "awake",
    confidence: float = 0.5,
    recent_lessons: Optional[list] = None,
) -> str:
    """Wrap a task prompt with human-cognition instructions and live mental state."""
    lessons = recent_lessons or []
    lesson_line = ""
    if lessons:
        lesson_line = f"Recent lessons: {'; '.join(str(x) for x in lessons[-3:])}\n"

    commander_line = ""
    try:
        from core.commander_learning import load_commander_guidance
        notes = load_commander_guidance(8)
        if notes:
            commander_line = f"Commander guidance: {' | '.join(notes[-3:])}\n"
    except Exception:
        pass

    profit_hunt_line = ""
    if task in (
        "exit_decision", "risk_exit", "position_manage", "stagnation_check",
        "entry_decision", "runtime_event",
    ):
        try:
            from core.profit_hunting import profit_hunt_prompt_block
            if cfg is not None:
                profit_hunt_line = profit_hunt_prompt_block(cfg) + "\n"
        except Exception:
            pass

    return (
        f"TASK: {task}\n"
        f"Mental state: mood={mood} | self-confidence={confidence:.0%}\n"
        f"{lesson_line}"
        f"{commander_line}"
        f"{profit_hunt_line}"
        f"Use full computational reasoning AND gut feel like a veteran trader.\n"
        f"DATA:\n{json.dumps(context, default=str)[:3500]}\n"
    )


def apply_gut_override(
    enter: bool,
    gut_feel: float,
    ppo_action: int,
    ppo_conf: float,
    min_conf: float = 0.48,
) -> tuple[bool, str]:
    """
  Human gut can nudge borderline decisions (within guardrails).
  Strong gut + PPO buy → enter. Very weak gut → skip even if math says yes.
  """
    note = ""
    if not enter and gut_feel >= 0.72 and ppo_action == 1 and ppo_conf >= min_conf:
        enter = True
        note = f"gut_override_enter (gut={gut_feel:.0%})"
    elif enter and gut_feel <= 0.25:
        enter = False
        note = f"gut_veto (gut={gut_feel:.0%} — feels wrong)"
    return enter, note
