"""M2 8 GB full start_hanoon env chain — canonical live values win."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _simulate_m2_live_chain() -> dict[str, str]:
    """Source the same tail chain as start_hanoon.sh on ≤12 GB (no main.py)."""
    script = f"""
set -a
export TOTAL_RAM_MB=8192
export HANOON_M2_CANONICAL_LIVE=true
export HANOON_LIMITLESS_WAR_ONLY=false
export HANOON_DEVICE_PROFILE_ROOT="{ROOT}"
# Simulate earlier scripts polluting env (limitless / wheel / sprint)
export MIN_PROFIT_PROBABILITY=0.32
export CONFIDENCE_THRESHOLD=0.40
export CAPITAL_DISCIPLINE=false
export HALIM_ENTRY_AWAIT_SEC=0
export HALIM_SERVE_PREFER_ADAPTER=true
export COMMANDER_RUNTIME_ENABLED=true
export LEARNING_LIVE_MICRO_PPO=true
source "{ROOT}/scripts/ppo_wheel_env.sh"
source "{ROOT}/scripts/hanoon_profit_learn_env.sh"
source "{ROOT}/scripts/halim_smart_sprint_env.sh"
source "{ROOT}/scripts/m2_8gb_live_profile.sh"
env | grep -E '^(HANOON_DEVICE_PROFILE|HANOON_M2_CANONICAL_LIVE|HANOON_LIMITLESS_WAR_ONLY|MIN_PROFIT_PROBABILITY|CONFIDENCE_THRESHOLD|CAPITAL_DISCIPLINE|TREAT_PAPER_AS_LIVE|COMMANDER_RUNTIME_ENABLED|COMMANDER_LOTTERY_MIN_PROFIT_PROB|HALIM_ENTRY_AWAIT_SEC|HALIM_ENTRY_LM_TIMEOUT_SEC|HALIM_SERVE_PREFER_ADAPTER|SMART_STACK_STRICT_PROFIT_PROB|GREEN_DOCTRINE_ENTRY|GREEN_SPIKE_PRECHECK|LEARNING_LIVE_MICRO_PPO|LEARNING_SYNC_INTERVAL_SEC|REGIME_ENTRY_BLOCK|PPO_ONLY_EXECUTION|LEARN_APPROVAL_REQUIRED)='
"""
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    result: dict[str, str] = {}
    for line in (out.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            result[k] = v
    if out.returncode != 0 and not result:
        raise AssertionError(f"chain simulation failed: {out.stderr}")
    return result


def test_m2_live_chain_wins_over_limitless_pollution():
    env = _simulate_m2_live_chain()
    assert env.get("HANOON_DEVICE_PROFILE") == "m2_8gb_live"
    assert env.get("HANOON_M2_CANONICAL_LIVE") == "true"
    assert env.get("HANOON_LIMITLESS_WAR_ONLY") == "false"
    assert env.get("MIN_PROFIT_PROBABILITY") == "0.58"
    assert env.get("CONFIDENCE_THRESHOLD") == "0.58"
    assert env.get("CAPITAL_DISCIPLINE") == "true"
    assert env.get("TREAT_PAPER_AS_LIVE") == "true"
    assert env.get("COMMANDER_RUNTIME_ENABLED") == "false"
    assert env.get("COMMANDER_LOTTERY_MIN_PROFIT_PROB") == "0.58"
    assert env.get("HALIM_ENTRY_AWAIT_SEC") == "1.0"
    assert env.get("HALIM_ENTRY_LM_TIMEOUT_SEC") == "12"
    assert env.get("SMART_STACK_STRICT_PROFIT_PROB") == "true"
    assert env.get("GREEN_DOCTRINE_ENTRY") == "true"
    assert env.get("GREEN_SPIKE_PRECHECK") == "true"
    assert env.get("LEARNING_LIVE_MICRO_PPO") == "false"
    assert env.get("LEARNING_SYNC_INTERVAL_SEC") == "0"
    assert env.get("LEARN_APPROVAL_REQUIRED") == "true"
    assert env.get("PPO_ONLY_EXECUTION") == "true"
    merged = ROOT / "halim/data/checkpoints/toddler_v1/merged/model.safetensors"
    if merged.is_file():
        assert env.get("HALIM_SERVE_PREFER_ADAPTER") == "false"
