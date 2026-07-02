#!/usr/bin/env bash
# Legacy loose replay — bulk gold / council labels (lower transfer to live paper).
# Opt-in: REPLAY_GOLD_VOLUME=true ./scripts/start_replay_live.sh

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && {
  echo "Source this file: source scripts/replay_gold_volume_profile.sh" >&2
  exit 1
}

export REPLAY_MATCH_LIVE=false
export REPLAY_RELAX_COUNCIL="${REPLAY_RELAX_COUNCIL:-true}"
export REPLAY_RELAX_COPILOT="${REPLAY_RELAX_COPILOT:-true}"
export REPLAY_RELAX_WAR="${REPLAY_RELAX_WAR:-true}"
export REPLAY_MIN_PROFIT_PROB="${REPLAY_MIN_PROFIT_PROB:-0.45}"
export REPLAY_CAPITAL_MIN_PROFIT_PROB="${REPLAY_CAPITAL_MIN_PROFIT_PROB:-0.45}"
export REGIME_ENTRY_BLOCK="${REGIME_ENTRY_BLOCK:-false}"
export MTF_ENTRY_BLOCK="${MTF_ENTRY_BLOCK:-false}"
export USE_ACCOUNT_LOSS_HALT="${USE_ACCOUNT_LOSS_HALT:-false}"
export CAPITAL_DISCIPLINE="${CAPITAL_DISCIPLINE:-false}"
export CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.65}"
export REPLAY_GOLD_QUALITY_FILTER="${REPLAY_GOLD_QUALITY_FILTER:-false}"
