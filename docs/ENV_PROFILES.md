# Environment profiles — precedence matrix

**Load order (last wins):** `.env` → `scripts/start_hanoon.sh` → `scripts/ppo_wheel_env.sh` → `scripts/hanoon_profit_learn_env.sh`

| Profile | Source | When | Key flags |
|---------|--------|------|-----------|
| **Base** | `start_hanoon.sh` | Always | `SMART_STACK=true`, `RAM_LIVE_ONLY=true`, `CAPITAL_PHASES_ENABLED=true` |
| **PPO Wheel** | `ppo_wheel_env.sh` | Default live | `PPO_ONLY_EXECUTION=true`, `SMART_STACK_AI_SURE_ENTRY=false`, `HALIM_ENTRY_AWAIT_SEC=0`, `CONFIDENCE_THRESHOLD=0.58` |
| **Profit+Learn** | `hanoon_profit_learn_env.sh` | `HANOON_PROFIT_LEARN_PROFILE=true` (default) | Profit hunt, micro-PPO, `STAGNATION_EXIT_SEC=75`, defer heavy RTH learn |
| **Dev / test** | Manual `.env` | Local tuning | Set `PPO_WHEEL_PROFILE_LOCK=false` to unlock params |

## Capital phases

| Phase | War ledger | Sizing |
|-------|------------|--------|
| `premarket_full` | Off | Full IB balance |
| `rth_war` | On | Virtual war pool bullets |
| `postmarket_full` | Off | Full IB balance |

War `record_entry` / `record_exit` only run when `war_ledger_applies()` → RTH war phase.

## Brain maturity overlays

Applied at session start via `apply_maturity_to_config()`:

| Stage | min_conf | min_prob | ai-sure (auto) |
|-------|----------|----------|----------------|
| newborn–toddler | 0.58 | 0.58 | off |
| child | 0.60 | 0.60 | on if `BRAIN_MATURITY_AI_SURE_AUTO=true` |
| teen | 0.61 | 0.61 | on if auto |
| adult | 0.62 | 0.62 | on if auto |

Default: `BRAIN_MATURITY_AI_SURE_AUTO=false` so PPO Wheel profile is not overridden.

## Reproducible installs

```bash
pip install -r requirements.txt -r requirements-dev.txt
# optional legacy modes:
pip install -r requirements-legacy.txt
# pinned subset (CI / new Mac):
pip install -r requirements-lock.txt
```

See also: [OPS.md](OPS.md) · [PPO_WHEEL_ARCHITECTURE.md](PPO_WHEEL_ARCHITECTURE.md)
