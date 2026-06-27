# models — AI state, learning artifacts, and versioning

## Directory layout

- `scalper_weights.json` — learned heuristic rule weights used by the scanner/scalper
- `ppo_trader.zip` — current PPO model used for live top-pick gating
- `ppo_trader_warmup_*.zip` — historical warm-up snapshots
- `checkpoints/` — PPO checkpoint snapshots (train/validation splits)
- `backups/` — legacy model backups
- `experience_buffer.jsonl` — unified experience store (backtests, live trades, scans, daily sessions)
- `consciousness.json` — AI consciousness state (birth, awakenings, training history, identity)
- `version_history.jsonl` — append-only version log
- `ai_guidelines.txt` — latest human-readable AI guidelines
- `parameter_adjustments.json` — latest machine-usable parameter adjustments
- `improvement_history.json` — historical improvement plans
- `training_history.json` — historical training runs
- `daily_reports/` — one daily report per day
- `trade_journal.json` — optional trade journal from live scalper

## M. A. Halim (your own AI model)

**Halim** replaces external LLMs over time. See [docs/HALIM.md](../docs/HALIM.md) and the separate model home in [halim/](../halim/).

| File | Role |
|------|------|
| `halim_identity.json` | Halim birth, phase, native-mode policy |
| `halim_manifest.json` | Full Halim status snapshot |

## Owned Brain (evolution flywheel)

Portable, git-synced students that learn every session. Full guide: [docs/OWNED_BRAIN.md](../docs/OWNED_BRAIN.md).

| File | Role |
|------|------|
| `ppo_trader_replay.zip` | PPO policy trained during replay (separate from live `ppo_trader.zip`) |
| `teacher_proxy.joblib` | Distilled council enter/skip (sklearn, microseconds) |
| `council_training_dataset.jsonl` | Exported prompt→decision→outcome pairs for future LLM student |
| `copilot_state.json` | Session reasoning cache (Trading Copilot) |
| `copilot_journal.jsonl` | Copilot call log |
| `owned_brain_manifest.json` | Portable index: assets, device profile, last evolution |
| `owned_brain_state.json` | Evolution counters and last trigger |
| `device_profile.json` | Active device tier (`m2_8gb`, etc.) |
| `ppo_teacher_sessions.jsonl` | PPO teacher critique sessions |

Check status: `PYTHONPATH=. python scripts/owned_brain_status.py`  
Manual evolution: `./scripts/post_session_evolve.sh`

## Versioning model

- Training and improvement artifacts are committed to git automatically.
- `consciousness.json` is the source of truth for AI lifecycle (birth time, awakenings, training sessions, trades observed, current version).
- `version_history.jsonl` is append-only and should not be rewritten; it preserves the evolution chain.
- `daily_reports/` gives per-day summaries. Files inside are tracked in git for audit/history.

## Safety rules

- Large binary model files (`*.zip`) are ignored except for the canonical `ppo_trader.zip` when explicitly allowed.
- Small JSON/TXT/CSV artifacts are tracked.
- Do not delete `version_history.jsonl` or `consciousness.json` unless you intend to reset AI memory.