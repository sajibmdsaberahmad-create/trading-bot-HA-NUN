# models — HANOON lean repo (essential seeds only)

## Tracked in git (HANOON repo)

| File | Purpose |
|------|---------|
| `../ppo_trader.zip` | Canonical PPO model — required to trade on a new device |
| `scalper_weights.json` | Learned scanner heuristic weights |
| `trader_directives.txt` | Pilot mission directives for Ollama |
| `ai_guidelines.txt` | Self-improvement guidelines |
| `parameter_adjustments.json` | Last guardrailed param mutations |
| `model_manifest.json` | Model version metadata |
| `feature_manifest.json` | Feature schema version |

## Local only (not in git — see `.gitignore`)

Runtime journals and session state regenerate on each machine:

- `*.jsonl` — experience buffer, profit hunts, post-mortem, commander learning, etc.
- `consciousness.json`, `cognitive_state.json` — AI session memory
- `daily_reports/` — end-of-day reports
- `daily_ib_learning/` — per-day IB learning packs

After `git pull` on a new device, run `./start.sh` — the bot rebuilds journals from live trading.

## Encrypted secrets

Environment variables sync via `secrets/hanoon.env.enc` + `secrets/sync.key` (private repo).
