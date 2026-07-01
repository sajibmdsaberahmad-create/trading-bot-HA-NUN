# Perfection Roadmap — MacBook Air M2 8 GB

**Goal:** Make HANOON as close to “perfect” as this device allows — without pretending toddler Halim is adult.  
**Assessment:** [SYSTEM_ASSESSMENT_2026-07-01.md](SYSTEM_ASSESSMENT_2026-07-01.md)  
**Rule:** Execute phases in order; check boxes before moving on.

---

## What “perfect on 8 GB” means (realistic target)

| Layer | Target on M2 8 GB |
|-------|-------------------|
| **Survival** | All hard rails on; fail-loud on IB grounding; live-money guard |
| **Config** | One canonical profile; no conflicting await/profit flags |
| **Halim** | Merged MLX stable; empty LM logs reason; 1s micro-peek optional |
| **Ops** | Preflight script; disk audit; no silent aggressive cleanup |
| **Tests** | Integration smoke for decide_entry, ram-live, profile |
| **CI** | pytest on push; workflow tracked in git |
| **Brain** | Still toddler until SFT retrain — **documented, not blocking launch** |

We do **not** claim Halim JSON perfection until child-stage retrain off-hours.

---

## Phase checklist

All phases implemented **2026-07-01** — verified: `pytest tests/ -q` → **204 passed**.

### Phase A — Canonical M2 env profile ✅
- [x] A1–A5

### Phase B — Halim serve reliability ✅
- [x] B1–B4

### Phase C — Fail-loud ops + live guard ✅
- [x] C1–C4

### Phase D — Integration tests ✅
- [x] D1–D3

### Phase E — Preflight + CI ✅
- [x] E1–E3

### Phase F — Journal + verify ✅
- [x] F1–F3

---

## Canonical M2 profile (reference values)

Sourced last on ≤12 GB Macs via `scripts/m2_8gb_live_profile.sh`:

| Variable | Value | Why |
|----------|-------|-----|
| `HANOON_DEVICE_PROFILE` | `m2_8gb_live` | Traceability in logs |
| `RAM_LIVE_ONLY` | `true` | No RTH disk sweep |
| `PERIODIC_CLEANUP_SEC` | `0` | Same |
| `AUTO_DISK_CLEANUP` | `false` | No silent cleanup |
| `SMART_STACK_STRICT_PROFIT_PROB` | `true` | Hard quality veto |
| `GREEN_DOCTRINE_ENTRY` | `true` | Uptrend alignment |
| `HALIM_SERVE_PREFER_ADAPTER` | `false` | Merged MLX more stable |
| `HALIM_ENTRY_LM_TIMEOUT_SEC` | `12` | 8 GB cold inference |
| `HALIM_ENTRY_AWAIT_SEC` | `1.0` | Micro-peek without blocking PPO |
| `HALIM_FORCE_LM` | `true` | Serve reasoning on 8 GB |
| `WAR_ENTRY_ADVISORY_ONLY` | `true` | Posture not mute |
| `HALIM_DEVICE_SWEEP_ON_START` | `false` | No start sweep |

Override any var in `.env` before start if needed.

---

## Daily operator workflow (post-roadmap)

```bash
# Off-hours / before RTH
./scripts/preflight_m2.sh
./scripts/ensure_halim_active.sh --serve-only --restart

# Launch (profile auto on ≤12 GB RAM)
./scripts/start_hanoon.sh

# Graceful stop (never Ctrl+C)
./scripts/stop_hanoon.sh

# After quitting Cursor (shrink .git/lfs)
./scripts/untrack_halim_weights.sh
```

---

## Off-hours brain improvement (not blocking live)

**Full playbook:** [OFF_HOURS_WORK_PLAN.md](OFF_HOURS_WORK_PLAN.md)

1. `./scripts/halim_v5_ready.sh` — more JSON gold  
2. Colab retrain → `./scripts/halim_apply_colab_checkpoint.sh`  
3. `./halim/scripts/eval_toddler.py` — target ≥3/4 before promotion  
4. `HALIM_PROMOTION_FORCE` only after eval passes  

---

## Success criteria (roadmap complete)

- [x] Both assessment + roadmap docs exist and match code  
- [x] `pytest tests/ -q` passes (**204** tests)  
- [x] `preflight_m2.sh` exits 0 when Halim serve + checkpoint OK  
- [x] Start banner shows `m2_8gb_live` profile  
- [x] Empty Halim log includes serve failure reason when `ok: false`  
- [x] Live port 4001 blocked without explicit ack  

---

*Roadmap owner: engineering sprint 2026-07-01 · Device: MacBook Air M2 8 GB*
