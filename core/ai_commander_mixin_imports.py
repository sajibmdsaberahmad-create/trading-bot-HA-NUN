#!/usr/bin/env python3
"""Shared imports for ai_commander mixin modules (extracted methods)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.bracket_validator import (
    adjust_managed_stop,
    adjust_managed_target,
    compute_atr_bracket,
    validate_decision_bracket,
)
from core.config import BotConfig
from core.deferred_council_learning import deferred_learning_enabled
from core.fast_execution import should_spike_fast_entry
from core.human_cognition import apply_gut_override, enrich_prompt
from core.live_ai_pipeline import (
    entry_fingerprint,
    exit_fingerprint,
    merge_entry_decision,
    merge_exit_decision,
    merge_position_manage_decision,
    merge_risk_signal_decision,
    merge_stagnation_decision,
    position_fingerprint,
    risk_signal_fingerprint,
    stagnation_fingerprint,
)
from core.market_hours import min_confidence_for_state
from core.notify import log
from core.pilot_mode import (
    effective_max_concurrent_positions,
    generative_think,
    get_ai_deploy_budget,
    get_effective_confidence_threshold,
    get_trade_risk_usd,
    is_ai_council_mode,
    is_ai_unlimited,
)
from core.risk import compute_atr, compute_momentum_score
from core.trade_telemetry import log_bracket_reject
