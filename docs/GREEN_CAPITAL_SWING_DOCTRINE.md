# Green Doctrine, Capital Phases & Swing — Complete Summary

**Date:** 2026-07-01  
**Hub code:** `core/green_trade_doctrine.py`, `core/swing_doctrine.py`, `core/capital_phase.py`  
**Related logs:** [ENGINEERING_FIX_LOG.md](ENGINEERING_FIX_LOG.md) · [BRAIN_DEVELOPMENT_LOG.md](BRAIN_DEVELOPMENT_LOG.md)

---

## Mission

One hull (`scalper_runner`), IB Gateway as single source of truth, AI-driven decisions everywhere — not static-only gates. **Same tactics** for war ($1k), pre-war full balance, and post-war full balance; **only sizing differs**. Swing uses the **same green principles** but matures slowly from IB trip learning.

---

## Architecture at a Glance

```
IB Truth (positions, fills, PnL, cash)
        │
        ▼
capital_phase ──► premarket_full | rth_war | rth_full | off
        │
        ├── Scalp pipeline (spike loop → ai_commander → entry/exit executor)
        │     └── green_trade_doctrine (entry + dynamic exit)
        │
        └── Swing pipeline (swing_intel → swing_executor → IB GTC brackets)
              └── swing_doctrine (maturity-scaled green entry/exit)
```

---

## Capital phases (sizing only)

| Phase | When | Scalp sizing | War ledger | Swing live IB |
|-------|------|--------------|------------|---------------|
| `premarket_full` | Pre-RTH | Full IB paper | Off | Yes |
| `rth_war` | RTH war window | ~$1k war pool | On (scalp) | No |
| `rth_full` | After war cap / continued RTH | Full IB | Off | Yes |
| `off` | Closed | — | — | — |

Order tags: `HN|horizon|capital_phase|pipeline` via `core/horizon_tags.py`.

---

## Scalp: unified green doctrine

### Entry (mandatory when `GREEN_DOCTRINE_ENTRY=true`)

Dynamic alignment required — not a single static threshold:

- **Uptrend** (`only_uptrend`)
- **Green bar** (close ≥ open and ≥ prior close)
- **Prediction up** (`pred_1bar`, micro momentum)
- **AI vote** (PPO buy + confidence OR Halim enter)
- **Profit probability** from `entry_quality` (commander/capital-discipline floors)

Wired in: `ai_commander_verdict`, `scalper_entry_executor`, `scalper_spike_loop`, `war_entry_gates` (all phases when unified).

### Exit (dynamic book / ride / cut)

| Signal | Action |
|--------|--------|
| `pred_3bar` + `profit_run` strong, low slippage | **Ride** multi-bar (up to `GREEN_MULTIBAR_MAX_BARS`) |
| Slippage critical (≥78%) | **Book profit** or **cut loss** immediately |
| Slippage + fade (≥62% in profit) | Early profit book |
| Slippage + loss_pressure (≥52% in loss) | Early loss cut |
| AI/PPO sell, stall, giveback from peak | Book profit |
| Green profit lock | Mechanical backup |

Functions: `assess_dynamic_exit`, `predict_exit_slippage`, `assess_multi_bar_ride`.

Wired in: `scalper_exit_executor` (profit lock, profit hunt defer on ride, loss cut, early exit).

---

## Swing: same doctrine, slow maturity

Swing is **not** a separate bot. It reuses green logic with:

- **Timeframes:** 1h / 4h / 1d (via `swing_intel` + `build_swing_micro`)
- **Hold unit:** days (not minutes)
- **Maturity ramp:** `brain_maturity` stage + IB swing trip count

### Maturity modes (`swing_maturity_level`)

| Level | Mode | Entry | Exit |
|-------|------|-------|------|
| 0.0–0.35 | **advisory** | Log green gaps; intel score still gates | Only slippage-critical exits |
| 0.35–0.72 | **partial** | Uptrend + composite score floor | Slippage + giveback exits |
| 0.72+ | **mandatory** | Full green entry alignment | Full dynamic exit (ride / book / cut) |

Training feeds maturity: `models/swing_ib_trips.jsonl`, `swing_policy.json`, off-hours `swing_web_learn` + `train_swing_policy`.

### Swing files

| File | Role |
|------|------|
| `core/swing_doctrine.py` | Maturity profile, entry/exit assessment |
| `core/swing_intel.py` | Full analysis + doctrine gate on verdict |
| `core/swing_executor.py` | IB entries, monitor + doctrine exit |
| `core/swing_learning.py` | IB close labels, multi-day `hold_days` |
| `core/swing_train.py` | Policy from real IB outcomes |

---

## IB-first consolidation (same session)

- Positions, PnL, cash from IB snapshot — not local fiction
- Macro from IB hub; Yahoo only if `MACRO_YAHOO_FALLBACK=true`
- Account evaluator, fill tracker, war sync aligned to IB Truth

---

## Environment variables (defaults in `scripts/start_hanoon.sh`)

### Core doctrine
```
GREEN_DOCTRINE_UNIFIED=true
GREEN_DOCTRINE_ENTRY=true
GREEN_DOCTRINE_EXIT=true
```

### Scalp exit tuning
```
GREEN_MULTIBAR_RIDE=true
GREEN_SLIPPAGE_EXIT=true
GREEN_MULTIBAR_MAX_BARS=5
GREEN_SLIPPAGE_EXIT_PROFIT=0.62
GREEN_SLIPPAGE_EXIT_LOSS=0.52
GREEN_SLIPPAGE_EXIT_ANY=0.78
```

### Capital phases & swing
```
CAPITAL_PHASES_ENABLED=true
SWING_IB_LIVE=true
SWING_INTEL_ENABLED=true
SWING_DOCTRINE_ENABLED=true
SWING_MULTIBAR_MAX_DAYS=12
SWING_DOCTRINE_TRIP_MATURE=24
```

### IB truth
```
IB_HUB_ENABLED=true
REQUIRE_IB_FILL_SYNC=true
WAR_IB_SYNC=true
```

---

## What we did (chronological)

1. **IB consolidation** — single truth hub for positions, PnL, macro  
2. **Capital phases** — pre-war / war / post-war sizing; real IB swing with HN tags  
3. **Swing intel** — multi-TF, IB fundamentals/news, web learn, policy training  
4. **Unified green doctrine (scalp)** — same war tactics on full balance; mandatory green entry/exit  
5. **Multi-bar ride + slippage exit (scalp)** — hold when pred_3bar strong; book/cut on slippage  
6. **Swing doctrine** — same principles, maturity-scaled learning from IB trips  

---

## Verify

```bash
python3 -m py_compile core/green_trade_doctrine.py core/swing_doctrine.py core/swing_executor.py
python3 -m pytest tests/test_green_trade_doctrine.py tests/test_capital_phase.py tests/test_swing_doctrine.py -q
```

---

## Never bypass (Smart Stack)

- No `ppo_hold_skip` before Halim/council  
- No hard-block gates without `decide_entry`  
- Always `_emit_spike_verdict` on finalize  
- Fix journal required for trading-loop changes → `docs/ENGINEERING_FIX_LOG.md`

---

## Next maturity (automatic)

As `brain_maturity` advances and `swing_ib_trips.jsonl` grows:

- Swing entry gates tighten toward full green mandatory  
- Slippage exit thresholds tighten  
- `swing_policy.json` min_score calibrates from win/loss score distributions  
- PPO swing training prefers IB trips over shadow-only labels  

No manual flip — env caps (`SWING_DOCTRINE_FULL_AT`, `SWING_DOCTRINE_TRIP_MATURE`) tune the ramp.

---

## IB Truth startup checklist

On every HANOON boot (after first IB refresh):

```
──────────────────────────────────────────────────────────────
  IB TRUTH CHECKLIST — LIVE FROM GATEWAY ✓
  Status: READY | server=... scope=rth
  NetLiq $... | PnL session $... | UPL $...
  ✓ connected: yes
  ✓ snapshot_fresh: age=1.2s (max 30s)
  ✓ account_values: NetLiq=$...
  ✓ capital_phase: rth_war sizing=war
  ...
──────────────────────────────────────────────────────────────
  ⚡ IB Truth ready — trading from Gateway snapshot (light/fast path)
```

If Gateway is not ready within `IB_TRUTH_STARTUP_WAIT_SEC` (20s), startup **halts** when `IB_TRUTH_STARTUP_BLOCK=true`.

During the loop, entries pause if snapshot age exceeds `IB_TRUTH_RUNTIME_MAX_AGE_SEC` (90s) until the hub refreshes.
