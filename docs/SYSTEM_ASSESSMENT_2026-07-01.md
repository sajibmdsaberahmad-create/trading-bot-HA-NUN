# HANOON System Assessment — 2026-07-01

**Purpose:** Honest whole-program verdict after deep code review, live log analysis, and ops audit.  
**Device context:** MacBook Air M2, 8 GB RAM, IB paper (port 4002).  
**Companion:** [PERFECTION_ROADMAP_M2_8GB.md](PERFECTION_ROADMAP_M2_8GB.md) (implementation plan).

---

## Executive verdict

| Question | Answer |
|----------|--------|
| Is the program **perfect**? | **No.** |
| Is it **broken**? | **No.** |
| Safe for **supervised IB paper**? | **Yes**, with documented caveats. |
| Ready for **unattended live money**? | **Not yet.** |

**One line:** Well-architected Life Engine with strong survival rails and an immature reasoning brain — correct for toddler phase, not a finished autonomous product.

---

## Architecture (what you have)

### One ship

Live path: `main.py --mode scalper` → `ScalperRunner` (mixin hull ~8,400 lines).

| Component | Role |
|-----------|------|
| `scalper_spike_loop.py` | Sensors, spike detect, gate advisories |
| `scalper_entry_executor.py` | `_attempt_entry`, IB brackets, fill poll |
| `scalper_exit_executor.py` | Exits, partial flatten, green/slippage |
| `ai_commander_entry.py` | `decide_entry()` — PPO → Halim → council → finalize |
| `smart_stack.py` | Policy hub: flags, war posture, teacher sampling, verdict gold |
| `green_trade_doctrine.py` | Hard entry/exit alignment (uptrend, green bar, profit prob) |

### Pipeline (every spike)

```
SENSE → spike loop → _attempt_entry → decide_entry()
  → _finalize_entry_decision → _emit_spike_verdict
  → _submit_ai_entry (brackets) → LEARN (verdicts.jsonl, coevolution)
```

### Vision alignment (`docs/VISION_SMART_STACK.md`)

| Pillar | Status (default env) |
|--------|----------------------|
| One hull (`scalper_runner`) | ✅ `--mode scalper` |
| PPO → Halim → blend → sampled teacher | ✅ Wired |
| Gates advisory, not silent brain bypass | ✅ `SMART_STACK_ADVISORY_GATES=true` |
| War as posture | ✅ `SMART_STACK_WAR_POSTURE=true` |
| Hard survival rails at execution | ✅ Risk, brackets, 2161, partial abort |
| Learn every spike (verdicts) | ✅ Commander paths; ⚠️ pre-filters skip gold |
| RAM-live (no RTH disk sweep) | ✅ `RAM_LIVE_ONLY=true` |
| No scattered mini-bots | ⚠️ Legacy `LiveTrader`, swing horizon parallel |

---

## Scorecard

| Area | Grade | Summary |
|------|-------|---------|
| Architecture / vision | A− | Coherent smart stack; mixin sprawl |
| Survival rails | A | Green, profit prob, risk, brackets work (SOXS/BITO vetoes) |
| IB integration | B+ | Truth hub, reconnect, watchdog; recent war/sync fixes |
| Exit / partial fills | B | Retry flatten added; needs soak |
| Halim LM | D+ | Toddler 1/4 token, 0/4 JSON; empty/ramble live |
| PPO + quality + council | B+ | Carries live entry quality |
| Tests | C+ | ~193 tests; weak on `decide_entry` integration |
| Config clarity | C− | ~398 exports, 4 layered env scripts, conflicting defaults |
| Ops reliability | 6.5/10 | Improving; high recent bug churn |
| **Perfect?** | **No** | Foundation strong; brain + ops immature |

---

## What works (production intent)

### Safety rails
- **Green doctrine** — blocks counter-trend spikes (logged: `green_entry:need uptrend`).
- **Strict profit probability** — hard veto on red quality when enabled.
- **Risk halts**, bracket validator, IB spread cap, partial-fill abort.
- **War posture** — advisory sizing; IB sync for open slots.

### Trading loop
- PPO HOLD escalates to Halim+council (no default `ppo_hold_skip`).
- Graceful shutdown via `./stop.sh` (Halim gold, evolution, git).
- Client ID guard, IB Gateway watchdog, connectivity wait on loss.

### Learning flywheel
- Verdict jsonl, coevolution gold, council dataset, SFT/Colab pipeline.
- Brain maturity stages gate teacher API budget.

### Engineering discipline
- `ENGINEERING_FIX_LOG.md` + pre-commit hook.
- CI workflow (`tests.yml`) for pytest.

---

## What is not perfect (material gaps)

### 1. Halim LM (biggest gap)
- Promotion gate: **1/4 token, 0/4 JSON** (`models/halim_promotion_state.json`).
- Live: `Halim entry LM empty` (~250 ms = fail/timeout, not inference).
- When it responds: toddler ramble / training echo, not JSON.
- **By design at toddler:** PPO + quality + council lead; Halim = advisory + gold.

### 2. Test coverage holes
- Strong: IB, war, green, exit flatten (~45 files, ~193 tests).
- Weak: full spike → `decide_entry` → verdict → mock submit; ScalperRunner (~2 smoke tests).

### 3. Configuration fragility
- `start_hanoon.sh` sets `HALIM_ENTRY_AWAIT_SEC` to 2.5 → 1.0 → 0 in same file.
- Four layered scripts: `ppo_wheel_env`, `hanoon_profit_learn_env`, `halim_smart_sprint_env`, inline exports.
- Effective runtime config requires reading all layers.

### 4. Legacy / parallel paths
- `main.py --mode trade` (`LiveTrader`) — no smart-stack verdicts.
- Swing uses `swing_doctrine`, not `decide_entry`.
- Pre-`decide_entry` mechanical filters skip verdict gold.

### 5. Silent failures
- Many `except Exception: pass` in exit monitor and spike loop.
- IB grounding errors can degrade without loud alarms (INTC avgCost, cross-ticker bleed class).

### 6. M2 8 GB constraints
- Halim MLX ~1 GB; Cursor git LFS diff bleeds RAM.
- Cold serve / lock contention → empty LM on spikes.
- Aggressive disk cleanup (now gated — see fix log 2026-07-01).

### 7. Live money readiness
- No hard live-account interlock in start script.
- No IB Gateway integration CI.
- War pool advisory, not full-account firewall.

---

## Live log interpretation (SOXS / BITO example)

```
ENTRY SOXS: ENTER … ppo:micro_fast
Halim entry LM empty (259ms, serve no text)   ← toddler/timing, non-fatal
GREEN veto: need uptrend score=0.82 pp=0.90   ← rails saved capital
```

**Correct behavior:** PPO hunted spike; Halim didn't contribute; green blocked non-uptrend entry.

---

## Duplicate entry paths (conflict map)

| Path | Verdict gold | Notes |
|------|--------------|-------|
| Scalper → `decide_entry` | ✅ | Canonical |
| `AI_FULL_CONTROL=false` legacy | ❌ | Off by default |
| `LiveTrader` mode | ❌ | Separate orchestrator |
| Swing IB entry | Separate | Blocks scalp via `scalp_blocked_by_swing` |
| Sniper/micro-fast inside commander | ✅ via finalize | Speed paths; green still gates submit |

---

## Halim maturity snapshot

| Metric | Value |
|--------|-------|
| Phase | toddler |
| Owned brain stage | teen (718+ pairs) |
| Checkpoint | toddler_v1, 14,084 SFT pairs |
| Eval | 1/4 token, 0/4 JSON — promotion blocked |
| Serve | MLX on :8765; `reflex_only` profile overridden by `HALIM_FORCE_LM` |
| Live role | Advisory + gold; **not** execution brain |

---

## Operational reliability (2026-07-01)

Recent fixes: war IB sync, multi-position ledger, INLF partial exit retry, strict profit prob, disk cleanup gating.

Open risk classes: ledger drift, partial exits, Gateway snapshot stalls, env override confusion, silent IB excepts.

---

## “Perfect” checklist (honest)

| Requirement | Met? |
|-------------|------|
| Halim ≥3/4 JSON eval | ❌ |
| Integration tests on deliberation loop | ❌ |
| Single documented env profile | ❌ → **roadmap fixes** |
| Fail-loud IB grounding in hot paths | Partial → **roadmap fixes** |
| Multi-week clean paper soak | Unknown |
| One entry brain (scalper mode) | Mostly ✅ |
| CI unit tests | ✅ |
| CI IB integration | ❌ |

---

## References

- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md), [VISION_SMART_STACK.md](VISION_SMART_STACK.md)
- Env: [ENV_PROFILES.md](ENV_PROFILES.md)
- Fixes: [ENGINEERING_FIX_LOG.md](ENGINEERING_FIX_LOG.md)
- Plan: [PERFECTION_ROADMAP_M2_8GB.md](PERFECTION_ROADMAP_M2_8GB.md)
- Off-hours: [OFF_HOURS_WORK_PLAN.md](OFF_HOURS_WORK_PLAN.md)

---

*Assessment date: 2026-07-01 · Next review: after roadmap Phase F complete or child-stage promotion.*
