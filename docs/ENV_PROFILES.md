# Environment profiles — precedence matrix

**Load order (last wins):** `.env` → `scripts/start_hanoon.sh` → `scripts/ppo_wheel_env.sh` → `scripts/hanoon_profit_learn_env.sh` → `scripts/halim_smart_sprint_env.sh` → **`scripts/m2_8gb_live_profile.sh`** (≤12 GB RAM only)

**Assessment & plan:** [SYSTEM_ASSESSMENT_2026-07-01.md](SYSTEM_ASSESSMENT_2026-07-01.md) · [PERFECTION_ROADMAP_M2_8GB.md](PERFECTION_ROADMAP_M2_8GB.md)

| Profile | Source | When | Key flags |
|---------|--------|------|-----------|
| **Base** | `start_hanoon.sh` | Always | `SMART_STACK=true`, `RAM_LIVE_ONLY=true`, `CAPITAL_PHASES_ENABLED=true` |
| **PPO Wheel** | `ppo_wheel_env.sh` | Default live | `PPO_ONLY_EXECUTION=true`, `CONFIDENCE_THRESHOLD=0.58` |
| **Profit+Learn** | `hanoon_profit_learn_env.sh` | `HANOON_PROFIT_LEARN_PROFILE=true` (default) | Profit hunt, **GREEN_WAVE_ENTRY**, micro-PPO |
| **Halim Sprint** | `halim_smart_sprint_env.sh` | `HALIM_SMART_SPRINT=true` (default) | JSON gold, sprint blocks toddler micro_fast |
| **M2 8 GB live** | `m2_8gb_live_profile.sh` | RAM ≤12 GB | **Canonical:** `strict_prob=true`, `await=1.0`, `merged MLX`, `AUTO_DISK_CLEANUP=false` |
| **Dev / test** | Manual `.env` | Local tuning | Set `PPO_WHEEL_PROFILE_LOCK=false` to unlock params |

**Preflight (M2):** `./scripts/preflight_m2.sh` before RTH.

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

## Institutional wave entry (`GREEN_WAVE_ENTRY`)

Rides sudden algo volume bursts — early footprint substitutes for strict `green_bar` when impulse is strong.

| Env | Default | Role |
|-----|---------|------|
| `GREEN_WAVE_ENTRY` | true (profit+learn) | Enable wave branch inside green doctrine |
| `GREEN_WAVE_RELAX_GREEN_BAR` | true | `wave_impulse` + vol_accel can replace green bar |
| `GREEN_SPIKE_PRECHECK` | false | Spike loop defers green to post-PPO entry gate |
| `GREEN_WAVE_IMPULSE_MIN_SCORE` | 0.40 | Minimum fused impulse score |
| `GREEN_WAVE_MIN_VOL_ACCEL` | 1.08 | Minimum volume acceleration |
| `GREEN_WAVE_MIN_INST_STRENGTH` | 0.36 | Institutional accumulating strength |
| `GREEN_WAVE_MIN_SPIKE_RATIO` | 1.15 | Live vol spike footprint (cold micro) |
| `GREEN_WAVE_EXIT_EDGE` | 0.20 | Book profit when fused wave edge collapses |
| `STAGNATION_EXIT_SEC` | 75 | Stagnation exit (profit+learn) |

Module: `core/green_wave_entry.py` · wired in `assess_green_entry` / `assess_dynamic_exit`.

## Reproducible installs

```bash
pip install -r requirements.txt -r requirements-dev.txt
# optional legacy modes:
pip install -r requirements-legacy.txt
# pinned subset (CI / new Mac):
pip install -r requirements-lock.txt
```

See also: [OPS.md](OPS.md) · [PPO_WHEEL_ARCHITECTURE.md](PPO_WHEEL_ARCHITECTURE.md)
