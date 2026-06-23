# Versioning Guide — How AI Progress Is Tracked

## Core principles

- Git is the system of record for model logic, training code, and small AI artifacts.
- The AI itself records its own lifecycle in `models/consciousness.json` and `models/version_history.jsonl`.

## What gets versioned

Tracked:
- `core/**/*.py`
- `docs/**`
- `models/scalper_weights.json`
- `models/ai_guidelines.txt`
- `models/parameter_adjustments.json`
- `models/experience_buffer.jsonl`
- `models/consciousness.json`
- `models/version_history.jsonl`
- `models/training_history.json`
- `models/improvement_history.json`
- `models/daily_reports/*`
- `backtest_results/results_1min_latest.csv`

Not tracked:
- Large binaries: `models/*.zip`, `models/checkpoints/*`, `models/backups/*`
- Local state: `performance.csv`, `live_metrics.json`, `bot_state.json`
- Logs: `*.log`, `HANOON.log`

## AI lifecycle events

Each meaningful event should be preserved:
- Birth (`models/consciousness.json` creation)
- Training sessions (unified and self-training)
- Trade observations
- Regime/context updates
- Applied improvements and parameter changes
- Versioned snapshots