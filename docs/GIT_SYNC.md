# Git Sync — Automatic GitHub Push

Every significant change is **automatically committed and pushed** to GitHub.  
You never have to run `git push` manually.

## What gets pushed (tracked files)

| File | Trigger |
|------|---------|
| `ppo_trader.zip` | Model saved / online fine-tune |
| `performance.csv` | Trade opened or closed |
| `audit_trail.jsonl` | Guardrail event, config change, anomaly |
| `live_metrics.json` | Dashboard state update (every ~2s) |
| `bot_state.json` | Position / equity state change |
| `HA-NUN.log` | Startup, shutdown, error |
| `models/scalper_weights.json` | Self-training weight update |
| `models/daily_guidelines.txt` | Daily self-improvement |
| `core/config.py` | Config validation / override |
| `core/agent.py` | Agent logic change |
| `core/agent_enhanced.py` | Enhanced AI change |
| `core/ai_guardrails.py` | Guardrail change |
| `core/features_enhanced.py` | Feature engineering change |
| `core/risk.py` | Risk management change |

## Auto-push events

1. **Model update** — after every `model.save()` (online fine-tune)
2. **Trade event** — after every BUY / SELL / stop-hit / target-hit
3. **Guardrail event** — anomaly, override, config change, anomaly detection
4. **Daily summary** — end-of-day NAV + trade log
5. **Startup** — bot launch
6. **Shutdown** — bot stop (final NAV, open positions)
7. **Error snapshot** — unhandled exception
8. **Checkpoint** — full state backup (configurable)

## Commit message format

```
category: short description

Category: trade|model|guardrail|config|training|daily|startup|shutdown|error|checkpoint|batch
Timestamp: 2025-06-21 14:05:41 UTC
Auto-pushed by git_sync.py
```

## Batching & rate limiting

- Minimum push interval: **5 seconds**
- Multiple changes within 10 seconds are **batched** into one commit
- Max batch size: **20 files**

## Setup

1. Add to `.env`:
   ```bash
   GITHUB_TOKEN=ghp_xxxxxxxxxxxx
   GITHUB_REPO=username/repo
   ```
2. In `core/config.py`, ensure:
   ```python
   GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
   GITHUB_REPO = os.getenv("GITHUB_REPO", "")
   ```
3. Call `git_sync.init(cfg)` at startup (already done in `main.py`)

## Manual push

```python
from core.git_sync import push_change
push_change("manual: snapshot before market close")
```

## Monitoring

```python
from core.git_sync import get_stats
print(get_stats())
# {
#   "enabled": True,
#   "total_pushes": 42,
#   "failed_pushes": 0,
#   "last_push_ts": 1718953575.39,
#   "last_push_age_sec": 12.3,
#   "pending_queue": 0,
#   "tracked_files": 15,
#   "repo": "user/tradingbot"
# }