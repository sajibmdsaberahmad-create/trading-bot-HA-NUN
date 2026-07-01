#!/usr/bin/env python3
"""Canonical paths for learning datasets — single source, halim mirrors on sync."""

from __future__ import annotations

from pathlib import Path

# Authoritative council gold (append during live / replay)
COUNCIL_TRAINING_DATASET = Path("models/council_training_dataset.jsonl")

# Halim package mirror — updated by halim/scripts/sync_from_tradingbot.py only
HALIM_COUNCIL_MIRROR = Path("halim/data/trading/council_training_dataset.jsonl")


def council_training_dataset_path() -> Path:
    return COUNCIL_TRAINING_DATASET


def halim_council_mirror_path() -> Path:
    return HALIM_COUNCIL_MIRROR
