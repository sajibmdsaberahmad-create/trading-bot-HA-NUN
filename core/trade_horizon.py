"""
Trade horizon — scalp (live), swing (shadow→paper), position (future).

IB Truth is the accounting source for all horizons. Local ledgers tag `horizon`
for learning only; marks/PnL/cash always from IB when connected.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.logger import get_logger
from core.ib_truth import get_snapshot, ib_truth_context

if TYPE_CHECKING:
    from core.config import BotConfig

log = get_logger(__name__)

HORIZON_SCALP = "scalp"
HORIZON_SWING = "swing"
HORIZON_POSITION = "position"

ALL_HORIZONS = (HORIZON_SCALP, HORIZON_SWING, HORIZON_POSITION)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
SCALP_GATE_STATE = MODELS_DIR / "scalp_profit_gate.json"


def _truth() -> bool:
    return os.getenv("IB_TRUTH_ENABLED", "true").lower() in ("1", "true", "yes")


def active_order_horizon(cfg: Optional["BotConfig"] = None) -> str:
    """Only horizon allowed to place live IB orders today."""
    return HORIZON_SCALP


def swing_shadow_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    if os.getenv("SWING_SHADOW_ENABLED", "true").lower() in ("0", "false", "no"):
        return False
    try:
        from core.brain_maturity import compute_stage

        stage = compute_stage(cfg)
        return stage in ("child", "teen", "adult")
    except Exception:
        return False


def swing_paper_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    if os.getenv("SWING_PAPER_ENABLED", "false").lower() not in ("1", "true", "yes"):
        return False
    if not scalp_profit_gate_passed(cfg):
        return False
    try:
        from core.brain_maturity import compute_stage

        return compute_stage(cfg) in ("teen", "adult")
    except Exception:
        return False


def position_horizon_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    return (
        os.getenv("POSITION_HORIZON_ENABLED", "false").lower() in ("1", "true", "yes")
        and scalp_profit_gate_passed(cfg)
        and _stage_at_least(cfg, "adult")
    )


def _stage_at_least(cfg: Optional["BotConfig"], minimum: str) -> bool:
    try:
        from core.brain_maturity import compute_stage

        order = ("newborn", "child", "teen", "adult")
        stage = compute_stage(cfg)
        return order.index(stage) >= order.index(minimum)
    except Exception:
        return False


def scalp_profit_gate_passed(cfg: Optional["BotConfig"] = None) -> bool:
    """Scalp must show edge before swing paper / position live."""
    if os.getenv("SCALP_PROFIT_GATE_FORCE", "").lower() in ("1", "true", "pass", "yes"):
        return True
    if os.getenv("SCALP_PROFIT_GATE_FORCE", "").lower() in ("0", "false", "fail", "no"):
        return False
    if SCALP_GATE_STATE.exists():
        try:
            data = json.loads(SCALP_GATE_STATE.read_text())
            if data.get("passed") is True:
                return True
        except Exception:
            pass
    if not _truth():
        return False
    snap = get_snapshot()
    if snap.refreshed_at <= 0:
        return False
    min_days = int(os.getenv("SCALP_GATE_MIN_GREEN_DAYS", "3"))
    min_pnl = float(os.getenv("SCALP_GATE_MIN_SESSION_PNL", "5.0"))
    # IB session realized — no local FIFO math for gate
    if snap.session_pnl_ib >= min_pnl:
        return True
    return False


def update_scalp_gate_from_ib(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """Persist gate state from IB RealizedPnL (off-hours / post-RTH)."""
    snap = get_snapshot()
    out: Dict[str, Any] = {
        "updated_at": time.time(),
        "session_pnl_ib": snap.session_pnl_ib,
        "passed": scalp_profit_gate_passed(cfg),
    }
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        SCALP_GATE_STATE.write_text(json.dumps(out, indent=2))
    except Exception as exc:
        log.debug(f"scalp gate state: {exc}")
    return out


def horizon_context(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    ctx = ib_truth_context(cfg)
    ctx.update(
        {
            "active_order_horizon": active_order_horizon(cfg),
            "swing_shadow_enabled": swing_shadow_enabled(cfg),
            "swing_paper_enabled": swing_paper_enabled(cfg),
            "position_horizon_enabled": position_horizon_enabled(cfg),
            "scalp_profit_gate_passed": scalp_profit_gate_passed(cfg),
        }
    )
    return ctx


def tag_record(record: Dict[str, Any], horizon: Optional[str] = None) -> Dict[str, Any]:
    """Stamp horizon on verdict/fill/ledger rows."""
    h = horizon or record.get("horizon") or active_order_horizon()
    record["horizon"] = h
    return record
