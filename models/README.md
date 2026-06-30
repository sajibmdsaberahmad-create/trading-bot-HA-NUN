# models — AI state and learning artifacts

## Committed (curated, versioned)

| Path | Role |
|------|------|
| `scalper_weights.json` | Scanner heuristic weights |
| `ppo_trader.zip` | Canonical PPO model |
| `halim_manifest.json` | Halim checkpoint metadata |
| `owned_brain_manifest.json` | Owned brain evolution manifest |
| `hybrid_distill_state.json` | Proxy distillation metrics |
| `ai_session_limits.json` | Session limit snapshots |
| `daily_guidelines.txt` | Latest AI guidelines (optional) |

## Local only (gitignored — sync via `stop_hanoon` / Logs repo)

Runtime journals and session state — **do not commit**:

- `*_ledger.jsonl`, `experience_buffer.jsonl`, `commander_learning.jsonl`
- `consciousness.json`, `cognitive_state.json`, `pilot_experience.json`
- `war_account_state.json`, `halim_runtime_state.json`
- `daily_reports/*.json` (session reports)

Learning is preserved via `core/git_sync.py` on shutdown when `GIT_PUSH_DURING_SESSION=false`.

See `docs/OPS.md` for git/env defaults.
