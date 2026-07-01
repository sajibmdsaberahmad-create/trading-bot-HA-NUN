# PPO-Led Coevolution Profile

**Status:** Active in `scripts/start_hanoon.sh` (profile block at end of env exports)  
**Date:** 2026-07-01  
**Related:** [PPO_WHEEL_ARCHITECTURE.md](PPO_WHEEL_ARCHITECTURE.md) · [VISION_SMART_STACK.md](VISION_SMART_STACK.md) · [ENGINEERING_FIX_LOG.md](ENGINEERING_FIX_LOG.md)

---

## Mission

One decision chain — **no overlapping gates**:

1. **PPO** — first reflex and executioner when setup clears green.
2. **Halim** — async monitor, blend, complement, coevolution gold (does not clock-block PPO).
3. **API (Groq/Gemini)** — master teacher on **hard cases only** (disagreement, uncertainty, curriculum sample).
4. **🟢 Green doctrine** — **non-negotiable** single mechanical truth before any order.

Learning happens through **actions and verdicts**, not hourly clocks.

---

## Decision chain (live)

```text
SPIKE (scanner + micro)
    │
    ├─ Survival rails (IB connected, not halted, quote sanity)
    │
    ├─ Entry quality assess (advisory blend — not hard block unless re-enabled)
    │
    ├─ 🟢 GREEN doctrine (HARD — uptrend, green bar, pred up, profit prob, ai_vote)
    │       └── fail → 🟢 GREEN veto log, skip 12s
    │
    ├─ PPO reflex (BUY/HOLD + confidence)
    │
    ├─ Halim entry LM (async, ~1s peek — does not block 4.5s)
    │       blend / complement / gold register
    │
    ├─ Council (Groq/Gemini) — non-blocking when PPO_LEAD_WHILE_COUNCIL_PENDING=true
    │       API teacher only when should_ring_teacher_api() says hard case
    │
    └─ IB order → fill → verdict.jsonl → PPO + Halim + gold learn
```

---

## What changed (2026-07-01)

### 1. Unlimited hourly entries

| Variable | Old (typical) | New | Meaning |
|----------|---------------|-----|---------|
| `MAX_ENTRIES_PER_HOUR` | 2 → 5 | **0** | `0` = disabled (`check_entry_rate_limit`) |
| `WAR_MAX_ENTRIES_PER_HOUR` | 2 | **0** | No war hourly cap |
| `WAR_PAPER_MAX_ENTRIES_PER_HOUR` | 5 | **0** | No paper war hourly cap |

**Still active:** daily war round-trip caps (`WAR_MAX_ROUND_TRIPS_PER_DAY`), ticker loss cooldowns, risk halt, max concurrent positions.

### 2. Overlapping gates turned OFF (env only — code preserved)

| Variable | Old default | New | Why off |
|----------|-------------|-----|---------|
| `SMART_STACK_STRICT_PROFIT_PROB` | `true` | **`false`** | Duplicated green `profit_p` + pre-green hard veto |
| `SMART_STACK_AI_SURE_ENTRY` | `true` | **`false`** | Duplicated green `ai_vote` + forced council alignment |
| `COMMANDER_RUNTIME_ENABLED` | `true` | **`false`** | Lottery 80% prob / score 70 / spike 2× stacked on green |
| `HALIM_ENTRY_SOFT_VETO` | `true` | **`false`** | Halim advises; PPO leads execution |
| `HALIM_ENTRY_AWAIT_SEC` | 2.5–4.5 | **`1.0`** | Quick peek; no long clock-wait before PPO path |

### 3. Single aligned floors (~58%)

| Variable | Value |
|----------|-------|
| `MIN_PROFIT_PROBABILITY` | 0.58 |
| `CAPITAL_MIN_PROFIT_PROBABILITY` | 0.58 |
| `WAR_MIN_PROFIT_PROBABILITY` | 0.58 (was 0.80 live) |
| `WAR_PAPER_MIN_PROFIT_PROBABILITY` | 0.58 |
| `CAPITAL_MIN_CONFIDENCE` | 0.58 (was 0.65) |
| `CONFIDENCE_THRESHOLD` | 0.58 |

Green doctrine still enforces its own `min_pp` / `min_conf` / `ai_vote` on top of these.

### 4. Kept ON (chain enablers)

| Variable | Value | Role |
|----------|-------|------|
| `GREEN_DOCTRINE_ENTRY` | `true` | Mandatory green bar stack |
| `PPO_LEAD_WHILE_COUNCIL_PENDING` | `true` | PPO can enter while council in flight |
| `HALIM_PPO_COMPLEMENT` | `true` | Halim can lift PPO HOLD when quality-led |
| `HALIM_PPO_COEVOLUTION` | `true` | Mutual PPO ↔ Halim learning |
| `SMART_STACK_TEACHER_HARD_ONLY` | `true` | API not on every ticker |
| `SMART_STACK_ADVISORY_GATES` | `true` | MTF/regime → context only |
| `HALIM_ENTRY_LM_ENABLED` | `true` | Local monitor brain |

### 5. `fill_tracker` fix (same release)

`snapshot_market_price()` no longer calls `qualifyContracts` off the async path. Uses IB Truth marks first; avoids `qualifyContractsAsync was never awaited` during council risk checks.

---

## KEEP vs TURN OFF reference

### Always KEEP

- 🟢 Green doctrine (`GREEN_DOCTRINE_ENTRY=true`)
- IB survival rails (connectivity, halt, fill sync)
- Spike detection + quote sanity
- PPO reflex + online learning
- Halim LM + coevolution gold
- Sampled API teacher
- Verdict logging (`smart_stack_verdicts.jsonl`)
- Ticker loss cooldowns after repeated losses

### OFF in this profile (re-enable via env)

- Hourly entry caps
- Strict pre-green profit prob block
- AI-SURE double alignment
- Commander lottery runtime floors
- Halim soft veto over PPO

### TUNE after seeing results

- `MIN_PROFIT_PROBABILITY` / `CAPITAL_MIN_CONFIDENCE` (raise if too loose)
- `AI_COUNCIL_MAX_WAIT_SEC` (speed only)
- `HALIM_ENTRY_BLEND_WEIGHT`
- `SPIKE_SKIP_SEC` after green veto

---

## How to apply

```bash
./scripts/stop_hanoon.sh && ./scripts/start_hanoon.sh
```

Profile is **last block** in `start_hanoon.sh` — overrides earlier duplicate exports.

---

## How to verify

1. **No hourly cap logs:** should not see `👁 hourly entry cap` / rate limit messages.
2. **Green still vetoes:** `🟢 GREEN veto` when uptrend/ai_vote missing (expected).
3. **PPO lead:** entries can proceed with `council:in_flight` when green + PPO strong.
4. **Halim peek:** logs show `Halim entry fresh … (await 1.0s)` not 4.5s.
5. **Tests:**
   ```bash
   venv/bin/pytest tests/test_position_entry_price.py tests/test_notify_ib_context.py -q
   ```

---

## Roll back to strict lottery (without code delete)

```bash
export COMMANDER_RUNTIME_ENABLED=true
export COMMANDER_LOTTERY_MIN_PROFIT_PROB=0.80
export SMART_STACK_AI_SURE_ENTRY=true
export SMART_STACK_STRICT_PROFIT_PROB=true
export MAX_ENTRIES_PER_HOUR=2
export WAR_MAX_ENTRIES_PER_HOUR=2
./scripts/start_hanoon.sh
```

---

## Known remaining overlap (future env flag)

Green is checked **twice** by default (spike loop + verdict finalize). Set `GREEN_VERDICT_RECHECK=false` in the PPO wheel profile to skip the second check. See [PPO_WHEEL_ARCHITECTURE.md](PPO_WHEEL_ARCHITECTURE.md).

---

## Files touched

| File | Change |
|------|--------|
| `scripts/start_hanoon.sh` | PPO-led profile block + hourly caps 0 |
| `core/fill_tracker.py` | IB-truth-first market price; safe async |
| `tests/test_position_entry_price.py` | Tests for snapshot_market_price |
| `docs/ENGINEERING_FIX_LOG.md` | Fix journal entries |
| `docs/BRAIN_DEVELOPMENT_LOG.md` | One-liner |
| `docs/PPO_LED_COEVOLUTION_PROFILE.md` | This document |
