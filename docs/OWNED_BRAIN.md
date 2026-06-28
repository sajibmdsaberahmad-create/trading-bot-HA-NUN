# Owned Brain — Your evolving trading model (portable, git-synced)

HANOON’s **Owned Brain** is not a single giant LLM on your Mac. It is a **stack of students** that learn from live/replay sessions and from cloud teachers (Groq/Gemini). Everything lives under `models/` and is designed to **clone the repo anywhere** and keep improving.

## Growth stages (infant → adult)

The brain **starts small** and grows automatically. Teacher API budget **shrinks** as students improve.

| Stage | Trades | Teacher council/day | Copilot/day | PPO teacher API |
|-------|--------|---------------------|-------------|-----------------|
| **newborn** | 0–7 | 0 (local only) | 0 | never |
| **infant** | 8+ | ~4 | ~2 | never |
| **toddler** | 25+ | ~12 | ~5 | ~1 |
| **child** | 60+ + proxy | ~25 | ~8 | ~2 |
| **teen** | 150+ | ~15 | ~4 | ~1 |
| **adult** | 350+ | ~8 | ~2 | never |

As **proxy accuracy** rises (55% → 62% → 70%), daily API caps are multiplied down (75% → 35% → 20%).

Check stage:
```bash
PYTHONPATH=. python scripts/owned_brain_status.py
```

Force cloud teacher (debug only):
```bash
export BRAIN_MATURITY_FORCE_API=true
```

Brain development events are pushed to Telegram when `TRADING_BOT_TELEGRAM_BROADCAST_BRAIN=true` (default). Journal: `models/owned_brain_journal.jsonl` + `docs/BRAIN_DEVELOPMENT_LOG.md` (auto git-synced).

## Design goals

| Goal | How |
|------|-----|
| Runs on **MacBook Air M2 8GB** | No local Ollama. CPU PPO + sklearn proxy + cached copilot. API teacher only when needed. |
| Gets smarter over time | Every session exports decisions, trains proxy/PPO, updates weights, writes manifest. |
| Portable | Copy repo + `models/` → continue on another machine. |
| Git-backed | `core/git_sync.py` pushes learning artifacts after evolution. |
| Room to grow | Device profiles (`m2_8gb` → `m2_16gb` → `m2_32gb_plus`) unlock heavier training later. |

## Architecture (teacher → students)

```
Groq / Gemini (teacher)     ← reasoning, rate-limited, best quality
        ↓ logs every decision + outcome
┌───────────────────────────────────────────────────────────┐
│  YOUR OWNED ASSETS (on disk, microseconds–milliseconds)   │
├───────────────────────────────────────────────────────────┤
│  council_training_dataset.jsonl  → future small LLM student │
│  teacher_proxy.joblib            → distilled enter/skip     │
│  ppo_trader_replay.zip           → reflex policy (replay)   │
│  ppo_trader.zip                  → reflex policy (live)       │
│  scalper_weights.json            → scanner heuristics         │
│  copilot_state.json              → session reasoning cache    │
│  owned_brain_manifest.json       → portable index + device    │
└───────────────────────────────────────────────────────────┘
```

Think of it like **cloud teachers training Halim**: Groq/Gemini critique trades; your owned students (PPO, proxy, Halim LM) compress that knowledge into weights you keep.

## Device profiles

Set explicitly (recommended on 8GB Air):

```bash
export OWNED_BRAIN_DEVICE=m2_8gb
```

Or let the bot detect RAM via `core/memory_guard.py`.

| Profile | RAM | PPO micro-steps | Local LLM | Council hint |
|---------|-----|-----------------|-----------|--------------|
| `m2_8gb` | ≤10GB | 512 | No | `llama-3.1-8b-instant` |
| `m2_16gb` | ≤20GB | 1024 | Optional MLX 3B later | `llama-3.3-70b-versatile` |
| `m2_32gb_plus` | 32GB+ | 2048 | MLX 3–7B copilot | `llama-3.3-70b-versatile` |

Profile is written to `models/device_profile.json` each evolution.

## Session flywheel (automatic)

At **replay** or **live** session end, `run_post_session_evolution()` in `core/owned_brain_evolution.py`:

1. Export `models/council_training_dataset.jsonl` (prompt → decision → outcome)
2. Train `teacher_proxy.joblib` if enough closed trades
3. Run PPO teacher micro-session (heuristic fallback if API rate-limited)
4. Refresh `scalper_weights.json` from experience buffer
5. Write `models/owned_brain_manifest.json` + update `owned_brain_state.json`
6. Queue git push via `push_learning_checkpoint_async()` (unless disabled)

Dedup: same machine won’t re-run within `OWNED_BRAIN_MIN_EVOLUTION_SEC` (default 120s).

## Environment variables

```bash
# Device & evolution
OWNED_BRAIN_DEVICE=m2_8gb          # force profile
OWNED_BRAIN_GIT_PUSH=true          # push after evolution (default true)
OWNED_BRAIN_MIN_EVOLUTION_SEC=120  # dedup window

# Replay stack (see scripts/start_replay_live.sh)
REPLAY_LIVE=true
REPLAY_MODEL_PATH=models/ppo_trader_replay.zip
PPO_TEACHER_ENABLED=true
TRADING_COPILOT_ENABLED=true
COPILOT_REFRESH_SEC=90
```

## Commands

```bash
# Dashboard
PYTHONPATH=. python scripts/owned_brain_status.py

# Export council dataset only
PYTHONPATH=. python scripts/owned_brain_status.py --export

# Full evolution manually (no replay needed)
./scripts/post_session_evolve.sh

# JSON status
PYTHONPATH=. python scripts/owned_brain_status.py --json
```

## Clone anywhere workflow

1. Clone repo on new Mac (or pull latest).
2. Copy or pull `models/` (git sync should have artifacts if pushed).
3. Set `OWNED_BRAIN_DEVICE` for that machine.
4. Run replay: `./scripts/start_replay_live.sh day`
5. Session end runs evolution + optional git push.
6. Repeat — students improve; API usage can drop as proxy accuracy rises.

## Files to never delete casually

- `models/council_training_dataset.jsonl` — training gold for future student LLM
- `models/experience_buffer.jsonl` — unified experience
- `models/ai_decision_log.jsonl` — council decisions
- `models/owned_brain_manifest.json` — portable index
- `models/consciousness.json` — AI lifecycle (see `models/README.md`)

## Related docs

- [GIT_SYNC.md](GIT_SYNC.md) — auto-push behavior
- [MODEL_VERSIONING.md](MODEL_VERSIONING.md) — PPO versioning
- [TRAINING_GUIDE.md](TRAINING_GUIDE.md) — Colab / heavy training (optional, not daily on 8GB)

## Roadmap (when you upgrade hardware)

1. **Now (8GB):** PPO + proxy + copilot cache + dataset export
2. **16GB:** Optional MLX 3B local copilot; longer PPO micro-trains
3. **32GB+:** Local small LLM fine-tuned once on `council_training_dataset.jsonl`; copilot mostly on-device

The dataset you build today is what makes a future **frontier-scale Halim** possible without throwing away replay history.
