# HANOON Cleanup & Organization — Complete Session Report (2026-06-30)

This document is the **single authoritative summary** of everything cleaned, fixed, reorganized, and documented during the 2026-06-30 engineering session. It complements (does not replace) per-fix entries in [`ENGINEERING_FIX_LOG.md`](ENGINEERING_FIX_LOG.md).

---

## Executive summary

| Goal | Outcome |
|------|---------|
| **IB truth** for fills & P&L | Entries/exits require IB confirmation; orphan positions no longer inflate profits |
| **No git during RTH** | Learning pushes deferred by default; flush on shutdown |
| **Pre-market entries** | Extended-hours LIMIT instead of stalled MARKET `PreSubmitted` |
| **Learning flywheel** | Outcome teacher, holdout eval, Halim promotion gate, balanced sampling |
| **Code hygiene** | Extracted modules, pytest harness, runtime files untracked, docs updated |
| **Structural split (partial)** | `entry_pipeline`, `position_sync`, `git_sync_defer`, `account_view` extracted from monoliths |

**Restart after pulling:** `./scripts/stop_hanoon.sh && ./start.sh`

---

## Table of contents

1. [Live trading bugs fixed](#1-live-trading-bugs-fixed)
2. [How each bug was cleaned](#2-how-each-bug-was-cleaned)
3. [Organization & hygiene pass](#3-organization--hygiene-pass)
4. [Module extractions](#4-module-extractions)
5. [Git & repository hygiene](#5-git--repository-hygiene)
6. [Tests added](#6-tests-added)
7. [Documentation created/updated](#7-documentation-createdupdated)
8. [Environment defaults (after cleanup)](#8-environment-defaults-after-cleanup)
9. [What was intentionally deferred](#9-what-was-intentionally-deferred)
10. [Verification checklist](#10-verification-checklist)
11. [File index](#11-file-index)

---

## 1. Live trading bugs fixed

### 1.1 Phantom P&L (~$100k internal vs IB losses)

**Symptom:** Bot logs and internal NAV showed large unrealized profits while the IB paper account showed losses.

**Impact:** Wrong sizing confidence, misleading dashboards, exits booked on quote estimates.

### 1.2 Pre-market PreSubmitted timeouts (GVH)

**Symptom:** Entry parent orders stuck in `PreSubmitted` ~07:25 ET; recovery retried bare MARKET and timed out again.

**Impact:** Missed entries, wasted poll cycles, capital discipline noise.

### 1.3 Git push during live session

**Symptom:** `session_batch` / `git pull --rebase` activity during RTH; `start_hanoon.sh` could auto-start git_sync daemon.

**Impact:** Disk I/O, subprocess churn, potential lock contention during spikes.

### 1.4 Halim echo parse crash

**Symptom:** Token `0.54.` crashed confidence parser.

**Impact:** Halim entry line dropped on otherwise valid LM output.

### 1.5 CUPR IB Error 201 (closing-only)

**Symptom:** Entry attempts on closing-only restrictions.

**Impact:** Wasted orders; now handled via structural reject learning path (existing policy).

### 1.6 Learning flywheel gaps

**Symptom:** Weak outcome labels, no holdout gate, Halim promoted without evidence.

**Impact:** Training drift, overfitting to proxy signals.

### 1.7 War ledger replay contamination

**Symptom:** Overnight replay incremented live `round_trips_today`.

**Impact:** Live entries blocked after replay sessions.

---

## 2. How each bug was cleaned

### 2.1 Phantom P&L → IB-grounded accounting

| Step | What we did |
|------|-------------|
| **Root cause** | Orphan IB holdings counted as new fills; quote-based exit P&L; `bot_nav` shown as truth |
| **Detection** | `confirm_entry_fill` now uses position **delta** capped at `order_shares × 1.25`, baseline captured at submit |
| **Entry** | `_entry_poll_states` stores `ib_pos_baseline`; only IB-confirmed fills open slots |
| **Exit** | `IB_FILL_STRICT=true` blocks quote-force P&L in reconciler |
| **Display** | `core/account_view.py` — `display_equity()`, `day_pnl()` from IB NetLiquidation |
| **Sync** | `core/position_sync.py` caps slot shares to session size; refreshes entry from `avgCost` |

**Key files:** `core/fill_tracker.py`, `core/fill_reconciler.py`, `core/scalper_runner.py`, `core/position_intel.py`, `core/account_evaluator.py`, `core/account_view.py`, `core/position_sync.py`

### 2.2 Pre-market PreSubmitted → extended-hours LIMIT

| Step | What we did |
|------|-------------|
| **Root cause** | Paper MARKET parent orders stall outside RTH |
| **Prevention** | `entry_price_mode_for_session()` in `core/entry_pipeline.py` — LIMIT in extended hours |
| **Recovery** | `stuck_entry_limit_px()` — stuck retry never re-submits bare MARKET ext-hours |
| **Config** | `ENTRY_STUCK_MAX_RETRIES` tuned in `core/config.py` |

**Key files:** `core/entry_pipeline.py`, `core/scalper_runner.py`, `core/config.py`

### 2.3 Git during session → defer until shutdown

| Step | What we did |
|------|-------------|
| **Root cause** | `GIT_PUSH_DURING_SESSION` default true; `force=True` bypassed defer |
| **Policy module** | `core/git_sync_defer.py` — replay batching, session push gate, checkpoint queue |
| **Default** | `GIT_PUSH_DURING_SESSION=false` in `BotConfig` |
| **Launcher** | `START_GIT_SYNC_WITH_HANOON=false`, `LEARNING_PUSH_ON_TRADE=false` |
| **Shutdown** | `stop_hanoon.sh` flushes queued reasons via `pre_shutdown` |

**Key files:** `core/git_sync_defer.py`, `core/git_sync.py`, `core/config.py`, `scripts/start_hanoon.sh`

### 2.4 Halim parse crash → tolerant float lexer

| Step | What we did |
|------|-------------|
| **Root cause** | Trailing dot in `0.54.` broke float parse |
| **Fix** | Strip trailing punctuation in `core/halim_entry_line.py` |

### 2.5 Learning flywheel → gates + curriculum

| Step | What we did |
|------|-------------|
| Outcome teacher | Realized P&L labels in `core/ppo_teacher_training.py` |
| Holdout eval | Proxy holdout in hybrid distiller |
| Brain gates | Maturity ladder enforcement in `core/brain_maturity.py` |
| Halim promotion | Evidence gate in `core/halim_promotion_gate.py` |
| Sampling | Balanced positive/negative in training pipeline |

### 2.6 War replay isolation

| Step | What we did |
|------|-------------|
| **Fix** | `is_replay_session()` in `core/war_account.py`; replay never writes live ledger |
| **Reset** | `reset_live_war_session()` for manual recovery |

---

## 3. Organization & hygiene pass

### 3.1 Config alignment

- `SMART_STACK`, `RAM_LIVE_ONLY`, `REQUIRE_IB_FILL_SYNC`, `IB_FILL_STRICT` in `BotConfig`
- `main.py --port` inherits `BotConfig.IB_PORT` (4002) when omitted
- `core/capital_discipline.py` — fixed `PPO_LEAD_WHILE_COUNCIL_PENDING` getattr default

### 3.2 Launcher deduplication

`scripts/start_hanoon.sh`:
- Removed duplicate `PPO_LEAD_WHILE_COUNCIL_PENDING` export
- Removed duplicate `TRAILING_PROFIT_GIVEBACK_PCT`
- Explicit git/IB sync env block documented in `docs/OPS.md`

### 3.3 Legacy deprecation

- `archive/replay_live_runner.py` → use `replay_scalper_runner`
- Production path: `main.py --mode scalper` only (`docs/OPS.md`)

### 3.4 Runtime state local-only

`.gitignore` expanded for:
- `models/*.jsonl` journals
- `halim/data/actions/action_log.jsonl`
- Session state JSON (`halim_runtime_state`, `war_account_state`, etc.)

~40 tracked runtime files removed via `git rm --cached` — they remain on disk but are no longer committed.

---

## 4. Module extractions

Monolith reduction without spawning parallel decision systems (Smart Stack rule: one hull — `scalper_runner`).

```
scalper_runner.py (hull)
    ├── entry_pipeline.py     IB entry fill + price mode + poll state
    ├── position_sync.py      Multi-slot IB share/entry sync
    ├── account_view.py       Display equity / day P&L
    └── (delegates) fill_tracker.confirm_entry_fill

git_sync.py
    └── git_sync_defer.py     Session push policy + checkpoint queue
```

| Module | Responsibility | Extracted from |
|--------|----------------|--------------|
| `core/entry_pipeline.py` | `confirm_entry_fill_from_ib`, `new_entry_poll_state`, `entry_price_mode_for_session`, `stuck_entry_limit_px` | `scalper_runner.py` |
| `core/position_sync.py` | `repair_slot_entry_price`, `sync_position_slots_from_ib` | `scalper_runner.py` |
| `core/account_view.py` | IB-grounded equity / day P&L helpers | scattered display logic |
| `core/git_sync_defer.py` | Defer/batch/shutdown git push policy | `git_sync.py` |

**Wiring:** `scalper_runner` imports and delegates; behavior unchanged, surface area smaller.

---

## 5. Git & repository hygiene

### Before

- Runtime jsonl committed on every trade
- `git_sync` could push/pull during RTH
- Auto-sync commits mixed code + live session state

### After

| Practice | Implementation |
|----------|----------------|
| No session pushes | `GIT_PUSH_DURING_SESSION=false` |
| Queue until stop | `_queue_batched_checkpoint` → flush on `pre_shutdown` |
| No background daemon | `START_GIT_SYNC_WITH_HANOON=false` |
| Clean history | Runtime artifacts in `.gitignore` |
| Fix journal enforced | Pre-commit hook requires `ENGINEERING_FIX_LOG.md` entry |

See [`docs/GIT_SYNC.md`](GIT_SYNC.md) for operator reference.

---

## 6. Tests added

| File | Covers |
|------|--------|
| `tests/test_fill_tracker.py` | Position delta cap, baseline fill detection |
| `tests/test_git_sync_defer.py` | Session push off by default; shutdown flush allowed |
| `tests/test_account_view.py` | IB-first display helpers |

**Harness:** `pytest.ini`, `requirements-dev.txt`

```bash
venv/bin/pip install -r requirements-dev.txt   # once
venv/bin/pytest tests/ -q                      # 9 passed
```

---

## 7. Documentation created/updated

| Document | Purpose |
|----------|---------|
| **`docs/CLEANUP_AND_ORGANIZATION_2026-06-30.md`** | This report |
| `docs/OPS.md` | Canonical start/stop, env defaults, accounting truth |
| `docs/ARCHITECTURE.md` | Updated module map |
| `docs/GIT_SYNC.md` | Defer policy, shutdown flush |
| `models/README.md` | What belongs in git vs local-only |
| `docs/ENGINEERING_FIX_LOG.md` | Per-fix journal (mandatory for loop changes) |
| `docs/BRAIN_DEVELOPMENT_LOG.md` | One-liner brain milestones |

---

## 8. Environment defaults (after cleanup)

```bash
# Accounting
REQUIRE_IB_FILL_SYNC=true
IB_FILL_STRICT=true
IB_FILL_FORCE_SEC=120

# Git (live session)
GIT_PUSH_DURING_SESSION=false
START_GIT_SYNC_WITH_HANOON=false
LEARNING_PUSH_ON_TRADE=false

# Smart stack
SMART_STACK=true
RAM_LIVE_ONLY=true

# IB
IB_PORT=4002   # Gateway paper (BotConfig default)
```

Override only when you explicitly want mid-session learning pushes:

```bash
GIT_PUSH_DURING_SESSION=true   # not recommended during RTH
```

---

## 9. What was intentionally deferred

These are **documented future work**, not forgotten tasks:

| Item | Reason |
|------|--------|
| Full `scalper_runner.py` split (~8.8k lines) | Risky during live season; incremental extraction preferred |
| Full `ai_commander.py` split (~3.2k lines) | Same |
| `git_sync.py` beyond defer module | Push/restore paths stay centralized |
| IB Error 161 cancel-on-exit tightening | Low frequency; needs live repro |
| `position_loop.py` for deferred exits | Small methods; low ROI vs hull stability |

**Rule preserved:** No parallel entry bots outside `ai_commander` pipeline; extend organs, don't spawn boats (`docs/VISION_SMART_STACK.md`).

---

## 10. Verification checklist

### Accounting

- [ ] Entry log: `✅ IB entry confirmed TICKER: Nsh @ $X (order_status|position_delta|exec_cache)`
- [ ] Exit log: `📕 EXIT TICKER (IB fill):` — not `est. fill` with strict on
- [ ] Day P&L matches IB account change, not inflated unrealized

### Pre-market entries

- [ ] Log shows `ext_hours_limit_*` or `LIMIT@$…`, not bare `MARKET` outside RTH
- [ ] Stuck recovery: `Stuck-entry retry: limit@…`

### Git

- [ ] No `session_batch` / `pull --rebase` during RTH with defaults
- [ ] `stop_hanoon.sh` emits one consolidated learning push

### Tests

```bash
venv/bin/pytest tests/ -q
grep -c PPO_LEAD_WHILE scripts/start_hanoon.sh   # expect 1
```

### Halim

- [ ] No parse crash on trailing-dot confidence tokens
- [ ] `Halim entry fresh` / `+halim+` in replay/live logs when LM healthy

---

## 11. File index

### New files

```
core/account_view.py
core/entry_pipeline.py
core/position_sync.py
core/git_sync_defer.py
tests/test_fill_tracker.py
tests/test_git_sync_defer.py
tests/test_account_view.py
pytest.ini
requirements-dev.txt
docs/OPS.md
docs/CLEANUP_AND_ORGANIZATION_2026-06-30.md
models/README.md
```

### Heavily modified

```
core/scalper_runner.py
core/fill_tracker.py
core/fill_reconciler.py
core/git_sync.py
core/config.py
core/halim_entry_line.py
core/position_intel.py
core/account_evaluator.py
core/ppo_teacher_training.py
core/brain_maturity.py
scripts/start_hanoon.sh
main.py
.gitignore
docs/ENGINEERING_FIX_LOG.md
docs/ARCHITECTURE.md
docs/GIT_SYNC.md
```

### Deprecated

```
archive/replay_live_runner.py  → use replay_scalper_runner
```

---

## Session timeline (chronological)

1. Codebase understanding + learning flywheel recommendations
2. Live log triage (CUPR 201, Halim parse, GVH PreSubmitted)
3. Phantom P&L investigation → IB fill sync hardening
4. Git-during-RTH performance concern → defer policy
5. Organization audit → hygiene pass
6. Push + polish (tests, docs, gitignore, module extractions)
7. Final extractions (`git_sync_defer`, `position_sync`, entry poll wiring) + this document

---

*Generated 2026-06-30. For per-fix root-cause detail, see [`ENGINEERING_FIX_LOG.md`](ENGINEERING_FIX_LOG.md) sections dated 2026-06-30.*
