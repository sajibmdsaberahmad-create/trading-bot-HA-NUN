#!/usr/bin/env bash
# PPO wheel profile — FORCE exports (overrides halim_env.sh, early start_hanoon, and .env leaks).
# Sourced last in start_hanoon.sh immediately before main.py launch.
# Set PPO_WHEEL_PROFILE_LOCK=false to allow Halim developer to tune locked params.

export PPO_WHEEL_PROFILE_LOCK="${PPO_WHEEL_PROFILE_LOCK:-true}"

export MAX_ENTRIES_PER_HOUR=0
export WAR_MAX_ENTRIES_PER_HOUR=0
export WAR_PAPER_MAX_ENTRIES_PER_HOUR=0
export PPO_LEAD_WHILE_COUNCIL_PENDING=true
export SMART_STACK_STRICT_PROFIT_PROB=false
export SMART_STACK_AI_SURE_ENTRY=false
export COMMANDER_RUNTIME_ENABLED=false
export SMART_STACK_TEACHER_HARD_ONLY=true
export SMART_STACK_WAR_POSTURE=true
export WAR_ENTRY_ADVISORY_ONLY=true
export PPO_DEPLOY_TIERS_ENABLED=true
export LEARN_APPROVAL_REQUIRED=true
export GREEN_VERDICT_RECHECK=false

export HALIM_ENTRY_SOFT_VETO=false
export HALIM_ENTRY_AWAIT_ENABLED=true
export HALIM_ENTRY_AWAIT_LIVE=true
export HALIM_ENTRY_AWAIT_SEC=0
export HALIM_PPO_COMPLEMENT=true

export CONFIDENCE_THRESHOLD=0.58
export MIN_PROFIT_PROBABILITY=0.58
export CAPITAL_MIN_CONFIDENCE=0.58
export CAPITAL_MIN_PROFIT_PROBABILITY=0.58
export WAR_MIN_PROFIT_PROBABILITY=0.58
export WAR_PAPER_MIN_PROFIT_PROBABILITY=0.58
