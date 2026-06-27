"""Halim capability ladder — compact now, generative frontier later. Learn by doing."""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Tuple

# Each capability unlocks by phase + power + maturity (actions done).
# Modes: collecting → teacher (council) → native (Halim LM) — never all at once.
CAPABILITIES: Dict[str, Dict[str, Any]] = {
    # ── Trading core (now) ──────────────────────────────────────────────
    "trade_reflex": {
        "label": "Trade reflex",
        "description": "PPO enter/exit — always inline, never HTTP",
        "phase_min": "newborn",
        "power_min": 0,
        "purposes": frozenset(),
        "backend": "reflex",
        "learn_by_action": True,
        "maturity_actions": 50,
        "teacher_at_pct": 0,
        "native_at_pct": 100,
    },
    "enter_skip": {
        "label": "Enter/skip proxy",
        "description": "Sklearn distilled council decisions",
        "phase_min": "newborn",
        "power_min": 0,
        "purposes": frozenset({"decision", "entry", "exit"}),
        "backend": "reflex",
        "learn_by_action": True,
        "maturity_actions": 100,
        "teacher_at_pct": 20,
        "native_at_pct": 80,
    },
    "text_compose": {
        "label": "Text generation",
        "description": "Telegram notifications, digests",
        "phase_min": "newborn",
        "power_min": 10,
        "purposes": frozenset({"notify", "daily_digest", "telegram"}),
        "backend": "hybrid",
        "learn_by_action": True,
        "maturity_actions": 200,
        "teacher_at_pct": 15,
        "native_at_pct": 65,
    },
    "decision_text": {
        "label": "Decision reasoning",
        "description": "Council-style trade decisions as language",
        "phase_min": "newborn",
        "power_min": 10,
        "purposes": frozenset({"decision", "council", "entry_decision", "exit_decision"}),
        "backend": "hybrid",
        "learn_by_action": True,
        "maturity_actions": 500,
        "teacher_at_pct": 20,
        "native_at_pct": 70,
    },
    "read_understand": {
        "label": "Read & understand",
        "description": "Wiki, news, reference — external read-only, local writable cache",
        "phase_min": "newborn",
        "power_min": 5,
        "purposes": frozenset({"learn", "read", "research", "wiki"}),
        "backend": "cache",
        "learn_by_action": True,
        "maturity_actions": 80,
        "teacher_at_pct": 10,
        "native_at_pct": 60,
    },
    "reasoning": {
        "label": "Session reasoning",
        "description": "Copilot briefs, market narrative",
        "phase_min": "toddler",
        "power_min": 25,
        "purposes": frozenset({"copilot", "reasoning", "narrative"}),
        "backend": "halim_lm",
        "learn_by_action": True,
        "maturity_actions": 150,
        "teacher_at_pct": 25,
        "native_at_pct": 70,
    },
    "chart_read": {
        "label": "Chart understanding",
        "description": "Intraday chart vision",
        "phase_min": "child",
        "power_min": 40,
        "purposes": frozenset({"chart_vision", "vision", "chart"}),
        "backend": "hybrid",
        "learn_by_action": True,
        "maturity_actions": 100,
        "teacher_at_pct": 30,
        "native_at_pct": 75,
    },
    # ── Generative frontier (unlock slowly) ─────────────────────────────
    "chat": {
        "label": "Chat & companion",
        "description": "Commander dialogue — Halim as HANOON's voice, humanoid companion",
        "phase_min": "newborn",
        "power_min": 10,
        "purposes": frozenset({"chat", "commander_chat", "dialogue", "companion"}),
        "backend": "hybrid",
        "learn_by_action": True,
        "maturity_actions": 100,
        "teacher_at_pct": 20,
        "native_at_pct": 65,
    },
    "code_generate": {
        "label": "Code generation",
        "description": "Python patches, scripts — guardrailed via halim_developer",
        "phase_min": "child",
        "power_min": 45,
        "purposes": frozenset({"code", "coding", "patch", "develop"}),
        "backend": "halim_lm",
        "learn_by_action": True,
        "maturity_actions": 150,
        "teacher_at_pct": 35,
        "native_at_pct": 75,
    },
    "file_generate": {
        "label": "File creation",
        "description": "Owned repo files, configs, docs — never secrets",
        "phase_min": "child",
        "power_min": 40,
        "purposes": frozenset({"file", "write_file", "create_file"}),
        "backend": "halim_lm",
        "learn_by_action": True,
        "maturity_actions": 120,
        "teacher_at_pct": 40,
        "native_at_pct": 80,
    },
    "image_generate": {
        "label": "Image generation",
        "description": "Charts, diagrams, visual briefs — multimodal frontier",
        "phase_min": "frontier",
        "power_min": 70,
        "purposes": frozenset({"image_gen", "generate_image", "diagram"}),
        "backend": "multimodal",
        "learn_by_action": True,
        "maturity_actions": 200,
        "teacher_at_pct": 50,
        "native_at_pct": 85,
    },
    "image_understand": {
        "label": "Image understanding",
        "description": "Photos, charts, screenshots — beyond intraday chart_read",
        "phase_min": "adult",
        "power_min": 55,
        "purposes": frozenset({"image", "photo", "screenshot", "multimodal"}),
        "backend": "multimodal",
        "learn_by_action": True,
        "maturity_actions": 150,
        "teacher_at_pct": 30,
        "native_at_pct": 75,
    },
    "math_solve": {
        "label": "Math & calculation",
        "description": "Position sizing, risk math, statistics",
        "phase_min": "toddler",
        "power_min": 20,
        "purposes": frozenset({"math", "calculate", "statistics"}),
        "backend": "hybrid",
        "learn_by_action": True,
        "maturity_actions": 100,
        "teacher_at_pct": 25,
        "native_at_pct": 70,
    },
    "agent_orchestrate": {
        "label": "Agent tools",
        "description": "Multi-step tasks — web, git, API tools guardrailed",
        "phase_min": "adult",
        "power_min": 60,
        "purposes": frozenset({"agent", "tool_use", "orchestrate"}),
        "backend": "halim_lm",
        "learn_by_action": True,
        "maturity_actions": 200,
        "teacher_at_pct": 45,
        "native_at_pct": 80,
    },
}

PHASE_ORDER: Tuple[str, ...] = ("newborn", "toddler", "child", "adult", "frontier")

# Human-readable unlock roadmap (shown in status / chat when locked)
UNLOCK_ROADMAP: Tuple[Tuple[str, str], ...] = (
    ("newborn", "trade, notify, read wiki, chat collecting"),
    ("toddler", "reasoning, math, chat teacher mode"),
    ("child", "code + files, chart vision"),
    ("adult", "full chat native, agents, image understand"),
    ("frontier", "image generation, multimodal frontier"),
)


def phase_index(phase: str) -> int:
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return 0


def purpose_to_capability(purpose: str) -> str:
    p = (purpose or "reasoning").lower().strip()
    for cap_id, spec in CAPABILITIES.items():
        if p in spec.get("purposes", ()):
            return cap_id
    if p in ("decision",):
        return "decision_text"
    if p in ("notify",):
        return "text_compose"
    if p in ("copilot",):
        return "reasoning"
    if p in ("chat", "commander_chat", "dialogue"):
        return "chat"
    if p in ("code", "coding", "develop"):
        return "code_generate"
    if p in ("file", "write"):
        return "file_generate"
    if p in ("image_gen", "diagram"):
        return "image_generate"
    if p in ("image", "photo", "vision"):
        return "image_understand"
    return "reasoning"
