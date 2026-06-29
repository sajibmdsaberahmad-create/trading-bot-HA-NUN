# HANOON Complete System Guide

**Version:** Foundation live (Smart Stack Phases A–E) · Brain maturity ladder active  
**Last updated:** June 2026  
**Audience:** Operators, developers, and anyone who needs the full picture of what this program is and how it works.

---

## Table of Contents

1. [What HANOON Is](#1-what-hanoon-is)
2. [Mission & Design Philosophy](#2-mission--design-philosophy)
3. [System Architecture](#3-system-architecture)
4. [Repository Layout](#4-repository-layout)
5. [How to Run](#5-how-to-run)
6. [The Trading Hull: ScalperRunner](#6-the-trading-hull-scalperrunner)
7. [Market Scanning & Target Locking](#7-market-scanning--target-locking)
8. [Sensing Layer](#8-sensing-layer)
9. [Entry Decision Pipeline](#9-entry-decision-pipeline)
10. [Execution & Risk Management](#10-execution--risk-management)
11. [Exit & Profit Hunting](#11-exit--profit-hunting)
12. [War Account (Virtual Capital)](#12-war-account-virtual-capital)
13. [Halim: The Owned AI Mind](#13-halim-the-owned-ai-mind)
14. [Brain Maturity & Teacher API Fading](#14-brain-maturity--teacher-api-fading)
15. [Learning & Self-Improvement](#15-learning--self-improvement)
16. [Hard Survival Rails](#16-hard-survival-rails)
17. [RAM-Live Doctrine](#17-ram-live-doctrine)
18. [Environment Variables Reference](#18-environment-variables-reference)
19. [Supporting Infrastructure](#19-supporting-infrastructure)
20. [Legacy & Alternate Modes](#20-legacy--alternate-modes)
21. [Key Code Locations](#21-key-code-locations)
22. [MacBook Air M2 8GB: Can Halim Become a Proper Adult?](#22-macbook-air-m2-8gb-can-halim-become-a-proper-adult)

---

## 1. What HANOON Is

**HANOON** is a portable, AI-driven **penny and momentum stock scalper** that:

- Connects to **Interactive Brokers (IB Gateway)** for market data and order routing
- Scans a broad US equity universe for high-volume movers
- **Locks** onto the best 1–5 candidates and monitors them at tick speed
- Detects **volume spikes + uptrends** and enters with bracket orders
- Uses a **multi-brain AI stack** (PPO reflex, Halim local LM, cloud teacher, sklearn proxy) for entries and exits
- Enforces **hard survival rails** (loss limits, kill switch, bracket validation) that AI cannot bypass
- **Learns from every spike** — wins, losses, skips, and council deliberations become training gold

It is **not** a single neural network. It is a **Life Engine**: one ship (`ScalperRunner`) with specialized organs (scanner, brains, war account, execution, learning) wired through a single decision pipeline.

**Primary use case:** Paper or live scalping on small accounts (~$1,000 war capital) with institutional-style momentum hunting.

**Repo role:** Clean portable algo — clone on any Mac, decrypt secrets, run `./start.sh`.

---

## 2. Mission & Design Philosophy

### One-line mission

> A single living trading engine — smart sensors, smart brains, smart war, super-fast execution — that uses AI when it helps, learns from every spike, and makes profit faster and more intelligently over time.

### Core principles (Smart Stack)

| Principle | Meaning |
|-----------|---------|
| **One ship, not scattered boats** | No standalone entry bots. Everything flows through `decide_entry()` → `_finalize_entry_decision()` → `log_spike_verdict()`. |
| **AI decides WHEN; risk decides HOW MUCH** | PPO outputs HOLD/BUY/SELL only. Position size, stop, and target come from `core/risk.py` math. |
| **Gates advise, brains decide** | MTF/regime/quality gates feed context into prompts — they do not silently block spikes before the brain sees them (advisory mode). |
| **Teacher API is curriculum, not crutch** | Groq/Gemini label hard cases only. Sample rate fades as brain matures. |
| **RAM-first live sessions** | No disk sweeps during market hours. Working set stays in RAM. |
| **Hard rails never demote** | Loss limits, kill switch, 2161 blacklist, partial-fill abort — deterministic, always on. |

**Source of truth:** `docs/VISION_SMART_STACK.md` · Hub: `core/smart_stack.py`

---

## 3. System Architecture

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

### Live pipeline (every spike)

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
| **Teacher API** | Groq/Gemini curriculum labels | Only hard/disagreement/sampled cases | Run on all names every spike |
| **Gates** | MTF, regime, quality, vol | `format_gate_context_for_prompt` | Hard-block in advisory mode |
| **War / sniper** | Lottery bands, conf/prob bumps | `war_posture_adjustments` | Mute pipeline before brains |
| **Execution** | Bracket, fill poll, flash paths | Fast path when blend confidence clears | Skip survival rails |
| **Hard rails** | Loss limits, kill switch, 2161, partial abort | Never — deterministic math | Be removed or softened |

---

## 4. Repository Layout

```
tradingbot/
├── main.py                    CLI entry — dispatches modes
├── start.sh                   One-command launcher
├── ppo_trader.zip             Pre-trained PPO (~28 MB)
│
├── core/                      Trading + AI engine (~200 modules)
│   ├── scalper_runner.py      Hull — main spike loop (~8600 lines)
│   ├── ai_commander.py        Brain orchestration — decide_entry, finalize, verdicts
│   ├── smart_stack.py         Life Engine hub — flags, advisories, war, teacher, verdicts
│   ├── scanner.py             IB penny/momentum screener
│   ├── risk.py                Position sizing, stops, circuit breakers
│   ├── broker.py              IB bracket order placement
│   ├── war_account.py         Virtual capital ledger
│   ├── brain_maturity.py      newborn → adult growth stages
│   ├── entry_quality.py       Regime/MTF caution (signal vs block)
│   ├── sniper_execution.py    Flash/strong entry paths
│   ├── profit_hunting.py      Spike-top exits, wave-end detection
│   ├── council_brain.py       Groq/Gemini cloud teacher facade
│   ├── config.py              All tunable parameters
│   └── ...
│
├── halim/                     Owned LM — training, serve, checkpoints
│   ├── halim/                 Python package (engine, serve, device)
│   ├── data/checkpoints/      Toddler LoRA + merged weights
│   └── scripts/               Train, eval, sync from tradingbot
│
├── models/                    Runtime state, weights, ledgers, gold
│   ├── smart_stack_verdicts.jsonl   Spike deliberation gold
│   ├── council_training_dataset.jsonl
│   ├── owned_brain_state.json
│   ├── teacher_proxy.joblib
│   └── ...
│
├── scripts/                   start_hanoon.sh, halim_serve, publish, stop
├── docs/                      Architecture, launch, vision, this guide
├── dashboard/                 Web monitoring UI
└── secrets/                   Encrypted env vault (hanoon.env.enc)
```

---

## 5. How to Run

### Quick start (production)

```bash
chmod +x start.sh scripts/*.sh
./start.sh
```

This will: create venv, install deps, decrypt `secrets/hanoon.env.enc` → `.env`, start Halim serve, connect IB Gateway, run scalper.

### CLI modes (`main.py`)

| Mode | Command | Purpose |
|------|---------|---------|
| **Scalper** (primary) | `python main.py --mode scalper` | HANOON institutional penny scalper |
| Replay-live | `python main.py --mode replay-live` | Fake-live from CSV replay data |
| Trade (legacy) | `python main.py --mode trade` | Single-ticker PPO live trader |
| Warmup | `python main.py --mode warmup` | Train PPO from scratch |
| Evaluate | `python main.py --mode evaluate` | Offline backtest |
| Advanced-train | `python main.py --mode advanced-train` | PPO + Transformer + LSTM |
| Fusion-trade | `python main.py --mode fusion-trade` | Multi-model fusion (experimental) |

### Prerequisites

| Item | Detail |
|------|--------|
| macOS 12+ | 8 GB RAM minimum (M1/M2 OK) |
| Python | 3.10–3.11 (not 3.12 — Stable-Baselines3) |
| IB Gateway | Paper port 7497/4002, API enabled, Read-Only OFF |
| Groq/Gemini keys | Optional cloud teacher (in `.env`) |
| Halim toddler | `halim/data/checkpoints/toddler_v1` + `./scripts/halim_start_toddler.sh` |

### IB Gateway setup

1. Login → **Paper Trading**
2. API Settings: enable socket clients, port **7497** (paper) or **7496** (live)
3. Allow localhost only; Read-Only **OFF**
4. Start Gateway **before** `./start.sh`

---

## 6. The Trading Hull: ScalperRunner

**File:** `core/scalper_runner.py`  
**Entry:** `main.py --mode scalper` → `ScalperRunner(connector, cfg, notifier).run()`

### What it does (matches manual scalping methodology)

1. Scan full universe → select 1–5 stocks (most active, top movers, volume, VWAP)
2. Lock selected stocks and monitor continuously
3. Detect volume spike + uptrend before entry
4. Deploy capital per slot (war bullets, often ~$200–$1000 each)
5. Hard stop loss + hard take profit ALWAYS in place (IB brackets)
6. Trail profit to ride institutional algo waves
7. Early exit on slippage prediction / AI fade detection
8. High-frequency: every bar/tick analyzed
9. AI predicts entries/exits like a human trader

**Goal:** 60%+ win rate, systematic small-account growth.

### Main loop (adaptive timing: ~50ms–2s)

Each iteration:

1. **Service pending fills** — bracket entry confirmation first (never delayed by scans)
2. **Heal stale stream prices** — IB snapshot fallback if tick stream stalls
3. **Detect exits** — stop/target hits, partial fills, deferred closes
4. **Market clock** — RTH / pre-market / after-hours transitions; daily learning triggers
5. **Tick spike queue** — sub-second spike detection on locked names
6. **AI early exit** — `AICommander.decide_exit()` when in position
7. **Rescan policy** — only when flat with no locked targets (~5 min); **not** while hunting
8. **Attempt entry** — when spike + uptrend triggers on locked ticker
9. **Background** — copilot refresh, macro context, PPO buffer updates, Telegram commands

### Startup sequence

- Learning persistence guard, brain maturity banner, Halim runtime attach
- War account + lottery bank initialization
- Shadow circuit check, stale order cleanup
- Initial scan (curated or live IB scanner)
- Telegram listener, commander runtime, RAM tier auto-tune

### Focus mode (critical behavior)

When targets are **locked**, the bot does **not** rescan the full universe (a full IB scan can block tick monitoring for ~87 seconds). It concentrates fire on locked names until:

- Entry taken, or
- Stale lock released (~30 min with no entry), or
- Flat with no targets → rescan after ~5 minutes

---

## 7. Market Scanning & Target Locking

### Scanner (`core/scanner.py`)

Screens penny/momentum names:

- NASDAQ or NYSE (no OTC/Pink Sheets)
- Price ~$1.00–$20.00
- Min ~$10M market cap, min ~500K daily volume
- Relative volume > 1.5× average
- Price above 20-day SMA (uptrend)
- Volume spike or gap detected

Uses IB scanner API with timeout-safe streaming subscription. Falls back to curated universe when market closed or IB deferred.

### Sniper-Lock architecture

**Phase 1 — Scout (every 5–15 min):** Wide scan → rank candidates by composite score  
**Phase 2 — Lock:** Top 1–5 tickers held in thread-safe `SniperTargetLock`  
**Phase 3 — Heartbeat:** Ultra-fast monitoring on locked names only (tick callbacks)

Scoring factors (weighted): volume spike, regime alignment, volatility, order book imbalance, AI confidence.

**Files:** `core/sniper.py`, `core/sniper_screener.py`, `core/sniper_heartbeat.py`, `core/sniper_orchestrator.py`

### Uptrend filter (`_only_uptrend`)

Before entry, price must be:

- Above SMA20 (1% tolerance)
- Above VWAP (1% tolerance)
- At least 2 of last 8 closes rising
- ATR sanity (not > 10% of price)

Loose enough to catch algo waves early, not so strict that moves are missed.

---

## 8. Sensing Layer

### Three speeds of data (`core/data.py`)

| Layer | Source | Used for |
|-------|--------|----------|
| **Tick stream** | `reqTickByTickData` or 5s bar fallback | Spike detection, tick-level stop evaluation |
| **Fast bars (5s)** | Aggregated from ticks | ATR, micro forecast, volatility on entry |
| **Decision bars (1min)** | Aggregated from fast bars | PPO 30-bar observation window |

All fed by the same IB event loop (`ib.sleep()` pumps events each iteration).

### Additional sensors

| Sensor | Module | Output |
|--------|--------|--------|
| Micro forecast | `scalper_micro_predict.py` | Spike likelihood, direction hint |
| MTF caution | `entry_quality.py` | 5m/15m trend alignment advisory |
| Regime detector | `market_regime.py` | Session regime tag |
| Institutional detector | `institutional.py` | Algo-wave signals |
| Macro context | `market_context.py` | Session-wide hints for prompts |
| Scanner score | `scanner.py` | Rank 0–100 per ticker |
| Spike ratio | spike loop | Current vol vs average |

In Smart Stack advisory mode, all of these pack into `smart_gate_context` for AI prompts — they do not hard-block before `decide_entry`.

---

## 9. Entry Decision Pipeline

**Orchestrator:** `core/ai_commander.py` → `decide_entry()`

### Step-by-step flow

```
1. PPO reflex          → HOLD/BUY/SELL + confidence (ppo_trader.zip)
2. Hard vetoes         → War block, live trade guard, copilot hard SKIP
3. Gate advisories     → MTF, regime, quality → prompt context only
4. Fast paths          → Sniper flash, sniper strong, PPO strong spike, micro-fast
5. Halim local LM      → enter/skip, confidence, profit probability (blend)
6. Cloud council       → Groq/Gemini on sampled hard cases only
7. Finalize            → _finalize_entry_decision() — sizing hints, war posture, bracket math
8. Verdict log         → _emit_spike_verdict() → smart_stack_verdicts.jsonl
```

### PPO reflex

- Stable-Baselines3 policy loaded from `ppo_trader.zip`
- Action space: `Discrete(3)` — HOLD(0), BUY(1), SELL(2)
- Even on HOLD, Smart Stack **escalates to Halim+council** (never silent `ppo_hold_skip`)

### Fast entry paths (no council wait)

| Path | Trigger | Pipeline tag |
|------|---------|--------------|
| Sniper flash | Extreme vol spike + scan score + Halim/PPO alignment | `sniper:flash` |
| Sniper strong | Lottery band, strong momentum | `sniper:strong` |
| PPO strong spike | Disciplined score/vol/PPO conf | `ppo:strong_spike` |
| Micro-fast | Micro forecast + PPO alignment | `ppo:micro_fast` |

### Halim blend (`_blend_halim_entry`)

Local Halim LM (Qwen2.5-0.5B + your LoRA adapter via MLX on Mac) parses structured enter/skip output. Blended with PPO confidence and gate advisories. Halim can lead sniper flash when confidence ≥ `SMART_STACK_FLASH_HALIM_MIN_CONF` (default 0.62).

### Cloud teacher (`should_ring_teacher_api`)

Teacher API rings **only** when:

- Halim missing/stale on meaningful spike
- PPO/Halim disagreement on strong spike
- PPO HOLD on elevated spike
- Halim uncertain (conf < 0.55) on high scan score
- Curriculum sample (rate from brain maturity stage)

Otherwise: `teacher:halim_ppo_sufficient` — local students handle it.

### Finalize (`_finalize_entry_decision`)

- Applies Halim blend, gut-feel override, war posture bumps/vetoes
- Computes deploy cap, max risk, bracket parameters
- Validates against min confidence, min profit probability
- Every non-pending outcome → `_emit_spike_verdict()` for gold

### Key design rule

> **AI decides WHEN to enter. Risk engine decides HOW MUCH.**

PPO never outputs shares, stop price, or target price.

---

## 10. Execution & Risk Management

### IB bracket orders (`core/broker.py`)

Every entry submits **three linked orders to IB**:

1. Parent entry (limit or market)
2. STOP child (hard stop)
3. LIMIT take-profit child

One-Cancels-All linkage. Once acknowledged, all three live on **IB's matching engine** — survives disconnects, Mac sleep, bot crashes.

Python-side trailing logic re-submits updated stop prices to IB to stay in sync.

### Position sizing (`core/risk.py`)

Backward from dollar risk constraint:

```
risk_usd       = min(equity × RISK_PER_TRADE_PCT, MAX_RISK_PER_TRADE_USD)
stop_distance  = ATR × STOP_ATR_MULTIPLIER  (clamped 0.3%–2%)
shares         = risk_usd / stop_distance
```

Then secondary caps: available cash, MAX_POSITION_PCT, MAX_SHARES_PER_TRADE, liquidity cap, spread check.

### Entry flow (`_attempt_entry`)

- Spread check (IB error 2161 protection)
- Liquidity cap on shares
- Parallel fill polling (`PARALLEL_ENTRY_EXIT`)
- Bracket validation (`bracket_validator.py`)
- Partial-fill abort + slippage flatten
- Entry failure cooldown + contract blacklist learning

### ATR-based stops

Fixed percent stops fail in varying volatility. ATR adapts to current movement; MIN/MAX stop distance clamps prevent freak values.

---

## 11. Exit & Profit Hunting

### Four automatic exit mechanisms

Every open position tracked by `TradePlan` (`core/risk.py`):

```
                         entry_price
                              │
    ┌─────────────────────────┼─────────────────────────┐
    │                          │                          │
hard stop              entry placed              hard take-profit
(ATR × 1.5,             with both as                (ATR × 2.5)
 clamped)               IB bracket orders
    │                                                     │
    │         once price moves favorably:                │
trailing stop                                    trailing profit
arms at +0.5%                                    arms at +1%
trails by ATR×1.2                                allows 40% giveback
```

- **Tick-level:** `evaluate_tick()` on every print — stop breach acted immediately
- **Decision-bar-level:** AI HOLD/BUY/SELL evaluated once per decision bar

### AI-driven exits

| Mechanism | Module | When |
|-----------|--------|------|
| Spike-top detection | `profit_hunting.py` | Volume spike fading at highs |
| Wave-end on spike fade | `profit_hunting.py` | Momentum exhaustion |
| `decide_exit()` | `ai_commander.py` | Halim + council early exit |
| Green profit lock | `green_profit_lock.py` | AI stalls in green |
| Mechanical bypass | `profit_hunting.py` | Strong profit — council optional |

---

## 12. War Account (Virtual Capital)

**Module:** `core/war_account.py`

IB balance is **not** used for sizing. War account maintains a virtual ledger with realistic small-account constraints.

### Concepts

| Term | Meaning |
|------|---------|
| **War NAV** | Operating capital (e.g. $1,000 paper, $1,200 live) |
| **Bullets** | ~5 shots per day, ~20% NAV each |
| **Round-trip cap** | Max 3/day default |
| **T+1 settlement** | Sells free unsettled cash until next session |
| **Fees + slippage** | Applied on virtual fills (pennies slip more) |

### Modes

| Mode | When | Entries | PPO promotion |
|------|------|---------|---------------|
| `WAR_ACTIVE` | Paper, war cash available | Yes | Yes |
| `LIVE_WAR` | Real account | Yes | Yes |
| `LAB_ACTIVE` | War settled; lab pool has cash | Yes (small) | No — gold only |
| `OBSERVE` | War + lab dry | No — watch + log | No |

### Default paper config (`scripts/start_hanoon.sh`)

```bash
WAR_ACCOUNT_ENABLED=true
WAR_CAPITAL_USD=1000
WAR_BULLETS=5
WAR_MAX_ROUND_TRIPS_PER_DAY=3
MAX_ENTRIES_PER_HOUR=2
WAR_SNIPER_MODE=true
```

IB is only the **order router**. The brain believes **war NAV**, not IB's fantasy paper buying power.

---

## 13. Halim: The Owned AI Mind

**HANOON = body. Halim = mind.**

Halim is **your own AI model** — not Groq, Gemini, Claude, or Ollama running someone else's weights. Weights live on disk; datasets built from your bot's real decisions.

### Two-path inference (never mixed)

```
FAST PATH (always, microseconds)          SLOW PATH (optional, ms–seconds)
─────────────────────────────────         ─────────────────────────────────
PPO zip          ─┐                       Halim serve (127.0.0.1:8765)
sklearn proxy    ─┼→ NEVER over HTTP  →   POST /v1/complete → Halim LM
scalper_weights  ─┘                       Fallback: Groq/Gemini → fade out
```

### Student stack

| Student | File | Role |
|---------|------|------|
| Reflex | `ppo_trader.zip` | Millisecond trade policy |
| Enter/skip proxy | `teacher_proxy.joblib` | Distilled council decisions |
| Scanner weights | `scalper_weights.json` | Heuristic tuning |
| Toddler LM | `halim/data/checkpoints/toddler_v1` | Local reasoning (Qwen2.5-0.5B + LoRA) |
| Training gold | `smart_stack_verdicts.jsonl`, `council_training_dataset.jsonl` | Future SFT rounds |

### Lifecycle phases

```
newborn   → PPO + proxy + heuristics (no external LLM required)
toddler   → First Halim transformer on trading dataset
child     → + code, math, reasoning corpora
adult     → On-device Halim inference, API polish only
frontier  → Full generative / calculative / coding model
```

### Mac inference (MLX)

On Apple Silicon, Halim toddler runs via **MLX** (Metal-optimized):

- Base: `mlx-community/Qwen2.5-0.5B-Instruct-4bit`
- Your LoRA adapter from Colab training
- Serve: `./scripts/halim_serve.sh` → `http://127.0.0.1:8765`

See: `docs/HALIM_MAC_INFERENCE.md`

### Native mode (zero external LLM)

```bash
export HALIM_NATIVE=true
export COUNCIL_ENABLED=false
./scripts/halim_native_replay.sh
```

HANOON trades using **only** owned students — PPO, proxy, local Halim, heuristics.

---

## 14. Brain Maturity & Teacher API Fading

**Module:** `core/brain_maturity.py`

Stages unlock from cumulative trades, evolutions, and dataset size. Teacher API budget **shrinks** as students improve.

| Stage | Trades | Dataset | Council sample | API/day (decision) | What activates |
|-------|--------|---------|----------------|-------------------|----------------|
| **newborn** | 0 | 0 | 0% | 0 | PPO + heuristics; collect experiences |
| **infant** | 8+ | 0 | 8% | 4 | Tiny teacher glimpses |
| **toddler** | 25+ | 50+ | 18% | 12 | Teacher labels; Halim training begins |
| **child** | 60+ | 200+ | 35% | 25 | Student proxy assists |
| **teen** | 150+ | 600+ | 15% | 15 | Students lead; teacher hard cases only |
| **adult** | 350+ | 1200+ | 6% | 8 | Owned brain; API polish only |

Check status:

```bash
PYTHONPATH=. python scripts/owned_brain_status.py
```

Proxy accuracy multipliers further reduce API caps (75% → 35% → 20% as accuracy rises).

**Adult ≠ zero API in code** — adult still allows ~8 decision API calls/day and 6% sample rate. True zero-API sessions require `COUNCIL_ENABLED=false` or reaching newborn-equivalent budget (0/day) via config.

---

## 15. Learning & Self-Improvement

The bot learns continuously from every session.

### During live session

| System | Output | Purpose |
|--------|--------|---------|
| Spike verdicts | `models/smart_stack_verdicts.jsonl` | Every entry deliberation |
| Experience buffer | in-memory + disk | PPO online micro-training |
| Deferred council | async gold | Late council answers correct PPO |
| Profit hunt ledger | `models/profit_hunt_ledger.jsonl` | Exit timing lessons |
| Action log | `halim/data/actions/action_log.jsonl` | Halim learn-by-doing |

### Session end / off-hours

| System | Module | Purpose |
|--------|--------|---------|
| Daily IB learning | `daily_ib_learning.py` | IB executions → analyze → PPO train |
| Post-session evolution | `owned_brain_evolution.py` | Export dataset, train proxy, refresh weights |
| Commander learning | `commander_learning.py` | Persist tuned params |
| Halim auto LM retrain | `halim_auto_lm.sh` | Export gold → SFT → MLX LoRA (+150 pairs) |
| PPO coevolution | `halim_ppo_coevolution.py` | Mutual gold between PPO and Halim |

### Persistent memory

| Asset | Purpose |
|-------|---------|
| `consciousness.json` | Long-term AI memory |
| `pilot_experience.json` | Veteran confidence thresholds |
| `pattern_memory_bank.json` | Recurring setup memory |
| `copilot_state.json` | Session reasoning cache |
| `scalper_weights.json` | Learned scanner weights |

### Git sync

Learning artifacts auto-push on shutdown, model release, daily IB learning. Clean repo publish to portable HANOON repo.

---

## 16. Hard Survival Rails

These are **deterministic math** — AI cannot bypass them:

- Daily loss / consecutive loss halt
- Kill switch / graceful shutdown (`./scripts/stop_hanoon.sh`)
- Max concurrent positions
- Partial-fill abort + slippage flatten
- IB error 2161 / contract blacklist
- Bracket validator / ATR reject (sizing math)
- Shadow circuit (paper safety — blocks real orders when triggered)
- Entry rate limits / hourly fill caps
- Spread gate (2161 risk)
- Live trade guard cooldowns after failures

Execution-time only — not "opinion."

---

## 17. RAM-Live Doctrine

**Goal:** Use installed RAM as the working set. No swapping during market hours.

| Rule | Implementation |
|------|----------------|
| RAM tier auto-tune | `core/ram_tier.py` — council wait, prefetch, training caps |
| Memory pressure detect | `core/memory_guard.py` — headroom before heavy work |
| **RAM_LIVE_ONLY** (default true) | No `run_periodic_cleanup` while market open |
| Off-hours cleanup | JSONL tail-trim via deque, not full-file load |
| Verdict gold protected | `smart_stack_verdicts.jsonl` trim cap 20k lines |
| Device trading focus | `DEVICE_TRADING_FOCUS=true` on ≤12GB Macs — Halim chat/learn paused during RTH |

**Anti-patterns during live session:**

- Aggressive disk cleanup while `can_trade`
- Loading entire multi-MB jsonl into memory
- Spawning duplicate model copies

---

## 18. Environment Variables Reference

### Smart Stack

| Variable | Default | Meaning |
|----------|---------|---------|
| `SMART_STACK` | `true` | Master switch |
| `SMART_STACK_ADVISORY_GATES` | `true` | Gates → context only |
| `SMART_STACK_WAR_POSTURE` | `true` | War adjusts bars, not hard veto |
| `SMART_STACK_HOURLY_FILLS_ONLY` | `true` | Hourly cap = fills not attempts |
| `SMART_STACK_FLASH_HALIM_MIN_CONF` | `0.62` | Halim-led sniper flash threshold |
| `RAM_LIVE_ONLY` | `true` | No disk sweep while market open |
| `SMART_STACK_TEACHER_HARD_ONLY` | `true` | API only on curriculum hard cases |

### Halim

| Variable | Default | Meaning |
|----------|---------|---------|
| `HALIM_LM_BACKEND` | `mlx` on Mac arm64 | Inference backend |
| `HALIM_BASE_MODEL` | Qwen2.5-0.5B 4bit | Scaffold weights |
| `HALIM_FORCE_LM` | `true` | Enable local LM on 8GB Mac |
| `HALIM_NATIVE` | `false` | No external LLM at all |
| `HALIM_DEVICE` | auto-detect | `m2_8gb`, `m2_16gb`, etc. |
| `HALIM_INFERENCE_TIMEOUT_SEC` | `90` on ≤12GB | Max wait for Halim reply |

### War / trading

| Variable | Default | Meaning |
|----------|---------|---------|
| `WAR_ACCOUNT_ENABLED` | `true` | Virtual capital ledger |
| `WAR_CAPITAL_USD` | `1000` | Paper war NAV |
| `COUNCIL_ENABLED` | `true` | Groq/Gemini teacher |
| `PAPER_TRADING` | `true` | Paper vs live |
| `OWNED_BRAIN_DEVICE` | auto | Device profile for training caps |

---

## 19. Supporting Infrastructure

| Component | Purpose |
|-----------|---------|
| **Telegram** | Trade alerts, inbound commands, daily summary, brain development broadcasts |
| **Git sync** | Auto-push learning artifacts; clean HANOON repo publish |
| **Dashboard** | `dashboard/app.py` — monitoring UI |
| **Replay system** | CSV replay for dev without live IB (`replay-live` mode) |
| **Encrypted env** | `secrets/hanoon.env.enc` — portable secrets across Macs |
| **Trading copilot** | Session-wide macro brief, confidence bumps |
| **Market hours** | US Eastern clock (`TZ=America/New_York`), RTH-only default |
| **Telegram listener** | Verify-any-account inbound copilot |
| **Halim developer** | Bounded param mutation + git sync (off-hours) |
| **Shadow mode** | Paper safety circuit — blocks broker when triggered |

---

## 20. Legacy & Alternate Modes

The repo retains older paths:

| Mode | File | Notes |
|------|------|-------|
| Single-ticker PPO | `core/trader.py` + `--mode trade` | Pre-HANOON design; one symbol |
| Fusion | `--mode fusion-trade` | PPO + Transformer + LSTM ensemble |
| Advanced train | `--mode advanced-train` | Multi-model training |
| Backtest engines | `archive_backtests/`, `backtest_*.py` | Offline evaluation |

**Production HANOON** = `--mode scalper` → `ScalperRunner`.

---

## 21. Key Code Locations

```
core/smart_stack.py           — hub: flags, advisories, war, teacher, verdicts
core/scalper_runner.py        — hull: spike loop, execution, RAM-live guard
core/ai_commander.py          — decide_entry, finalize, verdict emit
core/entry_quality.py         — regime_entry_caution, mtf_entry_caution
core/brain_maturity.py        — newborn → adult stages
core/war_account.py           — virtual capital ledger
core/sniper_execution.py      — flash/strong entry paths
core/profit_hunting.py        — spike-top exits, wave-end
core/council_brain.py         — Groq/Gemini facade
core/halim_inference.py       — HANOON ↔ Halim serve bridge
core/owned_brain_evolution.py — post-session flywheel
halim/halim/serve.py          — local LM HTTP server
models/smart_stack_verdicts.jsonl — spike deliberation gold
docs/VISION_SMART_STACK.md    — vision source of truth
```

---

## 22. MacBook Air M2 8GB: Can Halim Become a Proper Adult?

### Short answer

**Yes — you can reach adult *stage* and run zero-API trading sessions on M2 8GB.**  
**No — you should not expect adult-stage *quality* equivalent to Groq/Gemini on a 0.5B local model alone.**

The architecture supports this path; hardware is the constraint on reasoning depth, not on whether the system can operate without cloud APIs.

### What "adult" means in this codebase (two different things)

| Meaning | Requirement | Hardware role |
|---------|-------------|---------------|
| **Maturity stage "adult"** | 350+ closed trades, 8+ evolutions, 1200+ dataset pairs, trained proxy | **None** — earned through sessions |
| **Zero-API operation** | `COUNCIL_ENABLED=false` or daily API budget = 0 | **8GB sufficient** with owned stack |
| **Adult-quality reasoning** | Halim LM + proxy + PPO matching cloud teacher on hard cases | **8GB limited** — 0.5B quant only |

Adult stage in `brain_maturity.py` still allows **8 decision API calls/day** and **6% council sample rate**. It is "API polish only," not mathematically zero API. Full elimination requires explicit config.

### What runs on M2 8GB today

Your codebase is explicitly tuned for this machine:

| Component | 8GB M2 status | Notes |
|-----------|---------------|-------|
| PPO reflex | ✅ Runs | CPU/MPS, ~28 MB model |
| sklearn proxy | ✅ Runs | Milliseconds, tiny RAM |
| Halim 0.5B 4-bit MLX | ✅ Runs | `HALIM_FORCE_LM=true`, LoRA adapter (not merged weights) |
| Halim serve + HANOON simultaneously | ⚠️ Tight | `DEVICE_TRADING_FOCUS`, 90s inference timeout, chat off during RTH |
| Halim 1–3B local LM | ❌ Not recommended | `m2_16gb` tier in `halim/device.py` |
| Colab training | ✅ Off-device | Train toddler LoRA, sync zip to Mac |

From `scripts/halim_env.sh`:

```bash
# ≤12GB Mac: LoRA + 4bit base (~500MB) — merged safetensors OOM-kills serve under HANOON
HALIM_SERVE_PREFER_ADAPTER=true
HALIM_INFERENCE_TIMEOUT_SEC=90
DEVICE_TRADING_FOCUS=true
HALIM_LEARN_OFF_HOURS_ONLY=true
```

From `docs/HALIM_MAC_INFERENCE.md`:

> M2 8GB | ~0.5B @ 4-bit fits comfortably

### Path to eliminate API dependency on 8GB

**Step 1 — Enable native owned stack**

```bash
export HALIM_NATIVE=true
export COUNCIL_ENABLED=false
export HALIM_FORCE_LM=true
export HALIM_LM_BACKEND=mlx
export OWNED_BRAIN_DEVICE=m2_8gb
export HALIM_DEVICE=m2_8gb
```

**Step 2 — Ensure Halim serve is healthy**

```bash
./scripts/halim_install_lm.sh
./scripts/halim_start_toddler.sh   # if checkpoint not registered
curl -s http://127.0.0.1:8765/v1/status | python3 -m json.tool
# reasoning.backend should be "mlx"
```

**Step 3 — Accumulate gold through replay + paper sessions**

- Target: 350+ closed trades, 1200+ rows in `council_training_dataset.jsonl` / verdicts
- Run `./scripts/post_session_evolve.sh` after sessions
- Retrain toddler LoRA on Colab periodically; pull zip to Mac

**Step 4 — Monitor proxy accuracy**

```bash
PYTHONPATH=. python scripts/owned_brain_status.py
```

When proxy accuracy rises (55% → 62% → 70%), the system naturally relies more on local students even if council were enabled.

### What you gain vs what you lose

| Gain on 8GB zero-API | Loss vs cloud teacher |
|---------------------|----------------------|
| No Groq/Gemini costs or rate limits | Hard-case reasoning depth |
| Fully offline-capable trading sessions | Chart vision (Gemini) quality |
| Owned weights, portable, private | Copilot macro brief freshness |
| PPO + proxy + 0.5B Halim blend | Sub-200ms Halim latency (first reply ~10–15s cold) |
| Verdict gold still accumulates | 70B-class nuance on ambiguous spikes |

The project's own moat statement (`halim/docs/ARCHITECTURE.md`):

> **Moat:** your trade ledger, not parameter count.

Adult on 8GB means: **your specialized 0.5B + PPO + proxy trained on YOUR spikes**, not a general frontier model.

### Realistic assessment

| Question | Answer |
|----------|--------|
| Can I stop paying for Groq/Gemini? | **Yes**, with `HALIM_NATIVE=true` + `COUNCIL_ENABLED=false` + working Halim serve |
| Can I reach adult maturity stage? | **Yes**, through trades/replay — hardware independent |
| Will local Halim match cloud on every spike? | **No** — 0.5B on 8GB is a specialist, not a frontier model |
| Is 8GB enough for "proper adult" quality? | **Partially** — operational adult yes; reasoning quality ceiling is lower than 16GB+ with 1–3B |
| Best upgrade path? | Colab for training bursts (free/cheap) + keep 8GB Mac for inference; OR 16GB Mac for 1–3B MLX |

### Recommended 8GB strategy

1. **Paper/replay months** with council ON early (toddler/child) — build gold cheaply
2. **Train toddler LoRA on Colab** from your verdicts — sync to Mac
3. **Switch to native mode** once proxy accuracy > 60% and Halim serve stable
4. **Keep 2–4 API calls/day as polish** (adult default) OR hard-disable council entirely
5. **Off-hours only** for Halim LM retrain (`HALIM_LEARN_OFF_HOURS_ONLY=true`) — already default on 8GB
6. **Close IDE/memory hogs** during RTH (`HALIM_REMOVE_IDE_HOGS=true`)

### Bottom line

On **MacBook Air M2 8GB**, Halim can become an **operational adult** — owning the full entry pipeline without cloud API dependency — but "proper adult" in the sense of **matching Groq/Gemini reasoning quality on ambiguous spikes** requires either more training data + time on the 0.5B specialist, or a hardware step-up to 16GB for a 1–3B local model. The codebase is designed for exactly your machine today; the limitation is model capacity and RAM headroom, not missing architecture.

---

## Related Documents

| Doc | Topic |
|-----|-------|
| [VISION_SMART_STACK.md](VISION_SMART_STACK.md) | Life Engine vision & maturity roadmap |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Core design decisions (legacy PPO trader) |
| [LAUNCH_GUIDE.md](LAUNCH_GUIDE.md) | Setup, IB, Telegram, deployment |
| [HALIM.md](HALIM.md) | Halim overview & growth path |
| [HALIM_MAC_INFERENCE.md](HALIM_MAC_INFERENCE.md) | MLX on Apple Silicon |
| [OWNED_BRAIN.md](OWNED_BRAIN.md) | Teacher → students flywheel |
| [WAR_ACCOUNT_LIVE.md](WAR_ACCOUNT_LIVE.md) | War account setup |
| [TRAINING_GUIDE.md](TRAINING_GUIDE.md) | PPO training & tuning |
| [SNIPER_LOCK_ARCHITECTURE.md](SNIPER_LOCK_ARCHITECTURE.md) | Scout + heartbeat design |

---

*One engine. AI everywhere it helps. RAM live. Learn every spike.*
