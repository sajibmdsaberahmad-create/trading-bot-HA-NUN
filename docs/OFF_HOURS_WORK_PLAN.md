# Off-Hours Work Plan — MacBook Air M2 8 GB

**When to use:** After RTH, when HANOON is stopped, IB Gateway can rest, and you have 1–3 hours uninterrupted.  
**Prerequisites:** [SYSTEM_ASSESSMENT_2026-07-01.md](SYSTEM_ASSESSMENT_2026-07-01.md) · [PERFECTION_ROADMAP_M2_8GB.md](PERFECTION_ROADMAP_M2_8GB.md) (live phases done)  
**Goal:** Close the remaining gaps — Halim brain maturity, disk/git hygiene, paper soak — without breaking live config.

---

## Before you start (every off-hours session)

```bash
cd ~/Downloads/tradingbot
source venv/bin/activate

# 1. HANOON must be fully stopped (not Ctrl+C — use stop script)
./scripts/stop_hanoon.sh
pgrep -fl 'main.py.*scalper' || echo "OK: no scalper"

# 2. Quit Cursor (frees RAM + git index.lock on LFS weights)
#    Reopen after Step 5 if you need the IDE.

# 3. Quick health read (no deletes)
./scripts/disk_audit.sh
./scripts/preflight_m2.sh   # may warn if Halim serve down — OK off-hours
```

**Do not run** `DEEP_SWEEP_AGGRESSIVE=true` unless you explicitly want Homebrew/LFS prune.

---

## Master checklist (work through in order)

Copy this into your notes and check off as you go:

```
[ ] O1  Stop HANOON + sidecars cleanly
[ ] O2  Disk / git hygiene (untrack weights, safe sweep)
[ ] O3  Gold pack + SFT manifest refresh
[ ] O4  Colab train (or local MLX if RAM allows)
[ ] O5  Install checkpoint + eval (≥3/4 to promote)
[ ] O6  Optional replay / council dataset boost
[ ] O7  Paper soak review (logs + war ledger)
[ ] O8  Preflight + commit brain artifacts if promoted
```

---

## O1 — Clean shutdown (5 min)

| Step | Command | Pass |
|------|---------|------|
| Stop live | `./scripts/stop_hanoon.sh` | "HANOON stopped" |
| Stop Halim TG | `./scripts/halim_stop.sh` 2>/dev/null || true | — |
| Release IB slot | `python3 scripts/guard_ib_client_id.py --client-id 1 --release` | no error |
| Verify no scalper | `pgrep -fl main.py` | empty |

**Why:** Skipping this loses Halim gold, coevolution rows, and git sync on hard kill.

---

## O2 — Disk & git hygiene (15–30 min)

| Step | Command | Notes |
|------|---------|-------|
| Quit Cursor | Cmd+Q | Required before LFS untrack |
| Untrack weights | `./scripts/untrack_halim_weights.sh` | Keeps files on disk; shrinks `.git/lfs` |
| Safe sweep | `./scripts/deep_sweep.sh` | Default = safe (no Homebrew prune) |
| Optional aggressive | `DEEP_SWEEP_AGGRESSIVE=true HALIM_GIT_LFS_PRUNE=true ./scripts/deep_sweep.sh` | Only if disk tight |
| Remove Downloads dupes | Manual or `DEEP_SWEEP_PRUNE_DOWNLOADS=true` with aggressive | Keeps one Colab zip backup if you want |

**Verify:**

```bash
du -sh .git .git/lfs halim/data/checkpoints/toddler_v1
git ls-files 'halim/data/checkpoints/**/*.safetensors' | wc -l   # target: 0
```

---

## O3 — Gold pack & SFT refresh (20–45 min)

| Step | Command | Pass |
|------|---------|------|
| Full gold pipeline | `./scripts/halim_v5_ready.sh --skip-learn` | SFT manifest updated |
| With learn cycles | `./scripts/halim_v5_ready.sh` | More council/json pairs |
| Colab zip ready | `./scripts/halim_colab_ready.sh` | zip in `halim/data/training/` or Downloads |
| Record hashes | `./scripts/halim_record_train.sh` | for incremental Colab |

**Targets (from brain maturity):**

| Metric | Child target | Check |
|--------|--------------|-------|
| Council pairs | 200+ (`BRAIN_CHILD_DATASET_TARGET`) | `wc -l models/council_training_dataset.jsonl` |
| JSON entry gold | growing | `wc -l halim/data/training/json_entry_gold.jsonl` |
| SFT pairs | 2500+ for native LM readiness | `halim/data/training/sft/manifest.json` |

---

## O4 — Train Halim (pick one path)

### Path A — Colab (recommended on 8 GB)

1. Upload zip from O3 to Google Drive / Colab.
2. Run `halim/colab/train_toddler_colab.py` (or notebook in repo).
3. Download `halim_toddler_vN.zip` to `~/Downloads`.

### Path B — Local MLX LoRA (only if ≥16 GB or HANOON stopped + nothing else running)

```bash
./scripts/halim_smart_sprint.sh --with-retrain   # if script supports; else halim auto LM off-hours
# Or: python halim/scripts/train_toddler.py --iters 200 --batch-size 1
```

**8 GB rule:** Do **not** run Colab train + Halim serve + HANOON simultaneously.

---

## O5 — Install, eval, promote (30 min)

| Step | Command | Pass |
|------|---------|------|
| Install zip | `./scripts/halim_apply_colab_checkpoint.sh ~/Downloads/halim_toddler_vN.zip` | merged + adapter on disk |
| Restart serve | `./scripts/ensure_halim_active.sh --serve-only --restart` | `curl -s :8765/health` → ok |
| Eval probes | `python3 halim/scripts/eval_toddler.py` | **≥3/4** token AND JSON |
| Promote (only if eval passes) | `HALIM_PROMOTION_FORCE=false ./scripts/halim_register_checkpoint.sh toddler_v1` | `models/halim_promotion_state.json` promoted |
| Force (emergency only) | `HALIM_PROMOTION_FORCE=true ...` | Document why in BRAIN_DEVELOPMENT_LOG |

**Probe meaning:**

| Score | Action |
|-------|--------|
| 0–2/4 | Stay toddler; more JSON gold (O3) + retrain |
| 3–4/4 | Promote; bump `HALIM_ENTRY_BLEND_WEIGHT` cautiously in `.env` |
| Empty LM live | Check serve reason in logs; run preflight before RTH |

---

## O6 — Optional replay & dataset boost (1–2 hr)

Use when council pairs below child target.

```bash
# Weekend / off-hours replay (collects gold, no live IB risk if configured)
./scripts/weekend_replay_train.sh
# or shorter:
./scripts/start_replay_live.sh   # stop with stop script when done
```

**Env (already in sprint profile):**

- `HALIM_REPLAY_GOLD_COLLECT=true`
- `REPLAY_DECISION_API_DAILY=64` (cap API spend)

**Stop replay** before next RTH live session.

---

## O7 — Paper soak review (weekly, 15 min)

After several RTH sessions, grep logs:

```bash
# Bad patterns
rg 'ERROR|SyntaxError|index\.lock|War IB sync.*error|stuck|partial' logs/HANOON.log | tail -30

# Good patterns
rg 'GREEN veto|strict.*profit|graceful|Halim entry LM ready' logs/HANOON.log | tail -20

# War ledger sanity
python3 -c "from core.war_account import WarAccount; from core.config import BotConfig; print(WarAccount(BotConfig()).summary())"
```

| Signal | Action |
|--------|--------|
| Repeated `Hot-path exit_ib_position_check` | IB Gateway / data issue — restart Gateway |
| `load_failed` on Halim empty | Restart serve; verify merged weights exist |
| Phantom war slots | `./scripts/stop_hanoon.sh` then review `ENGINEERING_FIX_LOG` war entries |
| Many green vetoes, few fills | **Expected** — rails working |

---

## O8 — Pre-RTH launch prep (10 min, next morning)

```bash
./scripts/preflight_m2.sh
./scripts/ensure_halim_active.sh --serve-only --restart
./scripts/start_hanoon.sh
# Confirm banner:
#   Device profile: m2_8gb_live
#   await=1.0 | strict_prob=true
tail -f logs/HANOON.log
```

---

## Session templates

### Short night (45 min)

O1 → O2 (safe sweep only) → O3 `--skip-learn` → sleep (Colab train async)

### Standard night (2 hr)

O1 → O2 → O3 full → O4 Colab → O5 install + eval

### Weekend (half day)

O1–O5 → O6 replay → O7 log review → update `docs/BRAIN_DEVELOPMENT_LOG.md` one line

---

## What NOT to do off-hours

| Don't | Why |
|-------|-----|
| Run live HANOON + full MLX retrain | OOM on 8 GB |
| `DEEP_SWEEP_AGGRESSIVE=true` casually | Deletes caches/Homebrew bottles |
| Commit `secrets/hanoon.env.enc` | Encrypted vault — sync via your secret workflow only |
| `HALIM_PROMOTION_FORCE=true` without eval | Toddler ramble enters blend |
| Ctrl+C stop during replay | Skips gold flush |

---

## Done criteria (off-hours program complete)

You can stop the off-hours cycle when **all** are true:

- [ ] `eval_toddler.py` ≥ **3/4** JSON on commander probes
- [ ] `halim_promotion_state.json` shows `promoted: true`
- [ ] `.git/lfs` < 1 GB; safetensors untracked from git index
- [ ] 5+ paper RTH days without war ledger phantom / stuck exit
- [ ] `preflight_m2.sh` passes before every session
- [ ] Council dataset ≥ child target (200+ pairs)

Until then: **PPO + green doctrine + council** remain the execution brain; Halim stays advisory.

---

## Related docs

| Doc | Role |
|-----|------|
| [SYSTEM_ASSESSMENT_2026-07-01.md](SYSTEM_ASSESSMENT_2026-07-01.md) | Why not “perfect” yet |
| [PERFECTION_ROADMAP_M2_8GB.md](PERFECTION_ROADMAP_M2_8GB.md) | Live-session perfection (done) |
| [COLAB_TRAINING.md](COLAB_TRAINING.md) | Colab details |
| [ENGINEERING_FIX_LOG.md](ENGINEERING_FIX_LOG.md) | Regression history |
| [OPS.md](OPS.md) | Day-to-day ops |

---

*Last updated: 2026-07-01 · Device: MacBook Air M2 8 GB*
