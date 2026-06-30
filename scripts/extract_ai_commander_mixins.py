#!/usr/bin/env python3
"""Extract AICommander methods into mixin modules."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "core" / "ai_commander.py"

VERDICT_METHODS = {
    "_stamp_council_signals", "_entry_verdict", "_emit_spike_verdict", "_finalize_entry_decision",
}

LEARNING_METHODS = {
    "_ring_entry_council_for_learning", "_schedule_deferred_entry",
    "ring_post_fill_learning", "ring_exit_for_deferred_learning", "_record_council_learning",
}

ENTRY_METHODS = {
    "_build_entry_bracket", "_entry_council_prompt", "_ring_halim_entry",
    "_await_halim_entry_slot", "_blend_halim_entry", "_resolve_halim_local_entry",
    "prefetch_entry_decision", "execute_ppo_led_entry_while_pending",
    "decide_entry", "poll_entry_council",
}

EXIT_METHODS = {
    "_ring_halim_exit", "_blend_halim_exit", "_apply_halim_exit_to_manage",
    "decide_stagnation", "poll_stagnation_council", "prefetch_stagnation",
    "decide_position_manage", "poll_position_council", "prefetch_position_manage",
    "decide_exit", "poll_exit_council", "decide_risk_exit", "poll_risk_exit_council",
    "_resolve_manage_prices",
}

GROUPS: Dict[str, Tuple[str, Set[str]]] = {
    "ai_commander_verdict": ("CommanderVerdictMixin", VERDICT_METHODS),
    "ai_commander_deferred": ("CommanderLearningMixin", LEARNING_METHODS),
    "ai_commander_entry": ("CommanderEntryMixin", ENTRY_METHODS),
    "ai_commander_exit": ("CommanderExitMixin", EXIT_METHODS),
}

MIXIN_IMPORTS = """
from core.ai_commander_verdict import CommanderVerdictMixin
from core.ai_commander_deferred import CommanderLearningMixin
from core.ai_commander_entry import CommanderEntryMixin
from core.ai_commander_exit import CommanderExitMixin
"""

HEADER = '''#!/usr/bin/env python3
"""Extracted from ai_commander — {title}."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log


class {cls}:
    """Mixin — composed into AICommander."""

'''


def _src(lines: List[str], node: ast.FunctionDef) -> str:
    return "".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    cls_node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AICommander")

    extracted = {k: [] for k in GROUPS}
    remain: List[ast.FunctionDef] = []
    for node in cls_node.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        body = _src(lines, node)
        for mod, (_, names) in GROUPS.items():
            if node.name in names:
                extracted[mod].append(body)
                break
        else:
            remain.append(node)

    for mod, (cls, _) in GROUPS.items():
        body = "".join(extracted[mod])
        if not body.strip():
            continue
        (ROOT / "core" / f"{mod}.py").write_text(
            HEADER.format(title=mod.replace("_", " "), cls=cls) + body, encoding="utf-8",
        )
        print(f"wrote {mod}.py: {len(extracted[mod])} methods")

    pre = "".join(lines[: cls_node.lineno - 1])
    if "CommanderVerdictMixin" not in pre:
        anchor = pre.rfind("\nfrom core.")
        line_end = pre.find("\n", anchor + 1) if anchor >= 0 else len(pre)
        pre = pre[: line_end + 1] + MIXIN_IMPORTS + pre[line_end + 1 :]

    class_line = lines[cls_node.lineno - 1].replace(
        "class AICommander:",
        "class AICommander(CommanderVerdictMixin, CommanderLearningMixin, "
        "CommanderEntryMixin, CommanderExitMixin):",
    )

    post = "".join(lines[cls_node.end_lineno or len(lines) :])
    new_text = pre + class_line + "".join(_src(lines, n) for n in remain) + post

    backup = SRC.with_suffix(".py.bak")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
    SRC.write_text(new_text, encoding="utf-8")
    print(f"ai_commander.py: {len(remain)} methods remain")


if __name__ == "__main__":
    main()
