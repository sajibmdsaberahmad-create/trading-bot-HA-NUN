"""Replay match-live profile — quality gold aligned with live paper gates."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MATCH_PROFILE = ROOT / "scripts" / "replay_match_live_profile.sh"
VOLUME_PROFILE = ROOT / "scripts" / "replay_gold_volume_profile.sh"


def _source_profile(path: Path) -> dict[str, str]:
    script = f"""
set -a
source "{path}"
export HANOON_DEVICE_PROFILE_ROOT="{ROOT}"
env | grep -E '^(REPLAY_MATCH_LIVE|REPLAY_RELAX_COUNCIL|REPLAY_RELAX_COPILOT|MIN_PROFIT_PROBABILITY|CAPITAL_DISCIPLINE|GREEN_DOCTRINE_ENTRY|SMART_STACK_STRICT_PROFIT_PROB|REGIME_ENTRY_BLOCK|REPLAY_GOLD_QUALITY_FILTER|CONFIDENCE_THRESHOLD|COMMANDER_RUNTIME_ENABLED|COMMANDER_LOTTERY_MIN_PROFIT_PROB)='
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


def test_replay_match_live_profile_exists():
    assert MATCH_PROFILE.is_file()


def test_replay_match_live_exports_strict_gates():
    env = _source_profile(MATCH_PROFILE)
    assert env.get("REPLAY_MATCH_LIVE") == "true"
    assert env.get("REPLAY_RELAX_COUNCIL") == "false"
    assert env.get("REPLAY_RELAX_COPILOT") == "false"
    assert env.get("MIN_PROFIT_PROBABILITY") == "0.58"
    assert env.get("CAPITAL_DISCIPLINE") == "true"
    assert env.get("GREEN_DOCTRINE_ENTRY") == "true"
    assert env.get("SMART_STACK_STRICT_PROFIT_PROB") == "true"
    assert env.get("REPLAY_GOLD_QUALITY_FILTER") == "true"
    assert env.get("COMMANDER_RUNTIME_ENABLED") == "false"
    assert env.get("COMMANDER_LOTTERY_MIN_PROFIT_PROB") == "0.58"


def test_replay_gold_volume_profile_is_loose():
    env = _source_profile(VOLUME_PROFILE)
    assert env.get("REPLAY_RELAX_COUNCIL") == "true"
    assert env.get("REPLAY_RELAX_COPILOT") == "true"
    assert env.get("REPLAY_GOLD_QUALITY_FILTER") == "false"


def test_replay_profile_helpers(monkeypatch: pytest.MonkeyPatch):
    from core import replay_profile as rp

    monkeypatch.delenv("REPLAY_LIVE", raising=False)
    assert rp.replay_match_live() is False

    monkeypatch.setenv("REPLAY_LIVE", "true")
    monkeypatch.delenv("REPLAY_GOLD_VOLUME", raising=False)
    monkeypatch.setenv("REPLAY_MATCH_LIVE", "true")
    assert rp.replay_match_live() is True
    assert rp.replay_relax_council() is False
    assert rp.replay_relax_copilot() is False
    assert rp.replay_profile_label() == "replay_match_live"

    monkeypatch.setenv("REPLAY_GOLD_VOLUME", "true")
    assert rp.replay_match_live() is False
    assert rp.replay_relax_council() is True
    assert rp.replay_profile_label() == "replay_gold_volume"


def test_experience_buffer_replay_quality_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from core import experience_buffer as eb

    buf = tmp_path / "experience_buffer.jsonl"
    monkeypatch.setattr(eb, "BUFFER_PATH", buf)
    monkeypatch.setenv("REPLAY_GOLD_QUALITY_FILTER", "true")
    monkeypatch.setenv("REPLAY_GOLD_MIN_PROFIT_PROB", "0.58")

    eb.append({
        "source": "replay_live",
        "action": "BUY",
        "profit_probability": 0.42,
        "ticker": "SOXS",
    })
    eb.append({
        "source": "replay_live",
        "action": "BUY",
        "profit_probability": 0.72,
        "ticker": "SPY",
    })
    eb.append({
        "source": "replay_live",
        "action": "SELL",
        "profit_probability": 0.30,
        "ticker": "SPY",
    })

    lines = buf.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "SPY" in lines[0] or "SPY" in lines[1]
