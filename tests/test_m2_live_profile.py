"""M2 8 GB canonical live profile exports."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "scripts" / "m2_8gb_live_profile.sh"


def _source_profile() -> dict[str, str]:
    """Source profile in subshell and dump key env vars."""
    script = f"""
set -a
source "{PROFILE}"
export HANOON_DEVICE_PROFILE_ROOT="{ROOT}"
env | grep -E '^(HANOON_DEVICE_PROFILE|RAM_LIVE_ONLY|PERIODIC_CLEANUP_SEC|AUTO_DISK_CLEANUP|SMART_STACK_STRICT_PROFIT_PROB|GREEN_DOCTRINE_ENTRY|HALIM_ENTRY_AWAIT_SEC|HALIM_ENTRY_LM_TIMEOUT_SEC|HALIM_SERVE_PREFER_ADAPTER|HALIM_FORCE_LM)='
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
    return result


def test_m2_profile_file_exists():
    assert PROFILE.is_file(), "m2_8gb_live_profile.sh missing"


def test_m2_profile_exports_canonical_values():
    if not PROFILE.is_file():
        return
    env = _source_profile()
    assert env.get("HANOON_DEVICE_PROFILE") == "m2_8gb_live"
    assert env.get("RAM_LIVE_ONLY") == "true"
    assert env.get("PERIODIC_CLEANUP_SEC") == "0"
    assert env.get("AUTO_DISK_CLEANUP") == "false"
    assert env.get("SMART_STACK_STRICT_PROFIT_PROB") == "true"
    assert env.get("GREEN_DOCTRINE_ENTRY") == "true"
    assert env.get("HALIM_FORCE_LM") == "true"
    assert env.get("HALIM_ENTRY_LM_TIMEOUT_SEC") == "12"
    assert env.get("HALIM_ENTRY_AWAIT_SEC") == "1.0"
    merged = ROOT / "halim/data/checkpoints/toddler_v1/merged/model.safetensors"
    if merged.is_file():
        assert env.get("HALIM_SERVE_PREFER_ADAPTER") == "false"


def test_live_money_guard_documented_in_start_script():
    text = (ROOT / "scripts/start_hanoon.sh").read_text(encoding="utf-8")
    assert "HANOON_LIVE_MONEY_ACK" in text
    assert "4001" in text
