#!/usr/bin/env python3
"""Extract ScalperRunner methods into mixin modules (preserves class header)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "core" / "scalper_runner.py"

EXIT_METHODS: Set[str] = {
    "_credit_exit_proceeds", "_detect_exit", "_ensure_position_stream",
    "_request_deferred_exit", "_service_deferred_exits", "_detect_all_exits",
    "_build_trade_close_record", "_enqueue_pending_close", "_service_pending_closes",
    "_finalize_closed_trade", "_clear_closed_position_state",
    "_apply_trade_close_learning", "_record_early_exit_learning", "_exit_position",
    "commander_positions_intel", "commander_risk_summary",
    "commander_exit_ticker", "commander_exit_filtered",
    "_evaluate_profit_hunt_exit", "_ai_profit_decision_stalled",
    "_enforce_green_profit_lock", "_execute_mechanical_profit_exit",
    "_reset_profit_hunt_state", "_live_position_monitor", "_ai_manage_position",
    "_apply_stop_update", "_apply_target_update", "_should_exit_early",
    "_update_trailing_stops", "_deliberate_exit_council", "_deliberate_risk_exit",
    "_resolve_exit_council", "_resolve_risk_exit_council",
    "_apply_position_manage_decision", "_resolve_position_council",
    "_resolve_stagnation_council", "_monitor_all_open_positions",
    "_service_tick_position_exit",
}

ENTRY_METHODS: Set[str] = {
    "_entry_parent_price", "_entry_price_mode", "_stuck_entry_limit_px",
    "_ib_sync_enabled", "_ib_position_shares", "_confirm_entry_fill_from_ib",
    "_clear_pending_entry", "_bracket_for_entry_fill", "_clamp_entry_shares",
    "_open_position_from_fill", "_service_shadow_positions",
    "_service_pending_entry", "_service_one_pending_entry",
    "_submit_ai_entry", "_attempt_entry", "_attempt_hot_swap_entry",
    "_attempt_scan_bootstrap_entry", "_apply_war_sizing", "_apply_lottery_bank_sizing",
    "_resolve_entry_council", "_ai_gate_entry", "_build_ai_context",
}

SESSION_METHODS: Set[str] = {
    "_suspend_off_hours_market_data", "_resume_tradable_market_data",
    "_halt_trading_for_closed_market", "_on_day_session_end", "_on_pre_market_open", "_on_rth_open",
    "_register_shutdown_signals", "_shutdown_abort", "_shutdown",
    "_write_init_report", "_write_close_report", "_log_startup_banner",
    "_log_tick_stream_config", "_maybe_daily_push", "_train_off_hours",
    "_daily_self_train", "_schedule_self_train", "_write_live_metrics",
    "_on_ib_connectivity", "_on_ib_session_reclaim", "_resubscribe_all_streams",
    "_maybe_resume_ib_from_shadow",
}

SPIKE_METHODS: Set[str] = {
    "_service_tick_spike_queue", "_on_locked_stream_tick", "_fast_monitor_locked",
    "_detect_volume_spike", "_predict_slippage", "_scan_one", "_refine_scan_candidates",
    "_scan_and_rank", "_scan_and_rank_fast_lock", "_commit_scan_lock",
    "_schedule_bar_prefetch", "_prefetch_one_ticker_bars", "_drain_bar_prefetch_queue",
    "_warm_locked_bar_cache", "_tick_bar_warm_on_main", "_locked_target_rows",
    "_apply_lock_row_merge", "_maybe_soft_rotate_lock", "_maybe_merge_lock_from_scanner",
    "_maybe_release_stale_lock", "_maybe_rotate_locked_focus",
    "_generative_review_locks", "_focused_ticker", "_service_stream_repairs",
    "_ensure_focus_stream", "_ensure_locked_streams", "_ensure_target_stream",
    "_start_target_stream", "_stop_target_stream", "_stop_all_target_streams",
    "_silent_background_watch", "_refresh_locked_bars", "_prefetch_live_ai_hotline",
    "_score_ticker", "_ai_score_ticker", "_store_scan_cache",
    "_detect_tick_volume_burst", "_log_flat_heartbeat",
}

GROUPS: Dict[str, Tuple[str, Set[str]]] = {
    "scalper_exit_executor": ("ScalperExitMixin", EXIT_METHODS),
    "scalper_entry_executor": ("ScalperEntryMixin", ENTRY_METHODS),
    "scalper_session": ("ScalperSessionMixin", SESSION_METHODS),
    "scalper_spike_loop": ("ScalperSpikeMixin", SPIKE_METHODS),
}

MIXIN_IMPORTS = """
from core.scalper_exit_executor import ScalperExitMixin
from core.scalper_entry_executor import ScalperEntryMixin
from core.scalper_session import ScalperSessionMixin
from core.scalper_spike_loop import ScalperSpikeMixin
"""

MIXIN_HEADER = '''#!/usr/bin/env python3
"""Extracted from scalper_runner — {title}."""

from __future__ import annotations

from core.scalper_mixin_imports import *  # noqa: F403

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    pass


class {cls}:
    """Mixin — composed into ScalperRunner."""

'''


def _extract_source(lines: List[str], node: ast.FunctionDef) -> str:
    return "".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])


def main() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    class_node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ScalperRunner"
    )

    extracted: Dict[str, List[str]] = {k: [] for k in GROUPS}
    remaining_nodes: List[ast.FunctionDef] = []

    for node in class_node.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        src = _extract_source(lines, node)
        placed = False
        for mod, (_, names) in GROUPS.items():
            if node.name in names:
                extracted[mod].append(src)
                placed = True
                break
        if not placed:
            remaining_nodes.append(node)

    for mod, (cls, _) in GROUPS.items():
        body = "".join(extracted[mod])
        if not body.strip():
            continue
        out = ROOT / "core" / f"{mod}.py"
        out.write_text(
            MIXIN_HEADER.format(title=mod.replace("_", " "), cls=cls) + body,
            encoding="utf-8",
        )
        print(f"wrote {out.name}: {len(extracted[mod])} methods")

    # Module before class (imports + module-level helpers)
    pre = "".join(lines[: class_node.lineno - 1])
    if "ScalperExitMixin" not in pre:
        anchor = pre.rfind("\nfrom core.")
        if anchor < 0:
            anchor = pre.rfind("\nimport ")
        line_end = pre.find("\n", anchor + 1) if anchor >= 0 else len(pre)
        pre = pre[: line_end + 1] + MIXIN_IMPORTS + pre[line_end + 1 :]

    pre = pre.replace(
        "class ScalperRunner:\n",
        "class ScalperRunner(ScalperExitMixin, ScalperEntryMixin, "
        "ScalperSessionMixin, ScalperSpikeMixin):\n",
    )

    class_line = lines[class_node.lineno - 1]
    if "ScalperExitMixin" not in class_line:
        class_line = class_line.replace(
            "class ScalperRunner:",
            "class ScalperRunner(ScalperExitMixin, ScalperEntryMixin, "
            "ScalperSessionMixin, ScalperSpikeMixin):",
        )

    post_start = class_node.end_lineno or len(lines)
    post = "".join(lines[post_start:])

    remaining_body = "".join(_extract_source(lines, n) for n in remaining_nodes)
    new_text = pre + class_line + remaining_body + post

    backup = RUNNER.with_suffix(".py.bak")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")
    RUNNER.write_text(new_text, encoding="utf-8")
    print(f"scalper_runner.py: {len(remaining_nodes)} methods remain")


if __name__ == "__main__":
    main()
