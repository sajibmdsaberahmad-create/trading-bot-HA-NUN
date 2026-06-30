#!/usr/bin/env python3
"""One-shot extractor: ScalperRunner methods -> mixin modules (AST-safe)."""

from __future__ import annotations

import ast
import textwrap
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
    "_halt_trading_for_closed_market", "_on_day_session_end", "_on_rth_open",
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


def _method_nodes(tree: ast.Module) -> Dict[str, ast.FunctionDef]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ScalperRunner":
            return {
                n.name: n for n in node.body
                if isinstance(n, ast.FunctionDef)
            }
    return {}


def _extract_source(lines: List[str], node: ast.FunctionDef) -> str:
    start = node.lineno - 1
    end = node.end_lineno or node.lineno
    return "".join(lines[start:end])


def main() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    methods = _method_nodes(tree)

    assigned: Set[str] = set()
    for _, (_, names) in GROUPS.items():
        assigned |= names

    extracted: Dict[str, List[str]] = {k: [] for k in GROUPS}
    remaining: List[str] = []

    class_node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ScalperRunner"
    )
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
            remaining.append(src)

    header = '''#!/usr/bin/env python3
"""Extracted from scalper_runner — {title}."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner


class {cls}:
    """Mixin — use via ScalperRunner multiple inheritance."""

'''

    for mod, (cls, names) in GROUPS.items():
        body = "".join(extracted[mod])
        if not body.strip():
            print(f"skip empty {mod}")
            continue
        title = mod.replace("_", " ")
        out = ROOT / "core" / f"{mod}.py"
        out.write_text(header.format(title=title, cls=cls) + body, encoding="utf-8")
        print(f"wrote {out.name}: {len(extracted[mod])} methods")

    # Rebuild ScalperRunner class with remaining methods only
    pre_class = text[: class_node.lineno - 1]
    # find start of class line in pre_class - we need everything before class def
    pre_lines = lines[: class_node.lineno - 1]
    post_import_addition = """
from core.scalper_exit_executor import ScalperExitMixin
from core.scalper_entry_executor import ScalperEntryMixin
from core.scalper_session import ScalperSessionMixin
from core.scalper_spike_loop import ScalperSpikeMixin
"""

    # Insert mixin imports before class definition (after last import block)
    pre_text = "".join(pre_lines)
    if "ScalperExitMixin" not in pre_text:
        insert_at = pre_text.rfind("\nfrom core.")
        if insert_at < 0:
            insert_at = pre_text.rfind("\nimport ")
        if insert_at >= 0:
            line_end = pre_text.find("\n", insert_at + 1)
            pre_text = pre_text[: line_end + 1] + post_import_addition + pre_text[line_end + 1 :]

    class_header = "class ScalperRunner(ScalperExitMixin, ScalperEntryMixin, ScalperSessionMixin, ScalperSpikeMixin):\n"
    old_header = "class ScalperRunner:\n"
    pre_text = pre_text.replace(old_header, class_header)

    new_class_body = "".join(remaining)
    new_runner = pre_text + new_class_body

    backup = RUNNER.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")
    RUNNER.write_text(new_runner, encoding="utf-8")
    print(f"scalper_runner.py: {len(remaining)} methods remain, backup at {backup.name}")


if __name__ == "__main__":
    main()
