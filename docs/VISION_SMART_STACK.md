# HANOON Life Engine — Vision & Maturity Roadmap

**Status:** Foundation live (Phases A–E) · Maturity ladder active  
**Hub module:** `core/smart_stack.py`  
**Maturity stages:** `core/brain_maturity.py`  
**Cursor rule:** `.cursor/rules/smart-stack-vision.mdc` (always applied)

This document is the **source of truth** for the entire operation — not just entry AI.
Any refactor must preserve: **one ship, one engine, AI everywhere it helps, RAM-first live sessions.**

---

## One-line mission

> **A single living trading engine — smart sensors, smart brains, smart war, super-fast execution — that uses AI capabilities when needed, learns from every spike, and makes profit faster, more accurately, and more intelligently over time.**

Mechanical gates, war rules, and survival rails **still exist** — but they use AI judgment as features and posture, not dumb kill switches that bypass the brain.

---

## One ship, not scattered boats

```
                    ┌─────────────────────────────────────────┐
                    │           HANOON LIFE ENGINE            │
                    │         (scalper_runner = hull)         │
                    └─────────────────────────────────────────┘
                                         │
     ┌───────────────┬──────────────┬────┴────┬──────────────┬──────────────┐
     ▼               ▼              ▼         ▼              ▼              ▼
 SMART          SMART           SMART      SMART          SMART         HARD
 SENSORS        PPO             HALIM      WAR            EXECUTION     SURVIVAL
 tick/bars      reflex ms       reason     posture        flash/bracket  rails only
 micro/MTF      online learn    local LLM  lottery/conf   IB fills       loss/kill/2161
 scanner        varied actions  blend      macro bumps    parallel poll  partial abort
```

**Anti-pattern:** Adding a new standalone script, gate, or “mini-bot” that decides entries without flowing through `decide_entry()` → `_finalize_entry_decision()` → `log_spike_verdict()`.

**Correct pattern:** New capability plugs into `core/smart_stack.py` or extends an existing organ (`ai_commander`, `war_account`, `entry_quality`, `sniper_execution`) on the same hull.

---

## What’s possible NOW vs what takes TIME

| Capability | Now (architecture + partial) | Takes time (data + maturity) | Foundation wired | Becomes mature |
|------------|------------------------------|------------------------------|------------------|----------------|
| Halim+PPO lead on every spike | ✅ Live | Halim adult-quality entries | `decide_entry`, smart stack | **Adult** stage + 1200+ gold rows |
| Remove council bypass on PPO HOLD | ✅ Live | — | Phase A | Always |
| Gates as features not vetoes | ✅ Live | Calibrated dynamic thresholds | Phase B, `regime_entry_caution` | **Child+** proxy calibration |
| Log all spikes for gold | ✅ Live | Large balanced training set | Phase D, verdicts.jsonl | **600+** labeled verdicts |
| API as sampled teacher only | ✅ Live | Zero-API session | Phase C, `brain_maturity` | **Adult** (350+ trades) |
| Smart war adaptive posture | ✅ Live | War brain tuned per regime | Phase E, `apply_smart_war_entry` | **Child+** + regime gold |
| Smart survival rails at execution | ✅ Live | — | risk.py, capital discipline | Always |
| PPO varied signals beyond 54% HOLD | 🔧 Foundation | Policy diversity from experience | online PPO, verdict rewards | **Child+** micro-steps |
| Smart sensors (micro+MTF+tick) | ✅ Live | Sensor fusion weights | spike loop, gate context | **Teen+** learned weights |
| Super-fast execution | ✅ Live | Sub-200ms adult latency budget | sniper flash, parallel entry | **Adult** tuning |

**Legend:** ✅ Live today · 🔧 Foundation collecting · maturity stage from `brain_maturity.STAGES`

Programmatic ladder: `core/smart_stack.maturity_ladder()`

---

## Brain maturity timeline (`brain_maturity.py`)

Stages unlock from cumulative trades, evolutions, and dataset size. Teacher API **fades** as students grow.

| Stage | Trades | Dataset | Council sample | API/day (decision) | What activates |
|-------|--------|---------|----------------|-------------------|----------------|
| **newborn** | 0 | 0 | 0% | 0 | PPO + heuristics; collect experiences |
| **infant** | 8+ | 0 | 8% | 4 | Tiny teacher glimpses; PPO micro-train |
| **toddler** | 25+ | 50+ | 18% | 12 | Teacher labels; Halim training begins |
| **child** | 60+ | 200+ | 35% | 25 | Student proxy assists; calibrated thresholds start |
| **teen** | 150+ | 600+ | 15% | 15 | Students lead; teacher hard cases only |
| **adult** | 350+ | 1200+ | 6% | 8 | Owned brain; API polish only; near zero-API sessions |

Halim quality, PPO diversity, war regime tuning, and threshold calibration **improve through these stages** — foundation code is already wired; maturity gates how much cloud help you still need.

---

## Live pipeline (every spike)

```
SENSE → PPO reflex (ms) → Halim reason (local) → blend
    → teacher API (sampled hard cases) → war posture → HARD survival rails → LEARN (verdicts.jsonl)
```

### Layer roles

| Layer | Role | Uses AI how | Must NOT |
|-------|------|-------------|----------|
| **Sensors** | Tick stream, micro forecast, MTF, scanner score | Signals → `smart_gate_context` for brains | Block spike before `decide_entry` |
| **PPO** | BUY/HOLD/SELL reflex + confidence | Online learn from verdicts/fills | Silent `ppo_hold_skip` |
| **Halim** | Local reasoning, profit prob, narrative | Leads entry blend | Be bypassed by mechanical pre-filters |
| **Teacher API** | Groq/Gemini curriculum labels | Only hard/disagreement/sampled cases | Run on all 47 names every spike |
| **Gates** | MTF, regime, quality, vol | `format_gate_context_for_prompt` | Hard-block in advisory mode |
| **War / sniper** | Lottery bands, conf/prob bumps | `war_posture_adjustments` | Mute pipeline before brains |
| **Execution** | Bracket, fill poll, flash paths | Fast path when blend confidence clears | Skip survival rails |
| **Hard rails** | Loss limits, kill switch, 2161, partial abort | Never — deterministic math | Be removed or softened |

---

## Phases A–E (implemented foundation)

| Phase | What | Module |
|-------|------|--------|
| **A** | PPO HOLD → Halim+council | `sniper_execution`, `ai_commander` |
| **B** | Gates advisory; hourly cap on fills | `smart_stack`, `entry_quality`, `scalper_runner` |
| **C** | Teacher curriculum sample | `should_ring_teacher_api`, `brain_maturity` |
| **D** | Every verdict → gold | `log_spike_verdict`, `_emit_spike_verdict` |
| **E** | War posture not mute | `apply_smart_war_entry`, sniper Halim flash |

---

## RAM-first live doctrine

**Goal:** Use installed RAM as the working set. No swapping, no memory hogging, no disk sweeping during market hours.

| Rule | Implementation |
|------|----------------|
| RAM tier auto-tune | `core/ram_tier.py` — council wait, prefetch, training caps |
| Memory pressure detect | `core/memory_guard.py` — headroom before heavy work |
| **RAM_LIVE_ONLY** (default `true` with smart stack) | No `run_periodic_cleanup` while market open |
| Off-hours cleanup | JSONL tail-trim via deque (O(max_lines) RAM), not full-file load |
| Verdict gold protected | `smart_stack_verdicts.jsonl` trim cap 20k lines |
| Halim inference | Local on Mac RAM — see `docs/HALIM_MAC_INFERENCE.md` |

**Anti-patterns during live session:**
- Aggressive `cleanup_local_workspace` while `can_trade`
- Loading entire multi-MB jsonl into memory
- Spawning duplicate model copies (one PPO, one Halim path)

Set `RAM_LIVE_ONLY=false` only for debugging disk issues on a dev machine.

---

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `SMART_STACK` | `true` | Master switch |
| `SMART_STACK_ADVISORY_GATES` | `true` | Gates → context only |
| `SMART_STACK_WAR_POSTURE` | `true` | War adjusts bars |
| `SMART_STACK_HOURLY_FILLS_ONLY` | `true` | Hourly cap = fills |
| `SMART_STACK_FLASH_HALIM_MIN_CONF` | `0.62` | Halim-led sniper flash |
| `RAM_LIVE_ONLY` | `true` (with smart stack) | No disk sweep while market open |
| `SNIPER_SKIP_COUNCIL_ON_PPO_HOLD` | `true` | Legacy only (`SMART_STACK=false`) |

---

## Key code locations

```
core/smart_stack.py           — hub: flags, advisories, war, teacher, verdicts, maturity_ladder
core/scalper_runner.py        — hull: spike loop, execution, RAM-live cleanup guard
core/ai_commander.py          — brain orchestration: decide_entry, finalize, verdict emit
core/entry_quality.py         — regime_entry_caution, mtf_entry_caution (signal vs block)
core/brain_maturity.py        — newborn → adult stages
core/ram_tier.py              — RAM tier profiles
core/memory_guard.py          — pressure detection
core/local_cleanup.py         — off-hours trim only
models/smart_stack_verdicts.jsonl — spike deliberation gold
```

---

## Hard rails (never demote)

Execution-time only — not “opinion”:

- Daily loss / consecutive loss halt
- Kill switch / shutdown
- Partial-fill abort + slippage flatten
- IB error 2161 / contract blacklist
- Max concurrent positions
- Bracket validator / ATR reject (sizing math)

---

## Future organs (same ship — foundation first)

These are **not separate bots**. Each gets a foundation hook now; maturity unlocks full power:

1. **Smart sensor fusion** — learned weights over micro + MTF + tick burst (teen+)
2. **War regime brain** — posture from regime-labeled gold, not static bumps (child+)
3. **PPO policy refresh** — HOLD collapse fixed by verdict-shaped rewards (child+)
4. **Halim continuous train** — nightly from verdicts + fills (toddler+)
5. **Zero-API day** — adult stage runs full session local (adult)
6. **Sub-200ms entry path** — pre-warmed Halim + PPO on focus tickers (adult)

---

## Verification checklist

1. PPO HOLD spike → `escalating to Halim+council`, not `ppo_hold_skip`
2. Regime/MTF caution → `GATE advisory` in log **and** in `gate_context` (even advisory mode)
3. `smart_stack_verdicts.jsonl` grows on every finalized deliberation
4. Startup: `LIFE ENGINE: Halim+PPO lead | … | RAM-live`
5. During market hours: no `🧹 Local cleanup done` log (unless `RAM_LIVE_ONLY=false`)
6. Adult maturity → teacher skip logs dominate over API rings

---

## Related docs

- `docs/HALIM.md` — local brain
- `docs/OWNED_BRAIN.md` — replacing cloud
- `docs/BRAIN_DEVELOPMENT_LOG.md` — milestone log
- `docs/SNIPER_LOCK_ARCHITECTURE.md` — sniper as posture
- `docs/WAR_ACCOUNT_LIVE.md` — war account → posture bumps
- `docs/HALIM_MAC_INFERENCE.md` — RAM inference

---

*When changing anything in the trading loop, read this file first. One engine. AI everywhere it helps. RAM live. Learn every spike.*
