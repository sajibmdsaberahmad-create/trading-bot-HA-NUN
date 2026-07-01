# Engineering fix log

**Purpose:** Track every intentional code/config change with enough detail to debug regressions, avoid duplicate fixes, and know what to verify. Append new entries at the top (newest first).

**Related:** [BRAIN_DEVELOPMENT_LOG.md](BRAIN_DEVELOPMENT_LOG.md) (runtime brain events) · [VISION_SMART_STACK.md](VISION_SMART_STACK.md) (architecture)

**How to add an entry:** Copy the template at the bottom, fill every section, link files and env vars explicitly.

**Enforced:** `scripts/git-hooks/pre-commit` blocks commits that touch `core/`, `halim/halim/`, `scripts/*.sh`, or `.cursor/rules/` without a new dated section here. Install: `./scripts/install_git_hooks.sh`. Cursor `afterFileEdit` hook reminds agents. Emergency bypass: `SKIP_FIX_JOURNAL=1` (document ASAP).

---

### Verify
Colab Cell 3: `bnb_4bit_compute_dtype: torch.float16` with `fp16=True` — no grad scaler crash.

---

## 2026-07-01 — Deferred audit complete: ib_async shim, UTC, git untrack

### Problem
Remaining audit items were docs-only: unmaintained ib_insync, datetime.utcnow deprecations, runtime journals in git.

### Fix
| File | Change |
|------|--------|
| `core/ib_client.py` | `IB_CLIENT_BACKEND=ib_insync` (default) or `ib_async` |
| `core/time_utils.py` | Timezone-aware UTC; bulk replace in core modules |
| `tests/test_ib_client.py` | Backend shim tests (157 total passing) |
| `.gitignore` + `git rm --cached` | Untrack runtime journals, IB locks, connectivity cache |
| `docs/IB_ASYNC_MIGRATION.md` | Phase 2 shim complete |

### Env
`IB_CLIENT_BACKEND=ib_async` to try maintained fork

### Verify
```bash
venv/bin/python -m pytest tests/ -q
```

---

## 2026-07-01 — Codebase health: tests, CI, maturity ladder, TF optional

### Problem
Audit found 6 failing tests (PPO wheel + capital-phase drift), no CI, runtime jsonl noise in git, monolithic scalper guidelines, duplicate council dataset paths, TensorFlow required for live scalper.

### Fix
| File | Change |
|------|--------|
| `tests/conftest.py` | `ppo_wheel_off` + `war_ledger_on` fixtures |
| `tests/test_*` | Fix ai-sure / war / ghost-exit tests; add smoke, round-trip, maturity, guidelines tests |
| `.github/workflows/tests.yml` | pytest on push/PR (Python 3.11) |
| `.gitignore` | IB locks, journals, connectivity cache |
| `core/scalper_guidelines.py` | Extract from `scalper_runner.py` |
| `core/training_dataset_paths.py` | Canonical `models/council_training_dataset.jsonl` |
| `core/brain_maturity.py` | Stage confidence/prob floors; `maturity_ai_sure_entry`; `BRAIN_MATURITY_AI_SURE_AUTO` |
| `core/smart_stack.py` | ai-sure respects maturity auto flag |
| `requirements.txt` | Drop TF (moved to `requirements-legacy.txt`) |
| `requirements-lock.txt` | Pinned core deps |
| `core/startup_checks.py` | TF optional unless `require_tensorflow=True` |
| `docs/ENV_PROFILES.md` | Profile precedence matrix |
| `docs/IB_ASYNC_MIGRATION.md` | ib_insync → ib_async plan |

### Env
`BRAIN_MATURITY_AI_SURE_AUTO=false` (default) · `PPO_WHEEL_PROFILE_LOCK=true`

### Verify
```bash
venv/bin/python -m pytest tests/ -q
# GitHub Actions: tests workflow on push
```

---


### Problem
User wanted one profile: lock profit in flight + capture learning gold + off-hours PPO/brain upgrade — without hand-tuning dozens of env vars.

### Fix
| File | Change |
|------|--------|
| `scripts/hanoon_profit_learn_env.sh` | **New** — green lock, profit hunt, bounded `LEARNING_LIVE_MICRO_PPO`, defer heavy RTH, teacher/off-hours train |
| `scripts/start_hanoon.sh` | Source profile after `ppo_wheel_env.sh`; banner `HANOON_PROFIT_LEARN_PROFILE` (default true) |
| `core/config.py` | `STAGNATION_EXIT_SEC` + stagnation knobs read from env (profile sets 75s) |

### Env
`HANOON_PROFIT_LEARN_PROFILE=true` (default) · disable with `false`

### Verify
```bash
./scripts/start_hanoon.sh   # banner: Profit+Learn profile ON + micro_ppo=true stagnation=75s
./stop.sh                   # graceful → flush_pending_learning + owned_brain_evolution
./scripts/post_session_evolve.sh   # manual off-hours upgrade
```

---

### Problem
Routine Telegram alerts were rewritten by Halim/Ollama — slow, often nonsense (`• Never canned templates`), or raw JSON fragments (`_pnl": 0.0...`). `notifier.info()` double-composed already-formatted messages.

### Root cause
`HALIM_TELEGRAM_TRADE_NOTIFY` + `compose_outbound` Halim/Ollama paths on trade/session events; no sanitizer on outbound text.

### Fix
| File | Change |
|------|--------|
| `core/council_budget.py` | `telegram_structured_only()`; all routine events template-only |
| `core/ai_notifier.py` | Structured-only short-circuit; `sanitize_telegram_message()`; rth_open template; broadcast ops no longer bypass via `copilot=True` |
| `core/notify.py` | Skip compose when structured-only; sanitize before send |
| `core/halim_companion.py` | Session ping uses IB structured line, not generative Halim |
| `core/account_evaluator.py` | Skip AI compose on account briefs when structured-only |
| `scripts/start_hanoon.sh` | `TELEGRAM_STRUCTURED_ONLY=true`, `HALIM_COMPANION_PING=false` |
| `tests/test_telegram_notify.py` | Sanitizer + compose unit tests |

### Env
`TELEGRAM_STRUCTURED_ONLY=true` (default) · copilot `/help` `/positions` still use LLM when enabled

### Verify
```bash
python3 -m unittest tests.test_telegram_notify -q
# Trade exit → ⚡ EARLY EXIT │ TICKER (instant, no LLM)
# send_smart_telegram bypasses Halim compose when structured-only
```

---

## 2026-07-01 — War only at RTH open; fast trade Telegram; ghost slot after exit

### Problem
Premarket exits logged `⚔️ WAR EXIT` and debited war pool (should be full IB / data phase). Telegram hung on Halim LLM (`NOTIFY │ • Never canned templates`) blocking perception of progress. GSUN kept spamming `TICK LOSS EXIT` after IB fill because slot wasn't cleared when another position finalized.

### Root cause
`record_exit()` gated on `war_account_enabled` not `war_ledger_applies()` (RTH war phase only). `HALIM_TELEGRAM_TRADE_NOTIFY=true` ran slow Halim compose on every trade. `_clear_closed_position_state` wiped global bracket state when one of multiple positions closed.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `record_exit` + `war_ledger_applies` RTH-only fallback; `war_mode_display` / `sizing_mode` in context |
| `core/scalper_exit_executor.py` | Fast finalize on confirmed fill; multi-position safe clear; tick exit skips flat IB |
| `core/scalper_runner.py` | `_deployable_cash` uses `uses_war_sizing` |
| `core/paper_mode.py` | Equity from war pool only in RTH war phase |
| `core/ai_notifier.py` | `TELEGRAM_FAST_TRADE_NOTIFY` — structured trade alerts, skip Halim LLM |
| `core/scalper_session.py` | RTH open rolls war account + logs capital phase |
| `scripts/start_hanoon.sh` | `HALIM_TELEGRAM_TRADE_NOTIFY=false`, `TELEGRAM_FAST_TRADE_NOTIFY=true` |

### Env
`CAPITAL_PHASES_ENABLED=true` · `TELEGRAM_FAST_TRADE_NOTIFY=true` · `HALIM_TELEGRAM_TRADE_NOTIFY=false`

### Verify
```bash
python3 -m unittest tests.test_capital_phase -q
# Premarket exit: no ⚔️ WAR EXIT line; capital_phase=premarket_full
# Trade close Telegram: instant structured message, no Halim stall
# Multi-position: closing INTC does not break GSUN monitor
```

---

## 2026-07-01 — Exit pipeline deep fix (pending closes, stuck orders, limits)

### Problem
Deep audit found multiple exit-path bugs beyond premarket MARKET stall: broken `_pending_closes` dedup (keys `TICKER:ts` vs bare ticker check), retry flatten not updating `pending.flatten_trade`, zombie pending closes when IB flat but fill not reconciled, 90s `exiting` guard allowing duplicate flattens, entry-abort/commander exits bypassing reconciliation, RTH thin-book MARKET SELL asymmetry, orphan short bare MARKET BUY outside RTH.

### Root cause
Exit pipeline evolved in pieces; reconcile keys/retries were inconsistent with entry stuck-order handling and `entry_pipeline` ext-hours limit doctrine.

### Fix
| File | Change |
|------|--------|
| `core/scalper_exit_executor.py` | `_has_pending_close`, stuck PreSubmitted cancel+resubmit, retry updates trade, zombie `finalize_flat_position_close`, exiting guard, commander pending close, accurate submit log |
| `core/fill_reconciler.py` | Exit `since_ts=pending.started_at`, `finalize_flat_position_close()`, `stuck_retries` |
| `core/entry_pipeline.py` | Thin-book + `should_use_extended_hours_orders` limit SELL; `cover_order_for_session` |
| `core/broker.py` | Orphan short limit cover; `_hn_order_mode` on trade |
| `core/scalper_entry_executor.py` | Abort/partial flatten → pending close + bid/ask limits |
| `tests/test_pending_close.py` | Thin-book limit + flat salvage tests |

### Env
`EXIT_STUCK_MAX_RETRIES=2`, `PENDING_SUBMIT_MAX_SEC=4`, existing `EXIT_FLATTEN_RETRY_SEC=30`

### Verify
```bash
python3 -m unittest tests.test_exit_flatten_ib tests.test_pending_close tests.test_fill_reconciler -q
# No duplicate flatten while pending close exists
# Stuck PreSubmitted → cancel + limit retry in log
# IB flat → 📕 EXIT (IB fill) or position_flat salvage
```

---

## 2026-07-01 — Premarket flatten: limit SELL (not bare MARKET)

### Problem
`MECH RISK EXIT: hard_stop` fired at ~07:56 ET (premarket). Logs showed `Flatten order submitted: SELL … (market) status=PreSubmitted` then `EXIT submitted — awaiting IB fill`, but IB app showed no fill/sold notification and positions (INTC, GSUN) unchanged.

### Root cause
`flatten_position()` always sent `MarketOrder` SELL. IB paper stalls parent MARKET orders in `PreSubmitted` outside RTH — same reason entries use limit-only in extended hours (`entry_pipeline.entry_price_mode_for_session`).

### Fix
| File | Change |
|------|--------|
| `core/entry_pipeline.py` | `flatten_exit_limit_px()`, `flatten_order_for_session()` — aggressive limit SELL |
| `core/broker.py` | Ext-hours/penny flatten uses limit; bid/ask snapshot; log limit price |
| `core/scalper_exit_executor.py` | Pass `last_price`/`bid`/`ask` on flatten + retry |
| `tests/test_exit_flatten_ib.py` | Limit pricing unit tests |

### Env
Uses existing `ENTRY_LIMIT_BUFFER_PCT` / `EXIT_LIMIT_BUFFER_PCT` / `PENNY_PRICE_THRESHOLD`.

### Verify
```bash
python3 -m unittest tests.test_exit_flatten_ib -q
# Premarket: hard_stop → log shows (limit_ext_hours_sell @ $…) not (market)
# IB: position clears + 📕 EXIT (IB fill) in HANOON.log
```

---

## 2026-07-01 — Exit flatten: don't clear slots until IB fill confirms

### Problem
Logs showed `EXIT submitted` / `Flatten order submitted` but IB paper still held T/INTC/GSUN (0 open orders). Bot cleared `_position_slots` and `shares=0` immediately after `placeOrder`, so monitoring stopped while positions remained.

### Root cause
`_exit_position()` zeroed local state before IB fill. `flatten_position()` did not `ib.sleep` or poll order status after submit. Rejected/pending orders were logged as success.

### Fix
| File | Change |
|------|--------|
| `core/fill_tracker.py` | `trade_order_status()`, `confirm_exit_fill()` |
| `core/broker.py` | Post-submit sleep, status log, urgent fill poll, reject warning |
| `core/scalper_exit_executor.py` | Keep slot until `_finalize_closed_trade`; `exiting` guard; retry stale flatten |
| `core/fill_reconciler.py` | `PendingClose.retry_attempted`, `ib_baseline_shares` |
| `tests/test_exit_flatten_ib.py` | Confirm/reject/pending unit tests |

### Env
`EXIT_FLATTEN_RETRY_SEC=30` (optional, default 30)

### Verify
```bash
python3 -m unittest tests.test_exit_flatten_ib -q
# Live: EXIT submitted shows status=id=; 📕 EXIT (IB fill) before slot clears; TWS positions match
```

---

## 2026-07-01 — Stagnation logs: "missing" → readable + PPO wheel mech path

### Problem
`COUNCIL stagnation T: … awaiting council (missing)` every 15s — "missing" is an internal code meaning Groq never rang (`stagnation_check` is nanny low-priority), not "waiting for answer".

### Fix
| File | Change |
|------|--------|
| `core/live_ai_pipeline.py` | `council_status_label()`; PPO wheel stagnation mech timeout; nanny-missing no longer blocks past limit |
| `core/ai_commander_exit.py` | Pass `cfg` to `merge_stagnation_decision` |
| `core/scalper_exit_executor.py` | Stagnation watch logs only on `pulse_verbose`; clearer exit line |
| `tests/test_ppo_wheel.py` | Stagnation wheel + label tests |

### Verify
```bash
python3 -m unittest tests.test_ppo_wheel.TestPpoWheelExecution -q
# After restart: 📊 Stagnation watch 45s/90s — not "awaiting council (missing)"
```

---

## 2026-07-01 — PPO wheel execution: PPO-only buy/sell, council advisory

### Problem
Docs said PPO owns live entry/exit after green, but runtime still bought via `halim:quality_flash` and sold on council Groq exit while PPO held. Logs said `Ollama exit` though backend is Groq/Gemini council.

### Root cause
`build_halim_local_entry()` quality_flash bypassed PPO; `merge_exit_decision()` allowed council-only exit; `_deliberate_exit_council()` blocked on `in_flight`; no exit mirror of `PPO_LEAD_WHILE_COUNCIL_PENDING`.

### Fix
| File | Change |
|------|--------|
| `core/ppo_wheel_profile.py` | `ppo_only_execution_enabled()`, `ppo_lead_exits_enabled()`, `council_execution_advisory_only()` |
| `scripts/ppo_wheel_env.sh` | Force `PPO_ONLY_EXECUTION`, `PPO_LEAD_EXITS`, `COUNCIL_EXECUTION_ADVISORY_ONLY` |
| `core/smart_stack.py` | PPO-only short-circuit in `build_halim_local_entry()` |
| `core/live_ai_pipeline.py` | PPO wheel paths in merge entry/exit; rename Ollama → council in reasons |
| `core/ai_commander_exit.py` | Pass `cfg` into `merge_exit_decision` |
| `core/scalper_exit_executor.py` | PPO-led exit/risk; clear advisory councils; mech loss exits |
| `tests/test_ppo_wheel.py` | Execution mode tests |
| `docs/PPO_WHEEL_ARCHITECTURE.md` | Execution ownership table |

### Env
`PPO_ONLY_EXECUTION=true`, `PPO_LEAD_EXITS=true`, `COUNCIL_EXECUTION_ADVISORY_ONLY=true`

### Verify
```bash
pytest tests/test_ppo_wheel.py -q
./scripts/stop_hanoon.sh && ./scripts/start_hanoon.sh
grep -E "ppo:wheel|PPO wheel profile|quality_flash|Ollama exit" logs/HANOON.log | tail -20
```

---

## 2026-07-01 — PPO wheel env lock: Halim await=0 not applying after restart

### Problem
After restart, logs still showed `Halim entry fresh … (await 4.5s)`. PPO wheel profile not active. Halim developer on shutdown raised `CONFIDENCE_THRESHOLD` to 0.71 despite wheel 0.58 target.

### Root cause
`halim_env.sh` exported `HALIM_ENTRY_AWAIT_SEC=4.5` before wheel block; `${VAR:-0}` preserves already-set env. Wheel block was not last before `main.py`. Self-improver/Halim dev could mutate locked params at shutdown.

### Fix
| File | Change |
|------|--------|
| `scripts/ppo_wheel_env.sh` | **New** — force exports, sourced last before launch |
| `scripts/start_hanoon.sh` | Source `ppo_wheel_env.sh`; print wheel banner |
| `scripts/halim_env.sh` | Skip await 4.5 when `PPO_WHEEL_PROFILE_LOCK` |
| `core/ppo_wheel_profile.py` | **New** — lock param list + startup banner |
| `core/param_bounds.py` | `is_runtime_blocked` includes wheel-locked params |
| `core/commander_learning.py` | Reject mutations on locked params |
| `core/self_improver.py` | Skip locked params in `_apply_adjustments` |
| `core/halim_entry_line.py` | Default await 0 (was 4.5) |
| `tests/test_ppo_wheel_profile.py` | Lock tests |

### Verify
```bash
./scripts/stop_hanoon.sh && ./scripts/start_hanoon.sh
grep "PPO wheel" logs/HANOON.log | tail -3
# expect: Halim await=0s — zero "await 4.5s" after session start time
```

---

## 2026-07-01 — PPO wheel: war advisory, deploy tiers, learn firewall, Halim async

### Problem
Stacked war/commander/lottery gates duplicated green doctrine. War blocked entries (`war:entry_veto`) instead of only sizing the $1k teaching ledger. Halim await clock-blocked PPO. PPO trained on every buffer row including unlabeled spikes. Green checked twice (spike + verdict).

### Root cause
Legacy sniper/war gates and commander lottery floors were wired as hard vetoes alongside green. Lottery was a gate stack, not a PPO deploy tier. No learn-approval filter on `ppo_reward_trainer`.

### Fix
| File | Change |
|------|--------|
| `core/war_entry_gates.py` | `WAR_ENTRY_ADVISORY_ONLY` — annotate, never veto |
| `core/smart_stack.py` | `apply_smart_war_entry` advisory path |
| `core/ppo_deploy_tiers.py` | **New** — normal/strong/lottery_bullet sizing |
| `core/learn_approval.py` | **New** — `LEARN_APPROVAL_REQUIRED` train firewall |
| `core/green_trade_doctrine.py` | `GREEN_VERDICT_RECHECK`; fix unified pipeline gate kwargs |
| `core/war_account.py` | Skip pipeline war veto when advisory |
| `core/scalper_entry_executor.py` | Tier sizing before war rescale |
| `core/ppo_reward_trainer.py` | Filter training records |
| `core/online_trainer.py` | Incremental train filter |
| `core/ai_commander_verdict.py` | Skip second green when `GREEN_VERDICT_RECHECK=false` |
| `scripts/start_hanoon.sh` | Wheel env block |
| `tests/test_ppo_wheel.py` | Unit tests |
| `docs/PPO_WHEEL_ARCHITECTURE.md` | Full architecture spec |

### Env vars
`WAR_ENTRY_ADVISORY_ONLY=true`, `PPO_DEPLOY_TIERS_ENABLED=true`, `LEARN_APPROVAL_REQUIRED=true`, `GREEN_VERDICT_RECHECK=false`, `HALIM_ENTRY_AWAIT_SEC=0`

### Verify
```bash
./scripts/stop_hanoon.sh && ./scripts/start_hanoon.sh
venv/bin/pytest tests/test_ppo_wheel.py -q
```
No `war:entry_veto` when advisory on; `deploy_tier` on strong setups; green veto still fires.

**Full spec:** [PPO_WHEEL_ARCHITECTURE.md](PPO_WHEEL_ARCHITECTURE.md)

---

## 2026-07-01 — PPO-led profile: unlimited entries + dedupe overlapping gates

### Problem
Hourly entry caps (2–5/hr) and stacked gates (strict profit prob, AI-SURE, commander lottery 80%, Halim soft veto) blocked learning via actions. Overlapped green doctrine checks.

### Fix
`scripts/start_hanoon.sh` — PPO-led coevolution profile block:
- `MAX_ENTRIES_PER_HOUR=0` (and war hourly caps 0) = unlimited
- `SMART_STACK_STRICT_PROFIT_PROB=false`, `SMART_STACK_AI_SURE_ENTRY=false`
- `COMMANDER_RUNTIME_ENABLED=false` (lottery floors off; green still gates)
- `HALIM_ENTRY_SOFT_VETO=false`, `HALIM_ENTRY_AWAIT_SEC=1.0`
- Single profit/conf floor ~58% aligned with green

### Verify
Restart; no `👁 hourly entry cap` logs; more spikes reach `decide_entry` when green passes.

**Full spec:** [PPO_LED_COEVOLUTION_PROFILE.md](PPO_LED_COEVOLUTION_PROFILE.md)

---

## 2026-07-01 — fill_tracker qualifyContractsAsync warning (council thread)

### Problem
`RuntimeWarning: coroutine 'IB.qualifyContractsAsync' was never awaited` during council risk checks (e.g. INTC hard_stop). `snapshot_market_price` called `ib.qualifyContracts` from contexts with a running asyncio loop.

### Root cause
`position_entry_price` → `snapshot_market_price` always qualified contracts live; ib_insync sync wrappers orphan coroutines off the main IB path.

### Fix
| File | Change |
|------|--------|
| `core/fill_tracker.py` | `_cached_market_price` from IB Truth + extended cache first; `_ib_blocking_calls_safe` gate; portfolio mark before qualify |
| `tests/test_position_entry_price.py` | Tests: truth cache path, skip IB under running loop |

### Verify
```bash
venv/bin/pytest tests/test_position_entry_price.py -q
# No qualifyContractsAsync RuntimeWarning on council risk lines
```

---

## 2026-07-01 — Deep IB alignment audit (all Telegram/report surfaces)

### Problem
Several user-facing paths still showed `bot_nav`, local `trades_today`, journal-summed P&L, or stream-calculated commander exit P&L outside the main `_notify_context` hub.

### Root cause
Two outbound pipelines: `send_smart_telegram` (IB-grounded) vs `format_outbound_message` / account eval / daily reports (partial local ctx).

### Fix
| File | Change |
|------|--------|
| `core/notify_ib_context.py` | `merge_ib_telegram_context()` for copilot partial ctx |
| `core/ai_telegram.py` | `format_outbound_message` merges IB before compose |
| `core/account_evaluator.py` | Snapshots + briefs IB-only; notify via `telegram_notify_context` |
| `core/daily_activity_report.py` | `/daily` account + session P&L from IB FIFO |
| `core/daily_self_evaluation.py` | EOD self-eval headlines IB NetLiq + session P&L |
| `core/system_status.py` | `/system` drops bot_nav; IB round-trips |
| `core/scalper_session.py` | Session close + daily push: IB round-trips, no bot_cash line |
| `core/halim_companion.py` | `live_snapshot` IB round-trips; no bot_nav fallback |
| `core/position_intel.py` | Risk report uses IB round-trip count |
| `core/scalper_exit_executor.py` | Commander `/exit` P&L from IB fill reconcile |
| `core/trading_copilot.py` | Copilot context IB round-trips |
| `core/telegram_listener.py` | Mood fallback labels IB round-trips |
| `tests/test_notify_ib_context.py` | `merge_ib_telegram_context` test |

### Verify
```bash
venv/bin/pytest tests/test_notify_ib_context.py tests/test_ib_grounding.py -q
# Telegram: /status /positions /risk /daily /system /mood — no bot_nav lines
# Commander /exit shows · IB fill tag when IB confirms
```

---

## 2026-07-01 — Telegram notifications IB-only (no local P&L/NAV)

### Problem
Telegram entry/exit/status alerts mixed `bot_nav`, stream prices, composer local `_session_pnl`, and `runner.trades_today` with IB data.

### Root cause
`_notify_context` included bot_cash, local deployed_pct, and `ai_notifier._enrich_context` overwrote `session_pnl` with composer accumulator. `position_intel` used stream `_live_price_for` when IB mark available.

### Fix
| File | Change |
|------|--------|
| `core/notify_ib_context.py` | **New** — `telegram_notify_context()` / `ib_telegram_account()` refresh IB Truth for all alerts |
| `core/scalper_runner.py` | `_notify_context` delegates to notify_ib_context |
| `core/ai_notifier.py` | enrich uses IB session P&L; templates label IB fills; `send_smart_telegram` re-grounds via runner |
| `core/telegram_listener.py` | `/status` ctx from `ib_telegram_account` |
| `core/position_intel.py` | Positions use IB mark + unrealized only when IB position exists |
| `core/scalper_entry_executor.py` | `event_type=trade_opened` |
| `core/scalper_exit_executor.py` | Exit ctx IB fill fields + `event_type` |
| `core/notify.py` | `trade_closed` fallback shows IB NetLiq + session P&L |
| `tests/test_notify_ib_context.py` | Unit tests |

### Verify
```bash
venv/bin/pytest tests/test_notify_ib_context.py tests/test_ib_grounding.py -q
# Telegram entry: "IB fill" + IB NetLiq + Session P&L from FIFO
# /status: no bot_nav line — NetLiq + Session P&L from IB
```

---

## 2026-07-01 — Strict green + fast PPO lead + full-day IB session P&L

### Problem
User rejected pre-market relaxations (commander floor skip, green_bar waiver). Wants green doctrine + PPO/AI on **all** sessions with **fast** entry decisions, not looser gates. Session P&L/Telegram should count **all** IB fills (pre-market included), not RTH-only $0 before 09:30.

### Root cause
Prior fix relaxed `premarket_full` gates. `PPO_LEAD_WHILE_COUNCIL_PENDING=false` and capital_discipline ignored env PPO lead unless strong spike. `IB_TRUTH_RTH_SESSION=true` excluded pre-market fills from FIFO session P&L.

### Fix
| File | Change |
|------|--------|
| `core/commander_runtime.py` | Revert — commander lottery floors apply in pre-market |
| `core/green_trade_doctrine.py` | Revert — full green_bar + ai_vote required every phase |
| `core/capital_discipline.py` | Honor `PPO_LEAD_WHILE_COUNCIL_PENDING` (green still gates submit) |
| `core/ib_truth.py` | Calendar scope prefers FIFO session P&L |
| `core/account_view.py` | Baseline uses `_ib_starting_balance` when RTH session off |
| `core/rth_session.py` | Telegram market_note for full-day IB scope |
| `scripts/start_hanoon.sh` | `PPO_LEAD=true`, `IB_TRUTH_RTH_SESSION=false`, `IB_TRUTH_RTH_FILLS_ONLY=false`, `HALIM_ENTRY_AWAIT_SEC=2.5` |
| `tests/test_ib_grounding.py` | Strict green + PPO lead + calendar FIFO tests |

### Env vars
```bash
PPO_LEAD_WHILE_COUNCIL_PENDING=true   # fast PPO while council logs; green required at submit
IB_TRUTH_RTH_SESSION=false            # FIFO since midnight ET
IB_TRUTH_RTH_FILLS_ONLY=false         # count pre/AH fills
HALIM_ENTRY_AWAIT_SEC=2.5
```

### Verify
```bash
venv/bin/pytest tests/test_ib_grounding.py -q
# Pre-market Telegram: non-zero ib_fifo_session_pnl after IB fills
# Spike path: still GREEN veto without green_bar; PPO can lead when council pending
```

---

## 2026-07-01 — IB-only pulse/notify + copilot Chinese discard + premarket entry floors

### Problem
1. Pre-market spikes vetoed at 80% profit_prob + green_bar/ai_vote — no IB entries despite live scanner.
2. `LIVE_PULSE` P&L from stream ticks, not IB portfolio mark/unrealized.
3. Copilot log showed Chinese text (`行动计划…`) under `[parse_fallback]` — looked like compromise; was Groq/Gemini non-JSON reply stuffed into narrative.
4. Telegram `trade_opened` fallback used stale `_latest_account_balance` not NetLiq snapshot.

### Root cause
`COMMANDER_LOTTERY_MIN_PROFIT_PROB=0.80` applied in `premarket_full` via `commander_entry_floors`. Green doctrine required `green_bar+ai_vote` on thin pre-market bars. Copilot `_parse_brief_json` used raw LLM text when JSON parse failed. Monitor path preferred stream over `ib_truth` snapshot.

### Fix
| File | Change |
|------|--------|
| `core/response_sanity.py` | **New** — CJK detection; discard non-English structured LLM output |
| `core/trading_copilot.py` | English-only prompt; heuristic on bad JSON; IB account/positions in session context |
| `core/commander_runtime.py` | Skip commander lottery floors in `premarket_full` |
| `core/green_trade_doctrine.py` | Relaxed green entry in pre-market (no green_bar/ai_vote gate) |
| `core/ib_truth.py` | `position_pulse()` — IB mark + unrealized for monitoring |
| `core/scalper_runner.py` | `_resolve_monitor_price` prefers IB Truth mark |
| `core/scalper_exit_executor.py` | LIVE_PULSE uses `position_pulse`; logs `IB` tag when grounded |
| `core/account_view.py` | `ib_equity` from snapshot NetLiq when fresh |
| `core/notify.py` | `trade_opened` fallback shows IB NetLiq |
| `tests/test_ib_grounding.py` | Copilot CJK, premarket floors, green relax |

### Env vars
Unchanged — behavior is phase-aware. Copilot still uses Groq/Gemini when `COUNCIL_ENABLED=true`.

### Verify
```bash
venv/bin/pytest tests/test_ib_grounding.py -q
# Log: no Chinese in COPILOT lines; bad JSON → heuristic or warning
# LIVE_PULSE: T $… | P&L … with optional " IB" suffix when ib_truth fresh
# Pre-market spike: profit_prob target ≤62–65% (not 80%) when phase=premarket_full
```

---

## 2026-07-01 — IB Truth startup hang after Commander runtime (war sync triple refresh)

### Problem
HANOON froze at `🧭 Commander runtime ON (live)` — no `⚔️ War account` or `🚀 Life engine running` lines. Process blocked on IB Gateway `kevent` for minutes.

### Root cause
`ensure_war_account(ib=…)` at startup called `sync_war_from_ib(force=True)`, which ran **three** full `build_snapshot` passes (positions, session PnL, reconcile) immediately after the IB Truth checklist had already refreshed. Overnight Gateway often stalls on repeated `reqExecutions` / `reqAllOpenOrders`.

### Fix
| File | Change |
|------|--------|
| `core/war_ib_sync.py` | One snapshot per sync via `_resolve_sync_snapshot`; reuse checklist cache when age ≤ `IB_TRUTH_STARTUP_MAX_AGE_SEC` |
| `core/war_account.py` | `sync_ib=False` skips blocking IB pull at boot; `force=False` when sync runs |
| `core/scalper_runner.py` | War ledger init from disk at startup; deferred IB sync on first main-loop tick |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `IB_TRUTH_STARTUP_MAX_AGE_SEC` | `30` | War sync reuses IB Truth snapshot younger than this |
| `WAR_IB_SYNC` | `true` | Set `false` to disable war↔IB sync entirely |

### Verify
```bash
python3 -m pytest tests/test_ib_truth.py -q
# Restart HANOON off-hours: expect ⚔️ War account + 🚀 Life engine within seconds of Commander runtime line
```

---

## 2026-07-01 — Colab fp16 QLoRA dtype mismatch (T4 crash at step 0)

### Problem
Fast path set `fp16=True` but `bnb_4bit_compute_dtype=bfloat16` — training died at step 0 with `NotImplementedError: _amp_foreach_non_finite_check_and_unscale_cuda not implemented for BFloat16`.

### Root cause
QLoRA on Colab T4 leaves bf16 grads; `fp16=True` enables GradScaler which cannot unscale them.

### Fix
| File | Change |
|------|--------|
| `halim/colab/train_toddler_colab.py` | T4 fast: `batch=8`, `fp16=False`, `bf16=False`; `prepare_model_for_kbit_training` |

### Verify
Cell 3: `fp16=False bf16=False profile=colab_t4_fast` — steps pass 0 without crash.

---

---

## 2026-07-01 — Colab max-power notebook (batch 24, ~2008 steps)

### Problem
T4 training at batch 8 used ~4.5/15 GB VRAM — half the GPU idle; 6024 steps ~6h.

### Root cause
Conservative batch 8 with same step count as effective-batch-8 slow path.

### Fix
| File | Change |
|------|--------|
| `halim/colab/halim_toddler_train_max.ipynb` | New notebook: batch 24, OOM fallback cell for batch 16 |
| `halim/colab/train_toddler_colab.py` | `HALIM_MAX_POWER=true` → batch 24 on 15GB T4 |

### Verify
Log: `batch=24 profile=colab_t4_max` and `~2008` total steps; GPU RAM ~10–13 GB.

---

## 2026-07-01 — Colab notebook fresh single-path workflow

### Problem
Old notebook mixed Drive SFT upload, slow defaults, and incremental/fresh modes — confusing for v4 rebuild.

### Root cause
`halim_toddler_train.ipynb` lagged behind hybrid + fast-path scripts.

### Fix
| File | Change |
|------|--------|
| `halim/colab/halim_toddler_train.ipynb` | Rewritten: Colab SFT upload, clean `/content`, fresh fast train, Drive zip only |
| `halim/colab/COLAB_GUIDE.md` | Single Mac→Colab→Mac path |
| `halim/colab/COLAB_DRIVE_CELLS.md` | Mirror notebook cells |

### Verify
Upload notebook + `halim_sft.zip` to Colab; Cell 3 shows `profile=colab_t4_fast`.

---

## 2026-07-01 — Colab hybrid: SFT on Colab, toddler versions on Drive

### Problem
Uploading `halim_sft.zip` to Drive each train is slow; training directly on Drive `toddler_v1/` adds I/O overhead.

### Root cause
`colab_drive_setup.py` only discovered SFT on Drive and defaulted `HALIM_OUT_DIR` to Drive.

### Fix
| File | Change |
|------|--------|
| `halim/colab/colab_drive_setup.py` | SFT: `/content/halim_sft*.zip` before Drive; train on `/content/toddler_v1`; copy Drive toddler only when continuing LoRA |
| `halim/colab/COLAB_DRIVE_CELLS.md` | Hybrid upload cells |

### Verify
Setup log: `sft_zip` from `/content/`, `out_dir` `/content/toddler_v1`, Drive audit still lists `halim_toddler_vN.zip`.

---

## 2026-07-01 — Colab T4 fast training path (fp16 + batch 8)

### Problem
Full v4 SFT on Colab T4 ran ~5.6s/step (~9h) with GPU RAM at 1.7/15 GB — batch=2, bf16 on T4.

### Root cause
Conservative defaults (micro-batch 2, grad_accum 4, bf16) under-utilized T4 tensor cores and VRAM.

### Fix
| File | Change |
|------|--------|
| `halim/colab/train_toddler_colab.py` | `HALIM_FAST_PATH=auto` → T4: batch 8, grad_accum 1, fp16; prints `profile=colab_t4_fast` |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `HALIM_FAST_PATH` | `auto` | T4 fast knobs; `false` restores batch 2 / accum 4 / bf16 |
| `HALIM_BATCH_SIZE` | (auto) | Explicit override |
| `HALIM_FP16` / `HALIM_BF16` | (auto) | Explicit precision override |

### Verify
Restart Colab train; log should show `profile=colab_t4_fast` and `s/it` ~1.5–2.5 vs ~5.6.

---

## 2026-07-01 — PPO teacher via Halim LM (Groq fallback)

### Problem
PPO teacher sessions always called Groq council; Halim LM was not used for batch distillation despite co-evolution gold.

### Root cause
`_call_teacher` only used `CouncilClient`; no Halim inference path.

### Fix
| File | Change |
|------|--------|
| `core/ppo_teacher_training.py` | Halim first (`HALIM_PPO_TEACHER_VIA_HALIM=auto`) → Groq → local heuristic |
| `core/halim_inference.py` | `ppo_teacher` purpose uses 120s timeout |
| `core/trading_focus_guard.py` | `ppo_teacher` allowed during live gold collect |
| `scripts/halim_env.sh` | `HALIM_PPO_TEACHER_VIA_HALIM`, `HALIM_PPO_TEACHER_TIMEOUT_SEC` |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `HALIM_PPO_TEACHER_VIA_HALIM` | `auto` | `auto`/`true`/`halim_first` try Halim when LM ready; `groq_only` skip; `halim_only` no Groq |
| `HALIM_PPO_TEACHER_TIMEOUT_SEC` | `120` | Halim teacher JSON timeout |

### Verify
```bash
python3 -m py_compile core/ppo_teacher_training.py core/halim_inference.py
# After v4 serve up: trigger teacher off-hours or force session
```

---

## 2026-07-01 — Auto-install Colab Halim vN on Mac (apply + watch + HANOON boot)

### Problem
After Colab v4 train, user had to manually install zip, record_train, restart serve.

### Root cause
No orchestrator; docs pointed at v2-only manual cells.

### Fix
| File | Change |
|------|--------|
| `scripts/halim_apply_colab_checkpoint.sh` | **New** — find latest vN zip, install, record_train, MLX deps, eval, restart serve |
| `scripts/halim_watch_colab_zip.sh` | **New** — poll Downloads/Drive until zip appears |
| `halim/scripts/find_colab_checkpoint.py` | **New** — highest `halim_toddler_vN.zip` in Downloads + Google Drive Halim/ |
| `scripts/start_hanoon.sh` | `HALIM_AUTO_INSTALL_COLAB=true` before ensure_halim_active |
| `scripts/ensure_halim_active.sh` | Auto-install hook + latest zip fallback |
| `scripts/halim_start_toddler.sh` | Delegates to apply pipeline |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `HALIM_AUTO_INSTALL_COLAB` | `true` | On HANOON start, install new Colab zip if detected |
| `HALIM_COLAB_SEARCH_DIRS` | *(unset)* | Extra `:`-separated dirs to scan for zips |
| `HALIM_COLAB_WATCH_SEC` | `15` | Watcher poll interval |

### Verify
```bash
chmod +x scripts/halim_apply_colab_checkpoint.sh scripts/halim_watch_colab_zip.sh
./scripts/halim_watch_colab_zip.sh   # while downloading v4
# or after download:
./scripts/halim_apply_colab_checkpoint.sh
```

---

## Mac after Colab Cell 4 (automatic)

```bash
# Option A — run now while v4 downloads from Drive:
./scripts/halim_watch_colab_zip.sh

# Option B — after zip is in ~/Downloads:
./scripts/halim_apply_colab_checkpoint.sh

# Option C — do nothing; next ./scripts/start_hanoon.sh auto-installs (HALIM_AUTO_INSTALL_COLAB=true)
```

Log: `logs/halim_colab_install.log` · State: `models/halim_colab_install_state.json`

---

## 2026-07-01 — Colab Drive: latest halim_toddler_vN (not v2-only)

### Problem
Manual Colab cells and upload_rule required `halim_toddler_v2.zip` even when v3+ existed on Drive; confused users with adapter already on `toddler_v1/`.

### Root cause
Legacy `COLAB_DRIVE_CELLS.md` predated `colab_drive_setup.py` auto-version pick.

### Fix
| File | Change |
|------|--------|
| `halim/colab/colab_drive_setup.py` | `audit_drive()` — list all vN zips, print highest; note when adapter on Drive skips extract |
| `halim/colab/COLAB_DRIVE_CELLS.md` | Rewritten — 4-cell auto flow, PPO→Halim gold table |
| `halim/colab/halim_toddler_train.ipynb` | Cell 2 audits Drive before setup |
| `halim/scripts/package_colab_sft.py` | upload_rule: latest vN optional, SFT required |

### Verify
```bash
./scripts/halim_colab_ready.sh
python3 halim/colab/colab_drive_setup.py  # on Colab after mount
```

---

## 2026-07-01 — Halim live spike participation: IB sizing context + coevolution on every verdict

### Problem
Halim entry LM prompts lacked IB account/sizing numbers; coevolution gold only fired on deferred council path, not every finalized spike verdict.

### Root cause
`_build_entry_prompt` had macro/quality only; `_emit_spike_verdict` logged verdicts but did not call `record_coevolution`.

### Fix
| File | Change |
|------|--------|
| `core/halim_entry_line.py` | `build_halim_entry_ib_context()` — nav, buying power, war deploy, shares_hint from cached ib_truth; math/size_intent lines in prompt |
| `core/ai_commander_verdict.py` | `record_coevolution` on every `_emit_spike_verdict` finalize |
| `scripts/halim_env.sh` | `HALIM_ENTRY_IB_CONTEXT`, blend 0.35, `HALIM_OUTCOME_GOLD`, `HALIM_PPO_COMPLEMENT` |
| `scripts/start_hanoon.sh` | Export entry participation env defaults |
| `tests/test_halim_entry_ib_context.py` | **New** — prompt + IB context unit tests |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `HALIM_ENTRY_IB_CONTEXT` | `true` | IB + sizing line in entry LM prompt |
| `HALIM_ENTRY_BLEND_WEIGHT` | `0.35` | Halim advisory blend into council decision |
| `HALIM_PPO_COMPLEMENT` | `true` | PPO HOLD + Halim ENTER quality override |
| `HALIM_OUTCOME_GOLD` | `true` | Outcome-labeled gold at trade close |

### Verify
```bash
python3 -m pytest tests/test_halim_entry_ib_context.py -q
python3 -m py_compile core/halim_entry_line.py core/ai_commander_verdict.py
./scripts/halim_colab_ready.sh   # v4 pack when gold ready
```

---

## 2026-07-01 — stop.sh: watchdog for IB kevent hang

### Problem
`./stop.sh` logged `Signal 15 received` but process stayed alive indefinitely. Sample showed main thread stuck in `kevent` (ib_insync) — SIGTERM set flags but startup scan / IB sleep never returned to check them.

### Root cause
Python signal handlers cannot run while the main thread blocks in IB's `kevent` wait; shutdown flag alone is insufficient without a force-exit watchdog or abort checks inside long startup/scan paths.

### Fix
| File | Change |
|------|--------|
| `core/scalper_session.py` | `shutdown-watchdog` thread force-exits after `SHUTDOWN_FORCE_EXIT_SEC` (default 25s) |
| `core/scalper_runner.py` | Abort startup (companion ping, IB adopt) on stop; interruptible pre-scan sleep |
| `core/scalper_spike_loop.py` | Abort `_scan_and_rank` per-ticker when stopping |
| `scripts/stop_hanoon.sh` | SIGKILL after `SHUTDOWN_SIGKILL_SEC` (45s default); kill `start_hanoon.sh` parent; stale halim watchdog pkill |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `SHUTDOWN_FORCE_EXIT_SEC` | `25` | In-process watchdog force-exit after signal |
| `SHUTDOWN_SIGKILL_SEC` | `45` | stop script SIGKILL if still alive |
| `SHUTDOWN_WAIT_SEC` | `60` | Max wait before kill phase |

### Verify
```bash
bash -n scripts/stop_hanoon.sh
# ./stop.sh while HANOON in startup scan — process gone within ~25–45s
```

---

## 2026-07-01 — stop.sh: main-loop interruptible sleep (completes stop fix)

### Problem
`stop_hanoon.sh` and session shutdown helpers were merged, but `scalper_runner` main loop still used blocking `ib.sleep()` and off-hours horizon work could continue after stop.

### Fix
| File | Change |
|------|--------|
| `core/scalper_runner.py` | `_interruptible_ib_sleep` on main loop + deferred scanner warmup; abort off-hours train/horizon on stop |

### Verify
`./stop.sh` while HANOON running — process exits within ~30s, not stuck in off-hours block.

---

## 2026-07-01 — stop.sh: responsive shutdown + no duplicate flush

### Problem
`./stop.sh` appeared hung or ineffective: bot kept running for minutes after stop, or stop script ran another full Halim gold + git pipeline even when the live process had already flushed cleanly.

### Root cause
1. Main loop used blocking `ib.sleep(loop_sec)` — stop request / SIGTERM only checked at loop top, so off-hours training and long sleeps delayed exit.
2. `stop_hanoon.sh` released IB client lock before the process exited.
3. After graceful SIGTERM teardown, stop script always re-ran Halim gold + cleanup (duplicate of in-process `_shutdown()`).

### Fix
| File | Change |
|------|--------|
| `core/shutdown_control.py` | `interruptible_wait()` — chunked sleep checks shutdown flag |
| `core/scalper_session.py` | `_interruptible_ib_sleep()`; signal handler writes shutdown file; off-hours train aborts on stop |
| `core/scalper_runner.py` | Main loop + deferred scanner warmup use interruptible sleep; skip off-hours train when stopping |
| `scripts/stop_hanoon.sh` | Stop sidecars first; broader PID match; release IB lock after exit; skip duplicate flush on graceful stop |
| `tests/test_shutdown_control.py` | **New** — interruptible wait tests |

### Verify
```bash
bash -n scripts/stop_hanoon.sh
python3 -m pytest tests/test_shutdown_control.py -q
# ./stop.sh while HANOON running → exits within ~30s with "exited gracefully"
# Hard-kill path still runs fallback Halim gold
```

---

## 2026-07-01 — IB 322 connector log downgrade (completes boot noise fix)

### Problem
After skipping `reqMarketRule` on paper, IB still emitted error 322 through the connector as WARNING.

### Fix
| File | Change |
|------|--------|
| `core/connector.py` | Early return on error 322 at DEBUG |

### Verify
Paper boot — no `IB error 322` WARNING lines during extended refresh.

---

## 2026-07-01 — IB 322 market rules + reqTickers cancel noise at boot

### Problem
Light IB extended refresh at boot flooded logs: `Error 322 Market rule with id = … is missing`, `reqMarketRuleAsync: Timeout`, and `cancelMktData: No reqId found` for held symbols (SPY, QQQ, …). Logger showed `hanoon` instead of preferred `HANOON`.

### Root cause
1. `fetch_market_rules()` called `reqMarketRule(conId)` for every held contract — paper Gateway often has no market rules (IB error 322).
2. `fetch_quote_snapshots()` called `cancelMktData` after `reqTickers` snapshot pulls — snapshots never allocate a streaming reqId.
3. Connector logged 322 as WARNING on every conId.

### Fix
| File | Change |
|------|--------|
| `core/ib_extended.py` | `market_rules_enabled()` — off on paper unless `IB_EXTENDED_MARKET_RULES=true`; skip `fetch_market_rules` when disabled; drop post-`reqTickers` `cancelMktData` |
| `core/connector.py` | Downgrade IB 322 to DEBUG (early return) |
| `core/notify.py` | Logger name back to `HANOON` |
| `tests/test_ib_extended.py` | Paper default + env override tests |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `IB_EXTENDED_MARKET_RULES` | *(unset)* | Paper: skip market rules; live: fetch; `true`/`false` forces |

### Verify
```bash
python3 -m pytest tests/test_ib_extended.py -q
# Boot paper Gateway — no 322 WARNING spam; extended refresh still logs pnl/contracts
# Log prefix: `HANOON | INFO` not `hanoon |`
```

---

## 2026-07-01 — Startup logs: Life Engine banner (scalp + swing)

### Problem
Boot logs showed `HANOON | HANOON | mode=SCALPER | ticker=SPY | capital=$1,000` — duplicate name, legacy scalper-only label, misleading fixed ticker and config cash.

### Fix
| File | Change |
|------|--------|
| `core/startup_log.py` | `log_launch_banner`, `build_session_ready_lines`, phase-aware `capital_line` |
| `core/notify.py` | Logger name `HANOON` (no duplicate prefix in message text) |
| `main.py` | Structured launch block instead of one-line SCALPER log |
| `core/scalper_session.py` | `SESSION READY · scalp + swing` banner |
| `scripts/start_hanoon.sh` | Shell banner text |
| `tests/test_startup_log.py` | **New** |

### Verify
```bash
python3 -m pytest tests/test_startup_log.py -q
# Boot: HANOON LAUNCH → IB TRUTH CHECKLIST → SESSION READY · scalp + swing
```

---

## 2026-07-01 — IB Truth startup checklist

### Problem
No single startup gate confirmed IB snapshot freshness before the trading loop; stale/disconnected Gateway could run with local fiction.

### Fix
| File | Change |
|------|--------|
| `core/ib_truth_checklist.py` | **New** — evaluate, wait, log banner, runtime age gate |
| `core/scalper_runner.py` | Run checklist after balance refresh; halt if blockers |
| `core/scalper_spike_loop.py` | Skip spike entries when runtime snapshot too stale |
| `scripts/start_hanoon.sh` | `IB_TRUTH_STARTUP_*` env defaults |
| `tests/test_ib_truth_checklist.py` | **New** — evaluate fresh/stale |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `IB_TRUTH_STARTUP_CHECK` | `true` | Log checklist at boot |
| `IB_TRUTH_STARTUP_BLOCK` | `true` | Halt if not ready after wait |
| `IB_TRUTH_STARTUP_WAIT_SEC` | `20` | Poll IB until fresh |
| `IB_TRUTH_RUNTIME_MAX_AGE_SEC` | `90` | Block entries if snapshot older |

### Verify
```bash
python3 -m py_compile core/ib_truth_checklist.py
python3 -m pytest tests/test_ib_truth_checklist.py -q
# Boot HANOON — expect "IB TRUTH CHECKLIST — LIVE FROM GATEWAY ✓"
```

---

## 2026-07-01 — account_view sizing uses IB equity outside war phase

### Problem
`sizing_equity()` always preferred war ledger even in `premarket_full` / `rth_full` full-balance phases.

### Fix
| File | Change |
|------|--------|
| `core/account_view.py` | `sizing_equity` returns `display_equity` when `uses_war_sizing` is false |

### Verify
Full-balance phase deploy budget matches IB paper equity in logs.

---

## 2026-07-01 — Swing doctrine: maturity-scaled green entry/exit

### Problem
Swing needed the same green/uptrend/book-profit/slippage logic as scalp but with multi-day holds and slow learning — not full mandatory gates on day one.

### Root cause
Swing only used `swing_intel` score; no `swing_doctrine` layer; no maturity ramp from IB trips + brain stage.

### Fix
| File | Change |
|------|--------|
| `core/swing_doctrine.py` | **New** — maturity profile, swing micro, entry/exit assessment |
| `core/swing_intel.py` | Doctrine gate on verdict (replaces raw green block) |
| `core/swing_executor.py` | Entry doctrine + monitor exit via `commander_exit_ticker` |
| `core/green_trade_doctrine.py` | Optional slip/ride overrides for swing time scale |
| `docs/GREEN_CAPITAL_SWING_DOCTRINE.md` | **New** — complete session summary |
| `tests/test_swing_doctrine.py` | **New** — maturity profile tests |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `SWING_DOCTRINE_ENABLED` | `true` | Swing green doctrine on |
| `SWING_MULTIBAR_MAX_DAYS` | `12` | Max ride days when pred strong |
| `SWING_DOCTRINE_TRIP_MATURE` | `24` | IB trips for full maturity weight |
| `SWING_DOCTRINE_FULL_AT` | `0.72` | Level for mandatory green entry |

### Verify
```bash
python3 -m pytest tests/test_swing_doctrine.py tests/test_green_trade_doctrine.py -q
python3 -m py_compile core/swing_doctrine.py core/swing_executor.py
```

---

## 2026-07-01 — Multi-bar ride + slippage-aware green exit

### Problem
Exits were single-bar biased; no coordinated multi-bar `pred_3bar` ride; slippage prediction scattered in spike loop only; loss cuts not tied to predicted fill degradation.

### Root cause
`assess_green_exit` ignored `pred_3bar`, `profit_run`, bars held, and centralized slippage; profit hunt could exit while ride path was still valid.

### Fix
| File | Change |
|------|--------|
| `core/green_trade_doctrine.py` | `assess_multi_bar_ride`, `predict_exit_slippage`, `assess_dynamic_exit` |
| `core/scalper_exit_executor.py` | Ride logging, loss cut, profit hunt defers on ride, slippage early exit |
| `scripts/start_hanoon.sh` | `GREEN_MULTIBAR_RIDE`, `GREEN_SLIPPAGE_EXIT` defaults |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `GREEN_MULTIBAR_RIDE` | `true` | Hold up to N bars when pred_3bar + profit_run strong |
| `GREEN_SLIPPAGE_EXIT` | `true` | Book profit / cut loss on predicted slippage |
| `GREEN_MULTIBAR_MAX_BARS` | `5` | Max bars to ride before forced book |
| `GREEN_SLIPPAGE_EXIT_PROFIT` | `0.62` | Book green when slip risk exceeds |
| `GREEN_SLIPPAGE_EXIT_LOSS` | `0.52` | Cut red when slip + loss_pressure |

### Verify
```bash
python3 -m py_compile core/green_trade_doctrine.py core/scalper_exit_executor.py
python3 -m pytest tests/test_green_trade_doctrine.py -q
```

---

## 2026-07-01 — Unified green doctrine: same tactics war + full balance

### Problem
Pre-war and post-war full-balance scalping skipped war entry gates and uptrend bypasses; no mandatory green entry/exit across phases. User requires dynamic AI/PPO/Halim decisions with uptrend + green bar everywhere — only sizing differs by capital phase.

### Root cause
`check_entry_allowed()` returned early in `premarket_full` / `rth_full`; `war_gates_active()` required sniper mode only; uptrend bypasses in spike loop and entry executor allowed entries without green alignment.

### Fix
| File | Change |
|------|--------|
| `core/green_trade_doctrine.py` | **New** — `assess_green_entry`, `require_green_entry`, `assess_green_exit`, phase tags |
| `core/war_entry_gates.py` | `war_gates_active` true when unified doctrine + war account |
| `core/war_account.py` | Full phases: pipeline/quality gates yes; war pool OBSERVE no |
| `core/ai_commander_verdict.py` | Green entry veto in `_finalize_entry_decision` |
| `core/scalper_entry_executor.py` | No uptrend bypass when doctrine on; pre-submit green check |
| `core/scalper_spike_loop.py` | Same uptrend + green entry gates on spike path |
| `core/scalper_exit_executor.py` | Dynamic `assess_green_exit` before mechanical lock |
| `core/swing_intel.py` | Swing entries require green doctrine alignment |
| `scripts/start_hanoon.sh` | `GREEN_DOCTRINE_*` env defaults |
| `tests/test_green_trade_doctrine.py` | **New** — doctrine + war gates tests |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `GREEN_DOCTRINE_UNIFIED` | `true` | Same war tactics on all capital phases |
| `GREEN_DOCTRINE_ENTRY` | `true` | Mandatory uptrend + green bar + AI vote + profit_prob |
| `GREEN_DOCTRINE_EXIT` | `true` | Dynamic green exit (AI fade / giveback) |

### Verify
```bash
python3 -m pytest tests/test_green_trade_doctrine.py tests/test_capital_phase.py -q
python3 -m py_compile core/green_trade_doctrine.py
```

---

## 2026-07-01 — Swing intel: full analysis, web learn, policy training

### Problem
Swing used a single 1h SMA signal; no fundamentals/news/macro/web context; training only counted shadow biases.

### Root cause
`swing_executor` called `_simple_swing_signal` only; no integration with `ib_extended` news/fundamentals or Halim web learn cache.

### Fix
| File | Change |
|------|--------|
| `core/swing_intel.py` | **New** — multi-TF technicals, IB hub data, macro, web RAG, composite score |
| `core/swing_web_learn.py` | **New** — off-hours Wikipedia swing topics → learn cache |
| `core/swing_train.py` | **New** — `swing_policy.json` from IB trips + analysis log |
| `core/swing_executor.py` | Entries gated on full `analyze_swing()` |
| `core/swing_shadow.py` | Shadow verdicts include intel fields |
| `core/scalper_runner.py` | Off-hours web learn + policy train |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `SWING_INTEL_ENABLED` | `true` | Full analysis pipeline |
| `SWING_WEB_LEARN` | `true` | Internet read-only swing topics |
| `SWING_INTEL_MIN_SCORE` | `28` | Entry score floor (trained policy overrides) |

### Verify
```bash
python3 -m pytest tests/test_swing_intel.py tests/test_capital_phase.py -q
python3 -m py_compile core/swing_intel.py core/swing_web_learn.py core/swing_train.py
```

---

## 2026-07-01 — Capital phases + real IB swing with HN orderRef tags

### Problem
War $1k sizing applied all day; OBSERVE blocked afternoon learning; swing used virtual `swing_paper_state.json` instead of IB fills; no multi-day swing labels.

### Root cause
Single sizing authority in `get_ai_deploy_budget()`; `active_order_horizon()` scalp-only; no `capital_phase` or `orderRef` horizon tags.

### Fix
| File | Change |
|------|--------|
| `core/capital_phase.py` | **New** — `premarket_full` / `rth_war` / `rth_full` phase router |
| `core/horizon_tags.py` | **New** — `HN\|horizon\|phase\|pipeline` orderRef + log tags |
| `core/swing_executor.py` | **New** — live IB swing brackets (GTC), IB Truth marks |
| `core/swing_learning.py` | **New** — `swing_ib_trips.jsonl` multi-day close labels |
| `core/broker.py` | orderRef + GTC for swing brackets |
| `core/war_account.py` | `war_ledger_applies()` — ledger only in `rth_war` scalp |
| `core/pilot_mode.py` | Full IB budget outside `rth_war` |
| `core/paper_mode.py` | `is_paper_free_learning` when phase is full |
| `core/account_view.py` | Phase-aware `sizing_equity` |
| `core/trade_horizon.py` | `swing_ib_live_enabled`, tag `capital_phase` |
| `core/scalper_runner.py` | Swing IB cycle + trip ingest off-hours |
| `core/scalper_entry_executor.py` | Scalp skips swing-held symbols; war ledger guard |
| `core/scalper_exit_executor.py` | Swing close → `record_swing_ib_close` |
| `core/ppo_swing_train.py` | Prefer IB trips over shadow verdicts |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `CAPITAL_PHASES_ENABLED` | `true` | 3-phase capital routing |
| `CAPITAL_PHASE_SKIP_LAB` | `true` | War dry → full IB (not LAB pool) |
| `SWING_IB_LIVE` | `true` | Real IB swing in full phases |
| `SWING_IB_MAX_POSITIONS` | `3` | Max concurrent swing lines |
| `SWING_STOP_PCT` / `SWING_TARGET_PCT` | `0.04` / `0.08` | Swing bracket width |

### Verify
```bash
python3 -m pytest tests/test_capital_phase.py tests/test_horizon_tags.py tests/test_trade_horizon.py -q
python3 -m py_compile core/capital_phase.py core/swing_executor.py core/swing_learning.py
```

---

## 2026-07-01 — Account evaluator: IB Truth for position/order snapshots

### Problem
`AccountEvaluator` still called `reqPositions()` and `reqAllOpenOrders()` on market transitions, duplicating `ib_truth` / `ib_hub` refresh.

### Root cause
Evaluator predated central snapshot; open/close reports bypassed `get_snapshot()`.

### Fix
| File | Change |
|------|--------|
| `core/account_evaluator.py` | `_ib_positions` / `_open_orders` read from `get_snapshot()` after optional `refresh()` |

### Verify
```bash
python3 -m py_compile core/account_evaluator.py
```

---

## 2026-07-01 — Remove redundant local/Yahoo paths; IB Truth single source

### Problem
Macro (Yahoo), position shares/avgCost (`ib.positions()`), and session PnL (`bot_nav - INITIAL_CASH`) duplicated data already in `ib_truth` / `ib_hub` / `ib_macro`.

### Root cause
Legacy fallbacks ran during RTH even when IB snapshot was fresh; macro tick did not pass connector; war session PnL used FIFO only.

### Fix
| File | Change |
|------|--------|
| `core/market_context.py` | IB-first refresh via connector; Yahoo only if `MACRO_YAHOO_FALLBACK=true`; `get_ib_market_snapshot` → `ib_macro` |
| `core/fill_tracker.py` | `_truth_long_position` for shares/avgCost; skip raw `positions()` when truth on |
| `core/account_view.py` | Equity/PnL from snapshot when `refreshed_at > 0` |
| `core/scalper_runner.py` | Macro warm/tick pass `conn`; truth-first position sync; skip local NAV when IB sync on |
| `core/position_sync.py` | Truth-only `ib_long_position_map`; no duplicate `positions()` when fresh |
| `core/position_intel.py` | No legacy `reqPositions` when truth enabled |
| `core/scalper_exit_executor.py` | Commander exit reads truth snapshot first |
| `core/war_ib_sync.py` | `session_pnl_war` prefers IB `RealizedPnL` tag |
| `core/halim_companion.py` | Fallback uses `account_equity`, not `bot_nav` fiction |
| `core/scalper_session.py` | Close/daily reports use `account_view.day_pnl` |
| `core/daily_self_evaluation.py` | Day PnL from `account_view` |
| `core/scanner.py` | Held tickers from truth snapshot |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `MACRO_YAHOO_FALLBACK` | `false` | Allow Yahoo macro only when IB disconnected |
| `MACRO_FROM_IB` | `true` | SPY/QQQ/VIX via `ib_macro` |

### Verify
```bash
python3 -m pytest tests/test_ib_hub.py tests/test_ib_extended.py tests/test_ib_truth.py -q
python3 -m py_compile core/scalper_exit_executor.py core/market_context.py
python3 scripts/ib_services_audit.py
```

---

## 2026-07-01 — IB Hub: orchestrate entire IB API surface for all programs

### Problem
IB services were spread across modules; some tags/APIs unused; balance refresh did not pull extended/macro every tick.

### Root cause
No single `ib_hub` orchestrator; `accountValues()` still called directly in places; look-ahead margin tags unmapped.

### Fix
| File | Change |
|------|--------|
| `core/ib_hub.py` | **New** — `refresh_all_ib_services`, `get_hub_context`, `audit_ib_coverage` |
| `core/ib_extended.py` | reqAccountSummary, reqTickers quotes, reqCompletedOrders, multi fundamental reports |
| `core/ib_truth.py` | reqExecutions fallback, reqPositions prefetch, look-ahead tags, account code |
| `core/scalper_runner.py` | `_refresh_account_balance` → ib_hub |
| `core/halim_companion.py` | `get_hub_context` for full AI bundle |
| `scripts/ib_services_audit.py` | **New** — coverage report CLI |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `IB_HUB_ENABLED` | `true` | Single orchestrated IB refresh on balance poll |
| `IB_FUNDAMENTAL_REPORTS` | `ReportSnapshot,Ratios` | Multiple fundamental pulls |

### Verify
```bash
python3 scripts/ib_services_audit.py
python3 -m pytest tests/test_ib_hub.py -q
```

---

## 2026-07-01 — SyntaxError exit executor + IB client_id=1 guard

### Problem
1. HANOON failed to start: `SyntaxError: unmatched ')'` in `scalper_exit_executor.py:494` — orphaned kwargs after war sync `except: pass`.
2. Other scripts could connect IB Gateway as client_id=1 → market data subscription conflicts (10197).

### Root cause
Merge corruption left dangling function arguments. `start_hanoon.sh` only warned on duplicate client_id, did not block.

### Fix
| File | Change |
|------|--------|
| `core/scalper_exit_executor.py` | Remove orphaned lines 478–494 |
| `core/ib_client_guard.py` | **New** — lock file + process scan for reserved client_id |
| `scripts/guard_ib_client_id.py` | **New** — CLI check/acquire/release |
| `scripts/start_hanoon.sh` | Hard-fail start if client_id guard fails |
| `scripts/stop_hanoon.sh` | Release lock on shutdown |
| `core/connector.py` | acquire lock on connect, release on disconnect |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `CLIENT_ID` / `IB_CLIENT_ID` | `1` | Reserved HANOON slot — other tools use 97+ |

### Verify
```bash
python3 -m py_compile core/scalper_exit_executor.py
python3 scripts/guard_ib_client_id.py --client-id 1
./START.command   # should boot main.py
```

---

## 2026-07-01 — IB extended A–Z wired (fundamentals, news, WSH, PnL, what-if)

### Problem
Fundamentals, news, WSH, reqPnLSingle, whatIfOrder, contract details, and horizon roadmap items were documented as "planned" only.

### Root cause
No `ib_extended` module; macro/news used Yahoo; no margin preview; swing paper pool and PPO swing weights missing.

### Fix
| File | Change |
|------|--------|
| `core/ib_extended.py` | **New** — reqPnL, reqPnLSingle, contract details, fundamentals, news, WSH, head timestamp, marketRule, whatIfOrder |
| `core/broker.py` | what-if margin gate before bracket entry |
| `core/ib_truth.py` | `ib_ai_context` merges extended cache + light refresh |
| `core/swing_paper.py` | **New** — virtual `WAR_SWING_PAPER_USD` pool, IB marks |
| `core/ppo_swing_train.py` | **New** — `models/ppo_swing_1h.json` from shadow verdicts |
| `core/war_account.py` | `horizon` on ledger rows; `swing_paper_capital_usd()` |
| `core/scalper_session.py` | Off-hours full IB extended + swing paper + PPO swing train |
| `tests/test_ib_extended.py` | **New** |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `IB_EXTENDED_ENABLED` | `true` | Master switch for extended IB pulls |
| `IB_EXTENDED_FULL_TTL_SEC` | `3600` | Off-hours full refresh interval |
| `IB_WHATIF_MARGIN_GATE` | `true` | Block bracket if margin > available funds |
| `WAR_SWING_PAPER_USD` | `2000` | Virtual swing paper pool |
| `SWING_PAPER_ENABLED` | `false` | Virtual swing entries from shadow verdicts |

### Verify
```bash
python3 -m pytest tests/test_ib_extended.py tests/test_ib_data_catalog.py -q
# Off-hours log: "IB extended refresh (full): pnl=... news=... wsh=..."
# Entry log: "What-if SYMBOL xN: marginΔ=..."
```

---

## 2026-07-01 — IB data catalog A–Z + ib_ai_context for all AIs

### Problem
IB provides dozens of account, order, fill, and market endpoints; bot duplicated fetches (Yahoo macro, local position math) and AIs lacked bracket/margin/PDT context.

### Root cause
No single inventory of IB capabilities; `ib_truth_context` was minimal; macro used Yahoo when IB was connected.

### Fix
| File | Change |
|------|--------|
| `core/ib_data_catalog.py` | **New** — A–Z tag map + API category registry |
| `core/ib_macro.py` | **New** — SPY/QQQ/VIX via `reqTickers` (one-shot) |
| `core/ib_truth.py` | Extended account tags, bracket order fields, commissions, server time, `ib_ai_context()` |
| `core/market_context.py` | IB-first macro when connector live (`MACRO_FROM_IB`) |
| `core/halim_companion.py` | `live_snapshot` merges `ib_ai_context` |
| `core/account_evaluator.py` | Positions/orders from `get_snapshot()` not raw IB |
| `docs/IB_DATA_CATALOG.md` | **New** — human A–Z reference |
| `scripts/start_hanoon.sh` | `MACRO_FROM_IB`, `IB_MACRO_TTL_SEC` |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `MACRO_FROM_IB` | `true` | SPY/QQQ/VIX from IB; Yahoo only if disconnected |
| `IB_MACRO_TTL_SEC` | `120` | Macro snapshot cache |

### Verify
```bash
python3 -m pytest tests/test_ib_truth.py tests/test_trade_horizon.py tests/test_ib_data_catalog.py -q
# Halim /status or companion chat should show ib_maint_margin, ib_day_trades_remaining, bracket stops
```

---

## 2026-07-01 — IB Truth extended + trade horizon (scalp live, swing shadow)

### Problem
Accounting and AI context still mixed local FIFO math with IB tags. No structured path for swing/position horizons while scalp matures. Duplicate IB fetches in `daily_ib_learning`.

### Root cause
`ib_truth` lacked open orders and full account tags; `day_pnl_from_snapshot` preferred FIFO over IB `RealizedPnL`. No horizon module or shadow scan. Verdict/fill logs untagged.

### Fix
| File | Change |
|------|--------|
| `core/ib_truth.py` | Open orders, portfolio realized/marketValue, account tags; `session_pnl_ib`; `ib_truth_context()` |
| `core/trade_horizon.py` | **New** — scalp/swing/position gates, maturity, scalp profit gate from IB |
| `core/swing_shadow.py` | **New** — off-hours 1h shadow verdicts (no orders), IB marks |
| `core/smart_stack.py` | `horizon` on spike verdicts |
| `core/fill_tracker.py` | `horizon=scalp` default on fill ledger |
| `core/scalper_runner.py` | Swing shadow + scalp gate update on off-hours train tick |
| `core/rth_session.py` | IB session PnL + open order count in reply context |
| `core/halim_companion.py` | `session_pnl` from IB; `horizon_context()` |
| `core/account_view.py` | `ib_session_pnl`, `ib_open_orders` |
| `core/daily_ib_learning.py` | Delegate account snapshot to `ib_truth` |
| `scripts/start_hanoon.sh` | `SWING_SHADOW_*`, `SWING_PAPER_ENABLED`, `POSITION_HORIZON_ENABLED` |
| `docs/HORIZON_ROADMAP.md` | **New** — one-hull horizon plan |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `SWING_SHADOW_ENABLED` | `true` | Off-hours 1h shadow scan (child+ maturity) |
| `SWING_SHADOW_INTERVAL_SEC` | `900` | Min seconds between shadow scans |
| `SWING_PAPER_ENABLED` | `false` | Swing orders (teen+ + scalp gate) — not wired to orders yet |
| `SCALP_PROFIT_GATE_FORCE` | — | `pass` / `fail` override for swing paper gate |

### Verify
```bash
python3 -m pytest tests/test_ib_truth.py tests/test_trade_horizon.py -q
python3 scripts/reconcile_ib_truth.py
# Off-hours: expect "Swing shadow: N verdict(s)" in logs once per SWING_SHADOW_INTERVAL_SEC
```

---

## 2026-07-01 — War IB sync log spam + RTH-aware Telegram/Halim replies

### Problem
1. `War IB sync applied` logged every ~3s — `_refresh_account_balance()` called `sync_war_from_ib(apply=True)` on every main-loop tick and every `/status` Telegram command.
2. `Price snapshot refresh PLTR/MARA/RIOT` spammed after 16:00 ET — stream heal polled watchlist tickers off-hours.
3. Halim/Telegram replies used raw `bot_nav` / midnight PnL — not RTH session context (after hours at 16:28 still looked "live").

### Root cause
War sync had no throttle or change detection; ledger line appended every poll. Stream heal ignored `can_trade_now`. `_runner_ctx` called full balance refresh (triggering sync). Companion snapshot lacked `rth_tier` / `market_note`.

### Fix
| File | Change |
|------|--------|
| `core/war_ib_sync.py` | 90s throttle (`WAR_IB_SYNC_INTERVAL_SEC`); log/ledger only when nav/slots/pnl change |
| `core/scalper_runner.py` | War sync removed from `_refresh_account_balance`; `_maybe_sync_war_from_ib()` on loop + post-exit |
| `core/scalper_runner.py` | Skip snapshots/stream-heal off-hours unless ticker is held |
| `core/rth_session.py` | `rth_reply_context()` for AI/Telegram |
| `core/halim_companion.py` | `live_snapshot` uses RTH context + `ib_fifo_session_pnl` (companion chat) |
| `core/telegram_listener.py` | `_runner_ctx` uses `ib_truth.refresh` + RTH fallback text |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `WAR_IB_SYNC_INTERVAL_SEC` | `90` | Min seconds between war ledger apply+log |

### Verify
```bash
python3 -m pytest tests/test_ib_truth.py -q
# After restart: no War IB sync lines every 3s; at most 1 per 90s if unchanged
# After 16:00 ET: no PLTR/MARA snapshot INFO unless position held
# /status at 16:28 shows "After hours" + RTH session PnL
```

---

## 2026-07-01 — IB Truth session aligned to RTH 09:30–16:00 ET

### Problem
IB Truth FIFO session PnL used calendar midnight ET, while war ledger resets at **09:30 RTH** — premarket fills inflated session PnL and Telegram/AI replies didn't match the RTH trading day.

### Root cause
`session_start_ts_et()` returned 00:00 ET; `_ib_starting_balance` was set at bot startup (often premarket), not at the bell.

### Fix
| File | Change |
|------|--------|
| `core/rth_session.py` | `rth_session_start_ts`, `execution_in_rth_window`, `ib_truth_session_start_ts` |
| `core/ib_truth.py` | RTH session window + filter premarket/after-hours fills |
| `core/account_view.py` | Day PnL uses `_rth_starting_balance` when set |
| `core/scalper_session.py` | `_on_rth_open` snapshots NetLiq baseline at 09:30 |
| `core/scalper_runner.py` | `_rth_starting_balance` field |
| `scripts/start_hanoon.sh` | `IB_TRUTH_RTH_SESSION`, `IB_TRUTH_RTH_FILLS_ONLY` |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `IB_TRUTH_RTH_SESSION` | `true` | FIFO since 09:30 ET (not midnight) |
| `IB_TRUTH_RTH_FILLS_ONLY` | `true` | Exclude premarket/after-hours fills |

### Verify
```bash
python3 -m pytest tests/test_ib_truth.py -q
# At 09:30 ET: log shows "RTH session baseline NetLiq=..."
# Premarket fills excluded from ib_fifo_session_pnl
# Telegram session_pnl matches ib_fifo_session_pnl (RTH scope)
```

---

## 2026-07-01 — IB Truth hub: entire bot sources positions/PnL from IB Gateway

### Problem
War ledger, bot_nav, coach session PnL, and position intel could diverge from IB (ghost exits like TZA -$3,212, stale war slots, $3.5k paper cap vs $1k intent). User required **IB Gateway as single source of truth** for all programs — not war-only.

### Root cause
Fragmented IB fetches across `account_view`, `position_intel`, `war_account`, `fill_reconciler` with local ledger fiction when slots missing. `record_exit` trusted bogus `pnl_usd_ib` on ghost exits. `WAR_CAPITAL_USD` defaulted to $3,500 in `start_hanoon.sh`.

### Fix
| File | Change |
|------|--------|
| `core/ib_truth.py` | **New** — central IB snapshot: account, positions, portfolio, FIFO fills, session PnL; `refresh()` + `apply_to_runner()` |
| `core/war_ib_sync.py` | War virtual $1k pool synced from IB Truth; reconcile report |
| `core/account_view.py` | Session PnL/equity from IB Truth snapshot |
| `core/position_intel.py` | Positions/unrealized from IB Truth |
| `core/scalper_runner.py` | `_refresh_account_balance` → `ib_truth.refresh` + war sync each tick |
| `core/war_account.py` | Ghost exit PnL cap/skip; `ensure_war_account(ib=)`; $1k default |
| `core/system_status.py` | IB Truth fields in status dump |
| `scripts/reconcile_ib_truth.py` | CLI reconcile + `--apply` war sync |
| `scripts/start_hanoon.sh` | `WAR_CAPITAL_USD=1000`, `WAR_IB_SYNC=true` |
| `tests/test_ib_truth.py` | FIFO PnL, ghost exit guard, $1k cap |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `REQUIRE_IB_FILL_SYNC` | `true` | Master switch — IB Truth on for entire bot |
| `WAR_IB_SYNC` | `true` | War ledger positions/session PnL from IB on refresh |
| `WAR_CAPITAL_USD` | `1000` | Virtual war pool for sizing (not full IB NAV) |

### Verify
```bash
python3 -m pytest tests/test_ib_truth.py tests/test_war_multi_position.py -q
python3 scripts/reconcile_ib_truth.py          # report only (needs IB Gateway)
python3 scripts/reconcile_ib_truth.py --apply  # rewrite war state from IB
# Live: bot_nav == IB NetLiq; positions from IB; war nav ~$1k pool
```

---

## 2026-07-01 — Halim echo blocks green spikes + INTC 10x quote + snapshot spam

### Problem
1. Green `profit_prob=91%` spikes (BIYA, YHC, INLF) never entered: Halim toddler LM echoed training `ppo=hold` → `enter=False conf≈50%`; AI-sure treated echo as real skip (`halim:ai_sure_wait`).
2. Council `in_flight` then `force-clear` with no resolution — timeout fallback blocked under AI-sure (`allows_timeout_fallback_entry=false`).
3. INTC entered at ~$140 (10× real ~$14) on `halim:quality_flash`; position monitor spammed `Price snapshot refresh INTC` every 1–2s.

### Root cause
1. Halim prompt lacked calculative quality (`profit_prob`, `enter_ok`); parser preferred training echo over score echo.
2. `_service_stale_councils` dropped pending councils without running timeout merge; no AI-sure quality timeout path.
3. IB paper quotes not sanitized at spike/snapshot time; `_force_price_snapshot` had no per-ticker cooldown and always logged INFO.

### Fix
| File | Change |
|------|--------|
| `core/halim_entry_line.py` | Quality in prompt; `halim_advisory_is_echo()`; score echo before training echo; `advisory_kind=echo` |
| `core/smart_stack.py` | `halim:ai_sure_escalate` pending on echo+green quality; teacher rings on echo+quality |
| `core/ai_commander_entry.py` | Pass quality to Halim ring; `force_timeout` in poll |
| `core/live_ai_pipeline.py` | `council:ai_sure_quality_timeout` when council times out with green quality |
| `core/entry_quality.py` | Allow `ai_sure_quality_timeout` through veto |
| `core/scalper_runner.py` | Force-clear resolves entry council; snapshot cooldown + sanitize |
| `core/fill_tracker.py` | `sanitize_quote_price()` |
| `core/scalper_spike_loop.py` | Sanitize live_px; QUOTE veto when >35% from bar |
| `tests/test_ai_sure_entry.py`, `tests/test_position_entry_price.py` | Echo escalate + quote sanitize |

### Env vars
| Var | Default | Effect |
|-----|---------|--------|
| `PRICE_SNAPSHOT_COOLDOWN_SEC` | `8` | Min seconds between IB snapshots per ticker |
| `SMART_STACK_AI_SURE_ENTRY` | `true` | Unchanged — now escalates echo to council |

### Verify
```bash
python3 -m pytest tests/test_ai_sure_entry.py tests/test_position_entry_price.py -q
# Live: green spike → Halim local teacher:halim_echo_quality OR halim:ai_sure_escalate pending
# Council force-clear → may enter via council:ai_sure_quality_timeout when prob green
# INTC spike blocked or priced ~$14 not $140; snapshot log ≤1 per 8s per ticker
# AI-sure timeout uses base min_prob (not war-bumped) so 90% green passes when war trips elevated floor
```


---

## 2026-06-30 — Multi-position monitor race + false green lock on wrong tick

### Problem
With several recovered IB positions open, LIVE_PULSE showed cross-ticker PnL (e.g. BITO +146% with SOXS stops) and GREEN LOCK / profit hunt fired on phantom gains (SOXS evaluated at BITO's ~$7.96). War ledger warned "no open slot" on recovered exits.

### Root cause
1. **Thread race:** IB tick callbacks and main-loop monitor both called `_load_position_context` / `_save_position_context` on shared runner state without a lock — SOXS entry/stops could be saved into BITO's slot mid-pulse.
2. **Aggregate shares leak:** `_save_position_context` wrote `self.shares` after `_refresh_aggregate_position_state` had summed all slots.
3. **Wrong stream fallback:** `_dm_for_ticker` borrowed `_active_stream_ticker`'s DataManager for unrelated symbols.
4. **No price sanity gate:** Green lock and profit hunt did not reject quotes >35% from entry (cross-ticker tick bleed).
5. **War adopt:** `adopt_ib_positions_into_slots` never called `record_entry`.

### Fix
| File | Change |
|------|--------|
| `core/scalper_runner.py` | `threading.RLock` around load/save; `_ctx_slot_shares`; `_resolve_monitor_price()` with IB snapshot fallback; `_record_war_adoptions()`; `_dm_for_ticker` per-ticker only |
| `core/scalper_exit_executor.py` | Monitor uses trusted price; profit hunt / green lock / mechanical exits gated; peak/pulse skipped when untrusted |
| `core/position_context.py` | `slot_price_sane()` helper |
| `tests/test_position_context_isolation.py` | `slot_price_sane` cross-ticker case |

### Env vars
None new.

### Verify
```bash
python3 -m pytest tests/test_position_context_isolation.py -q
# Live: no LIVE_PULSE with +100% on unrelated tickers; GREEN LOCK only when px within 35% of entry
# Recovered adopt: war exits without "no open slot"
```

---

## 2026-06-30 — Scalper mixin missing imports (require_ib_fill_sync NameError)

### Problem
HANOON crashed on startup after monolith split:
- `NameError: name 'require_ib_fill_sync' is not defined` in `_ib_sync_enabled` / `_refresh_account_balance`
- `NameError: name 'clear_transient_md_blocks' is not defined` in `_on_rth_open`
- `NameError: name 'get_live_scan_universe' is not defined` in startup IB scan (`scalper_spike_loop`)

### Root cause
Mixin extraction moved methods into separate modules but only copied a minimal header — symbols resolved from `scalper_runner.py` module scope were no longer in scope for mixin method globals.

### Fix
| File | Change |
|------|--------|
| `core/scalper_mixin_imports.py` | **New** — shared imports for all scalper mixins (fill sync, git sync, pilot_mode scan universe, session helpers, etc.) |
| `core/scalper_entry_executor.py` | `from core.scalper_mixin_imports import *` |
| `core/scalper_exit_executor.py` | same |
| `core/scalper_session.py` | same |
| `core/scalper_spike_loop.py` | same |
| `core/scalper_filters.py` | **New** — `only_uptrend()` shared by runner + mixins |
| `core/ai_commander_mixin_imports.py` | **New** — shared imports for ai_commander mixins (`get_ai_deploy_budget`, brackets, etc.) |
| `core/ai_commander_entry.py` | `from core.ai_commander_mixin_imports import *` (+ exit/verdict/deferred) |
| `core/scalper_session.py` | `_shutdown`: `pnl_pct=ib_pnl_pct` (was undefined `pnl_pct`) |

### Verify
```bash
python3 -c "from core.scalper_runner import ScalperRunner"
python3 -m pytest tests/ -q
./scripts/start_hanoon.sh   # no Fatal error; startup scan locks tickers
```

---

## 2026-06-30 — War AI sizing (full pool deploy, advisory bullets)

### Problem
With `AI_UNLIMITED_MODE` / full AI control, war entries were still capped to mechanical bullet slices (~$437 = $3500/8). AI could not deploy the full war pool for profit hunting; `bullets_left` blocked entries even when settled cash remained.

### Root cause
`_entry_deploy_cap`, `rescale_decision_for_war`, and `get_ai_deploy_budget` used `min(bullet, settled)`; `war_bullets_remaining` counted settled//bullet slices as hard trip limits.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `war_ai_sizing_enabled()`; full settled deploy cap; trip block = pool dry only |
| `core/pilot_mode.py` | War deploy budget = full settled (minus reserve) under AI sizing |
| `core/smart_stack.py` | Skip mechanical bullet-left posture bumps when AI sizing |
| `scripts/start_hanoon.sh` | `WAR_AI_SIZING=true`, `WAR_CASH_RESERVE_PCT=0.05` |

### Env vars
```bash
WAR_AI_SIZING=true          # default on with AI_UNLIMITED / AI_FULL_CAPITAL_ACCESS
WAR_CASH_RESERVE_PCT=0.05   # small cash buffer; rest deployable per AI decision
WAR_BULLETS=8               # advisory session budget for AI context, not slice size
```

### Verify
```bash
python3 -m pytest tests/test_war_account_rth.py -q
# war_context_line shows deploy_cap=$3,325 bullets_advisory=8/8
```

---

## 2026-06-30 — IB fill sync hardening (entry+exit P&L from broker)

### Problem
User audit: ensure entry/exit fills and per-trade P&L come from IB executions, not quote estimates, and bot NAV stays aligned with IB NetLiquidation.

### Root cause
`build_close_record` used slot entry only; exit reconcile did not re-check BOT executions; `bot_nav` was recalculated from internal cash mid-loop even when `REQUIRE_IB_FILL_SYNC=true`; post-trade account values were not refreshed from IB immediately.

### Fix
| File | Change |
|------|--------|
| `core/fill_reconciler.py` | `resolve_entry_from_ib`; IB commission on fills; net P&L with commission |
| `core/fill_tracker.py` | `round_trip_pnl(..., commission=)` |
| `core/scalper_exit_executor.py` | IB-only `_build_trade_close_record`; refresh account after finalize |
| `core/scalper_entry_executor.py` | Refresh IB account after entry fill |
| `core/scalper_runner.py` | Skip internal NAV recalc when IB sync on |
| `scripts/start_hanoon.sh` | Export `IB_FILL_FORCE_SEC` |
| `tests/test_fill_reconciler.py` | Strict + confirmed fill tests |

### Env vars
```bash
REQUIRE_IB_FILL_SYNC=true
IB_FILL_STRICT=true
IB_FILL_FORCE_SEC=120
```

### Verify
```bash
python3 -m pytest tests/test_fill_reconciler.py tests/test_fill_tracker.py -q
# Live: entry log `✅ IB entry confirmed`; exit `📕 EXIT (IB fill)`; Day P&L matches IB account change
```

---

## 2026-06-30 — Monolith split: scalper_runner, ai_commander, git_sync learning

### Problem
`scalper_runner.py` (~8.8k), `ai_commander.py` (~3.2k), and `git_sync.py` (~2.5k) were unmaintainable monoliths. An earlier auto-sync pass left broken `git_sync_*` submodules and overwrote `commander_learning.py` (telegram guidance) with an AICommander mixin.

### Root cause
- AST mixin extraction removed the `class ScalperRunner(...)` line until script was fixed.
- Mixin module `commander_learning.py` collided with existing `load_commander_guidance()` / `run_commander_learning_cycle()` module used by `telegram_listener`.
- Full `git_sync` state-module split introduced invalid `global S._repo` and circular imports.

### Files
- `core/scalper_runner.py` + `core/scalper_exit_executor.py`, `scalper_entry_executor.py`, `scalper_session.py`, `scalper_spike_loop.py`
- `core/ai_commander.py` + `core/ai_commander_verdict.py`, `ai_commander_deferred.py`, `ai_commander_entry.py`, `ai_commander_exit.py`
- `core/commander_learning.py` — restored as standalone guidance module (not a mixin)
- `core/git_sync.py` + `core/git_sync_learning.py` (learning restore/push only; push/commit/routing remain in monolith)
- `scripts/extract_scalper_mixins.py`, `scripts/extract_ai_commander_mixins.py`

### Env vars
None.

### Verify
```bash
python3 -c "from core.scalper_runner import ScalperRunner; from core.ai_commander import AICommander; from core.commander_learning import GUIDANCE_PATH; from core import git_sync"
python3 -m pytest tests/ -q
```

---

## 2026-06-30 — Balance-driven war trips (settled cash, not fixed cap)

### Problem
User wanted trips tied to **available settled balance**, not a fixed daily round-trip counter. With $3,469 settled, fixed cap 5/5 blocked entries despite cash for ~6 more bullets.

### Root cause
`round_trips_today >= max_war_round_trips_per_day()` forced OBSERVE independent of settled cash. `bullets_used_session` was telemetry only; trip cap was the real gate.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `WAR_BALANCE_DRIVEN_TRIPS` (paper default true): `war_bullets_remaining()`, `_trip_cap_blocks()` from settled; `war_account_state()` for posture; `_entry_deploy_cap()` uses remaining cash |
| `core/smart_stack.py` | War posture uses `war_bullets_remaining` when balance-driven |
| `core/ai_notifier.py` / `scalper_runner.py` | Telegram/session close show bullets left + fired |
| `scripts/start_hanoon.sh` | `WAR_BALANCE_DRIVEN_TRIPS=true`, `WAR_BALANCE_DRIVEN_LAB=true` |

### Env vars
```bash
WAR_BALANCE_DRIVEN_TRIPS=true      # paper default — cap = settled / min bullet
WAR_BALANCE_DRIVEN_TRIPS=false     # legacy fixed trip cap (live default)
WAR_BALANCE_DRIVEN_LAB=true        # lab pool same logic
```

### Verify
- `round_trips=5` + `settled=$3469` → `mode=WAR_ACTIVE`, `bullets_left≥5`
- OBSERVE only when `settled < min_entry`
- `pytest tests/test_war_account_rth.py -q`

---

## 2026-06-30 — War trip cap vs settled cash + Halim trade Telegram

### Problem
Paper war showed `settled=$3,469` but `mode=OBSERVE` with `trips=5/5` while `WAR_BULLETS=8` — cash was usable but trip cap blocked entries. Logs said "capital dry" when the real blocker was trips. Trade Telegram used structured templates only (no Halim local voice); `live_snapshot` still used poisoned `bot_nav`/`INITIAL_CASH`.

### Root cause
1. `WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY=5` mismatched `WAR_BULLETS=8`.
2. RTH reset runs once at 09:30 ET; restarts same day kept exhausted trip counters.
3. `notify_event_wants_api` false → `compose_outbound` returned templates without Halim companion path.
4. `live_snapshot()` ignored `account_view` and war pool context.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | Paper trip default follows `WAR_BULLETS`; `_maybe_refresh_trips_if_settled` on `ensure_war_account`; clearer trip-cap OBSERVE reason |
| `scripts/start_hanoon.sh` | `WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY=8`, `WAR_FRESH_TRIPS_ON_START=true` |
| `core/halim_companion.py` | `halim_trading_notify()` for trade/session events; `live_snapshot` uses IB + war context |
| `core/ai_notifier.py` | Halim-first when API off/busy; war line in structured fallbacks |
| `core/scalper_runner.py` | `_notify_context` includes war pool + runner ref for Halim |

### Env vars
```bash
WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY=8   # match WAR_BULLETS
WAR_FRESH_TRIPS_ON_START=true         # paper: refresh trips on HANOON restart if settled remains
HALIM_TELEGRAM_TRADE_NOTIFY=true      # Halim local voice for trade Telegram
```

### Verify
- Restart HANOON after trip cap: log `War trips refreshed on HANOON start` and `mode=WAR_ACTIVE`.
- Telegram startup/entry/close shows IB day P&L + war pool line.
- `pytest tests/test_war_account_rth.py -q`

---

## 2026-06-30 — Learning flywheel hardening (outcome teacher, proxy holdout, Halim gate)

### Problem
At **adult** stage PPO teacher API budget was 0 → `heuristic_fallback` labels (7–31% WR in brain log). Proxy reported 98–100% on random 80/20 split (overfit). Stage could jump to adult on inflated proxy acc. Halim checkpoints promoted to `latest` without golden probe eval. PPO reward training was tail-only (calm sessions erased volatile-regime memory).

### Root cause
1. `allow_ppo_teacher_api` → `_heuristic_teacher_plan` when API cap zero.
2. `hybrid_distiller.train_teacher_proxy` used random split only; `HYBRID_DISTILL_AUTO_FAST_PATH` default true.
3. `compute_stage` accelerated to teen/adult on raw `proxy_accuracy` alone.
4. `register_checkpoint.py` symlinked `latest` unconditionally.
5. `collect_training_records` used recent tail only.

### Fix
| File | Change |
|------|--------|
| `core/ppo_teacher_training.py` | `_outcome_teacher_plan()` from PnL + skip verdicts; `_local_teacher_plan()` prefers outcome over heuristic |
| `core/hybrid_distiller.py` | Time + ticker holdout; `holdout_accuracy` drives fast path; auto fast path default off |
| `core/brain_maturity.py` | Rolling WR + holdout gates for teen/adult; `BRAIN_ADULT_PPO_TEACHER_DAILY` floor; holdout for API multiplier |
| `core/halim_promotion_gate.py` | **New** — golden `eval_toddler` probes before `latest` symlink |
| `halim/scripts/register_checkpoint.py` | Routes through promotion gate (force/disable via env) |
| `core/experience_buffer.py` | `sample_balanced_records()` — 30% high-vol mix |
| `core/ppo_reward_trainer.py` | Balanced sampling in `collect_training_records` |
| `core/config.py` | `HALIM_PROMOTION_GATE`, holdout flags, `HYBRID_DISTILL_AUTO_FAST_PATH` default false |

### Env vars
```bash
PPO_TEACHER_OUTCOME_LABELS=true          # default — outcome teacher when API off
BRAIN_ADULT_PPO_TEACHER_DAILY=2          # small cloud PPO teacher budget at adult
BRAIN_TEEN_MIN_WIN_RATE=0.38
BRAIN_ADULT_MIN_WIN_RATE=0.40
BRAIN_ADULT_MIN_HOLDOUT_ACC=0.62
HYBRID_DISTILL_AUTO_FAST_PATH=false      # default after fix
HYBRID_DISTILL_REQUIRE_HOLDOUT=true
HALIM_PROMOTION_GATE=true
HALIM_PROMOTION_MIN_TOKEN_SCORE=3
PPO_REWARD_BALANCED_SAMPLE=true
PPO_REWARD_HIGH_VOL_FRACTION=0.30
```

### Verify
```bash
# Outcome teacher (no API)
python3 -c "
from core.ppo_teacher_training import trade_stats, _local_teacher_plan
from core.config import BotConfig
s = trade_stats(n=100)
print(_local_teacher_plan(s, BotConfig()).get('_source'))
"

# Proxy holdout train
python3 -c "from core.hybrid_distiller import train_teacher_proxy; from core.config import BotConfig; print(train_teacher_proxy(BotConfig()))"

# Brain stage uses holdout + rolling WR
PYTHONPATH=. python scripts/owned_brain_status.py

# Halim gate (requires MLX checkpoint + eval)
python3 halim/scripts/register_checkpoint.py toddler_v1
```

### Notes
`HALIM_PROMOTION_FORCE=true` or `HALIM_PROMOTION_GATE=false` bypasses Halim eval for hotfix only.

**Follow-up:** Added missing `import os` in `hybrid_distiller.py` (`_effective_proxy_accuracy` NameError).

---

## 2026-06-30 — Halim echo confidence parse crash (`0.54.`)

### Problem
`halim-entry-*` thread died on `ValueError: could not convert string to float: '0.54.'` in `_extract_echo_confidence`. KTTA then logged repeated await timeouts and `Halim empty` / `in_flight`.

### Root cause
Toddler LM echoed training lines with trailing punctuation after numeric confidence (`ppo_conf=0.54.`). Regex `[\d.]+` captured the extra dot; bare `float()` raised uncaught in `_run`.

### Fix
| File | Change |
|------|--------|
| `core/halim_entry_line.py` | `_safe_confidence_value()`; tighter numeric regex; try/except in `_run` |

### Verify
```bash
python3 -c "
from core.halim_entry_line import _parse_entry_lm_response
r = _parse_entry_lm_response('ppo_conf=0.54. ppo=hold entry_decision: skip')
print(r.get('confidence'), r.get('enter'))
"
```

### Notes
IB Error **201 closing-only** on CUPR is account/risk restriction — existing `parse_ib_order_block` + `_ai_skip_ticker_permanent` path applies on structural rejects; not a Halim bug.

---

## 2026-06-30 — Pre-market PreSubmitted entry timeouts (GVH)

### Problem
GVH (and ext-hours entries) polled 80/80 in `PreSubmitted` ~27s then `order_timeout`. Yesterday RTH fills worked; 07:25 ET pre-market did not.

### Root cause
1. `_entry_price_mode` forced `PAPER_MARKET_ENTRIES` → bare MARKET even outside RTH.
2. IB paper parent-only + `outsideRth` MARKET orders often never leave `PreSubmitted`.
3. Stuck recovery retried MARKET once (`market_retry_done`) then waited until poll cap — no limit chase.

### Fix
| File | Change |
|------|--------|
| `core/scalper_runner.py` | Ext-hours use `decide_smart_entry` limit; `_stuck_entry_limit_px`; up to `ENTRY_STUCK_MAX_RETRIES` limit retries |
| `core/config.py` | `ENTRY_STUCK_MAX_RETRIES` (default 2) |

### Env vars
```bash
ENTRY_STUCK_MAX_RETRIES=2
PENDING_SUBMIT_MAX_SEC=4
# Optional: force limit in RTH paper too
# PAPER_MARKET_ENTRIES=false
```

### Verify
Pre-market replay — entry log should show `ext_hours_limit_*` or `LIMIT@$…`, not bare `MARKET`. On stuck: `Stuck-entry retry GVH: limit@…`.

---

## 2026-06-30 — IB fill sync (phantom P&L vs IB account)

### Problem
Premarket logs showed large internal profits (~$100k+) while IB account showed losses. Bot NAV / unrealized P&L diverged from broker reality.

### Root cause
1. Entry fill detection treated **entire existing IB position** as a new fill (orphan paper holdings).
2. `_sync_all_positions_from_ib` could inflate slot shares to full IB size while keeping bot entry price.
3. Exit P&L credited from **quote fallback** after 8s without IB execution.
4. `bot_nav` / `day_pnl` used internal ledger, not IB NetLiquidation change.

### Fix
| File | Change |
|------|--------|
| `core/fill_tracker.py` | `confirm_entry_fill`, `ib_position_shares`, `require_ib_fill_sync`, `ib_fill_strict` |
| `core/scalper_runner.py` | Baseline position at order submit; IB-confirmed entry only; strict exit finalize; NAV sync to IB |
| `core/fill_reconciler.py` | No quote-force P&L when `IB_FILL_STRICT` |
| `core/position_intel.py` | IB-first shares/entry; display IB day P&L |
| `core/account_evaluator.py` | Day P&L from IB change when sync on |
| `core/config.py` | `REQUIRE_IB_FILL_SYNC`, `IB_FILL_STRICT`, `IB_FILL_FORCE_SEC` |

### Env vars
```bash
REQUIRE_IB_FILL_SYNC=true   # default
IB_FILL_STRICT=true         # no quote P&L booking
IB_FILL_FORCE_SEC=120
```

### Verify
- Entry log: `✅ IB entry confirmed TICKER: Nsh @ $X (order_status|position_delta|exec_cache)`
- Exit log: `📕 EXIT TICKER (IB fill):` — not `est. fill` unless strict off
- `/positions` shows `Day P&L` matching IB account change, not inflated unrealized

---

## 2026-06-30 — Defer git push during live session (performance)

### Problem
During premarket/RTH, logs showed `Push rejected — pull --rebase` and multi-repo `session_batch` pushes while entries/exits were active. Git commit/push/rebase competes with IB loop for disk, CPU, and network.

### Root cause
`GIT_PUSH_DURING_SESSION` defaulted **true** despite config comment saying defer. Batched checkpoint timer still flushed every ~180s with `force=True`, bypassing defer.

### Fix
| File | Change |
|------|--------|
| `core/config.py` | `GIT_PUSH_DURING_SESSION` default **false** |
| `core/git_sync.py` | `_git_session_push_enabled()`; no debounce flush when off; queue until shutdown |

### Env vars
```bash
GIT_PUSH_DURING_SESSION=false   # default — flush on stop_hanoon only
LEARNING_PUSH_ON_TRADE=true     # still queues; no push until shutdown
# Optional: ./scripts/stop_git_sync.sh during RTH
```

### Verify
Live session: no `session_batch` / `pull --rebase` logs during trading. On `stop_hanoon.sh`: `pre_shutdown` + full learning push once.

### Follow-up (same day)
`force=True` in `flush_batched_git_sync` still bypassed defer; stale debounce timers could fire. Gated `flush_batched_git_sync` + `push_learning_checkpoint(force)`; cancel timers on init; `START_GIT_SYNC_WITH_HANOON` default **false**.

---

## 2026-06-30 — Codebase organization & hygiene

### Problem
Scattered env defaults, triple accounting confusion, runtime jsonl in git, no unit tests, monolithic scalper, duplicate launcher exports.

### Fix
| Area | Change |
|------|--------|
| `core/account_view.py` | IB-grounded equity / day P&L |
| `core/entry_pipeline.py` | Extracted IB entry fill confirmation |
| `scripts/start_hanoon.sh` | Deduped `PPO_LEAD` / `TRAILING_PROFIT` exports; git/IB sync env |
| `tests/` | pytest for fills, git defer, account_view |
| `.gitignore` | Runtime journals + session state local-only |
| `archive/replay_live_runner.py` | Deprecated (use `replay_scalper_runner`) |
| `docs/OPS.md`, `ARCHITECTURE.md`, `models/README.md`, `GIT_SYNC.md` | Updated |
| `main.py` | `--port` inherits `BotConfig.IB_PORT` (4002) |
| `core/config.py` | `SMART_STACK`, `RAM_LIVE_ONLY` in BotConfig |
| `core/capital_discipline.py` | Fix PPO_LEAD getattr default |

### Verify
```bash
python3 -m pytest tests/ -q
grep -c PPO_LEAD_WHILE scripts/start_hanoon.sh  # expect 1
```

---

## 2026-06-30 — War OBSERVE message + Telegram NAV baseline

### Problem
War log said "capital dry/settled out" when trip cap was hit ($3,469 settled but 5/5 trips). Telegram/session close used `bot_nav`≈IB $982k and `INITIAL_CASH` was overwritten with IB equity, breaking Day P&L baseline.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `_observe_block_reason()` — trip cap vs settled; RLock on state; clearer startup log |
| `core/scalper_runner.py` | Stop poisoning `INITIAL_CASH`; `_notify_context` uses `account_view`; session close shows IB + war pool |

### Verify
Restart — Telegram startup should show IB equity; war veto should say `trip cap 5/5` not `capital dry`.

---

## 2026-06-30 — Regime always unknown on live spikes

### Problem
Logs and telemetry showed `regime=unknown` on most spikes. `MarketRegimeDetector` required 50 bars but live path only had 10–20 stream bars.

### Root cause
`classify()` returned `UNKNOWN` when `len(df) < 50`. Scalper spike/entry paths used raw enum value without spike-ratio fallback.

### Fix
| File | Change |
|------|--------|
| `core/market_regime.py` | `_classify_short()` for 5–49 bars; `resolve_regime()`; `regime_from_macro()` |
| `core/trade_telemetry.py` | `regime_tag()` treats UNKNOWN as missing; maps bear/gap labels |
| `core/scalper_runner.py` | Spike/entry/AI context use `resolve_regime()` |
| `core/consciousness.py` | Macro SPY/VIX regime instead of `classify(None)` |
| `core/trading_copilot.py` | `_infer_regime_read()` — no bare unknown in briefs |
| `tests/test_regime_resolve.py` | Short-bar + spike fallback tests |

### Verify
```bash
venv/bin/pytest tests/test_regime_resolve.py -q
# Spike logs should show momentum_spike / trend_grind / high_vol_spike — not unknown
```

---

## 2026-06-30 — War auto-reset at RTH open (ET)

### Problem
Premarket exhausted war/lab round-trip caps (5/5 + 4/4); RTH spikes blocked in OBSERVE despite settled cash remaining. User had to manually `reset_live_war_session` each morning.

### Root cause
`_roll_session` only reset on **calendar day** change (midnight), not at **09:30 ET** RTH open on the same day.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `_roll_rth_session()` — fresh capital + zero trips at first RTH check each ET day |
| `scripts/start_hanoon.sh` | `WAR_AUTO_RESET_AT_RTH=true` (paper $3.5k; live uses `WAR_LIVE_OPERATING_CAPITAL` $1k) |
| `tests/test_war_account_rth.py` | Unit tests for RTH roll |

### Env
```bash
WAR_AUTO_RESET_AT_RTH=true          # default
WAR_CAPITAL_USD=3500                # paper (change to 1000 via WAR_LIVE_OPERATING_CAPITAL when live)
WAR_LIVE_OPERATING_CAPITAL=1000     # set when going live
```

### Verify
```bash
venv/bin/pytest tests/test_war_account_rth.py -q
# At 09:30 ET log: ⚔️ War account RTH reset (ET) — mode=WAR_ACTIVE nav=$3,500 ...
```

---

## 2026-06-30 — Structural module extractions (final org pass)

### Problem
`scalper_runner.py` and `git_sync.py` still monolithic; entry poll state duplicated; defer policy buried in 2.5k-line git module.

### Fix
| File | Change |
|------|--------|
| `core/git_sync_defer.py` | Session defer policy, checkpoint queue, replay batching |
| `core/git_sync.py` | Re-exports defer API; registers shutdown flush hook |
| `core/position_sync.py` | `repair_slot_entry_price`, `sync_position_slots_from_ib` |
| `core/entry_pipeline.py` | `new_entry_poll_state`, `entry_price_mode_for_session`, `stuck_entry_limit_px` |
| `core/scalper_runner.py` | Delegates to extracted modules (no behavior change) |
| `docs/CLEANUP_AND_ORGANIZATION_2026-06-30.md` | Complete session cleanup report |

### Verify
```bash
venv/bin/pytest tests/ -q
python3 -c "from core.git_sync_defer import should_defer_git_push; from core.position_sync import sync_position_slots_from_ib"
python3 -c "from core.entry_pipeline import entry_price_mode_for_session, stuck_entry_limit_px"
```

---

## 2026-06-30 — War replay ledger isolation (code)

### Problem
Replay sessions could still increment live `round_trips_today` when `REPLAY_RELAX_WAR=false`; git sync committed the journal before guards shipped.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `is_replay_session()`; replay always disables war; `save_state`/`_append_ledger` no-op; `reset_live_war_session()` |

### Verify
```bash
REPLAY_LIVE=true python3 -c "from core.war_account import war_account_enabled; assert not war_account_enabled()"
```

---

## 2026-06-30 — Halim live entry ship + replay API budgets

### Problem
Halim entry line did not participate reliably on live/replay: LM outputs often unparseable, coevolution `halim_signal` null on success paths, decision API cap too low for replay training, health monitor wrong path.

### Root cause
Missing live await + echo parser; verdict dict dropped blend stamps; `brain_maturity` caps; monitor hit `/health` not `/v1/health`.

### Fix
| File | Change |
|------|--------|
| `core/halim_entry_line.py` | Live/replay await, JSON prompt, training-echo parser, supersede rings |
| `core/ai_commander.py` | `_entry_verdict()` stamp merge on all finalize paths; INFO await outcomes |
| `core/halim_ppo_coevolution.py` | `merge_coevolution_stamps()`, `enrich_decision_halim_peek()` |
| `core/brain_maturity.py` | `REPLAY_DECISION_API_DAILY=48`, `LIVE_DECISION_API_DAILY=16` |
| `scripts/halim_env.sh` | `HALIM_ENTRY_AWAIT_SEC`, live await, LM timeout/token limits |
| `scripts/monitor_replay_health.sh` | `/v1/health` fallback after `/health` |

### Env
- `HALIM_ENTRY_AWAIT_SEC=4.5`, `HALIM_ENTRY_AWAIT_LIVE=true`
- `REPLAY_DECISION_API_DAILY=48`, `LIVE_DECISION_API_DAILY=16`

### Verify
```bash
rg "Halim entry fresh|\\+halim\\+" logs/REPLAY_SCALPER.log | tail -20
./scripts/monitor_replay_health.sh
```

---

## 2026-06-30 — Replay cannot touch live war ledger + session reset

### Problem
Overnight replay incremented `round_trips_today` to 18 on the live war account, blocking paper entries after user had stopped HANOON before prior close. `REPLAY_RELAX_WAR=false` could re-enable war during replay.

### Root cause
War ledger only skipped when `REPLAY_RELAX_WAR=true`; replay exits could still call `save_state` / `record_exit` if env toggled. Stale session state persisted on disk.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `is_replay_session()`; replay always disables war; `save_state`/`_append_ledger` no-op in replay; `reset_live_war_session()` |

### Verify
```bash
python3 -c "from core.config import BotConfig; from core.war_account import reset_live_war_session, is_replay_session; print(reset_live_war_session(BotConfig(), reason='verify'))"
REPLAY_LIVE=true python3 -c "from core.war_account import war_account_enabled; print(war_account_enabled())"
```

---

## 2026-06-30 — IB connectivity cleanup + detailed journal

### Problem
Redundant connectivity state (`_ib_connectivity_waiting` + outage flags), duplicate Telegram on restore (`reconnect_event` + `connectivity_restored`), watchdog notify path unused, anti-flap could return `False` and kill HANOON during outage, sparse reconnect failure logs.

### Root cause
Layered flags in scalper vs connector; legacy `reconnect_event`; anti-flap short-circuit without sleep.

### Fix
| File | Change |
|------|--------|
| `core/ib_connectivity_journal.py` | **New** — `models/ib_connectivity.jsonl` + structured `HANOON.log` lines for every outage/retry/restore |
| `core/connector.py` | Single outage flag; per-attempt journal; anti-flap sleeps; removed `reconnect_event`; trigger tags (`ib_1100`, `disconnected_event`, …) |
| `core/notify.py` | Removed `reconnect_event`; restore message includes duration + attempt count |
| `core/scalper_runner.py` | Dropped `_ib_connectivity_waiting`; entry guard uses `conn.in_connectivity_outage()` only; position-count log on disconnect |
| `core/config.py` | (unchanged in cleanup pass — see wait-mode entry below) |
| `scripts/start_hanoon.sh` / `scripts/stop_hanoon.sh` | (unchanged in cleanup pass — see wait-mode entry below) |
| `scripts/ib_gateway_watchdog.py` | Log-only; journal `gateway_port_down/up`; periodic OK heartbeat |

### Verify
```bash
tail -f logs/HANOON.log | rg "IB connectivity"
tail -f models/ib_connectivity.jsonl
```

---

## 2026-06-30 — IB connectivity wait mode + Gateway watchdog

### Problem
Live/paper HANOON exited after `RECONNECT_MAX_ATTEMPTS` (10) when Wi‑Fi or IB Gateway dropped — killing in-memory Halim/PPO state even though open positions and bracket stops remain on IB servers.

### Root cause
`IBConnector.reconnect()` capped attempts and `ScalperRunner` main loop `break` on failure. No sidecar monitored Gateway port; Telegram fired per final failure only.

### Fix
| File | Change |
|------|--------|
| `core/config.py` | `CONNECTIVITY_WAIT_ON_IB_LOSS`, `RECONNECT_MAX_ATTEMPTS_LIVE` (0=infinite), `RECONNECT_WAIT_LOG_EVERY`, `IB_GATEWAY_WATCHDOG_ENABLED` |
| `core/connector.py` | Infinite live reconnect with interruptible sleep (respects shutdown); `_mark/_clear_connectivity_outage()`; one-shot outage/restore via notifier |
| `core/notify.py` | `connectivity_lost()`, `connectivity_restored()` — one Telegram per outage cycle |
| `core/scalper_runner.py` | Connectivity wait flag; no entries while outage; resubscribe after reconnect |
| `scripts/ib_gateway_watchdog.py` | Sidecar TCP probe on `IB_HOST:IB_PORT`; writes `runtime/ib_gateway_down.flag` |
| `scripts/start_ib_gateway_watchdog.sh` | Start watchdog (log-only notify by default) |
| `scripts/stop_ib_gateway_watchdog.sh` | Stop watchdog on graceful HANOON shutdown |
| `scripts/start_hanoon.sh` | Export wait-mode env; start IB Gateway watchdog with Halim watchdog |
| `scripts/stop_hanoon.sh` | Stop IB Gateway watchdog |

### Env
- `CONNECTIVITY_WAIT_ON_IB_LOSS=true` (live default)
- `RECONNECT_MAX_ATTEMPTS_LIVE=0` (infinite)
- `RECONNECT_WAIT_LOG_EVERY=10`
- `IB_GATEWAY_WATCHDOG_ENABLED=true`
- `IB_GATEWAY_WATCHDOG_OK_EVERY=20` (watchdog heartbeat log interval)

### Verify
```bash
python3 -c "
from core.config import BotConfig
from core.connector import IBConnector
c = BotConfig()
print('wait', c.CONNECTIVITY_WAIT_ON_IB_LOSS, 'live_max', c.RECONNECT_MAX_ATTEMPTS_LIVE)
conn = IBConnector(c)
print('max_attempts', conn._reconnect_max_attempts())
"
./scripts/start_ib_gateway_watchdog.sh
tail -3 logs/ib_gateway_watchdog.log
./scripts/stop_ib_gateway_watchdog.sh
```

---

## 2026-06-30 — PPO training-echo entry parser

### Problem
After JSON prompt fix, 0% LM ready — model regurgitates gold lines (`PPO-led micro-fast: score=84 | ATR R:R 2.0`, `ppo=hold conf=0.50`) instead of JSON. All awaits ended `empty`; no `+halim+` blend.

### Root cause
Toddler SFT heavily trained on PPO/council `reason` strings; inference copies those tokens. Prior parser only handled instruction-echo and strict JSON.

### Fix
| File | Change |
|------|--------|
| `core/halim_entry_line.py` | `_parse_training_echo_entry()`, `_extract_echo_confidence()` — detect PPO/ATR/entry_decision field echoes; default `enter=false` for `ppo-led micro-fast` gold copy; `ppo_buy`/`ppo=hold` explicit; log `ready (echo)` |

### Verify
```bash
python3 -c "
from core.halim_entry_line import _parse_entry_lm_response
samples = [
  'PPO-led micro-fast: score=80 ppo=hold | ATR R:R 2.0',
  '} ppo=hold conf=0.50 note=Low confidence',
  'COIN entry_decision: price=179.85 ppo_buy=False ppo_conf=0.54',
]
for s in samples: print(_parse_entry_lm_response(s))
"
```

---

## 2026-06-30 — Entry LM parse quality + coevolution halim_signal stamps

### Problem
~67% Halim entry LM outputs were unparseable toddler ramble (`Entry_decision is not a signal…`). Coevolution v2 rows stayed `halim_signal=null` even when pipeline showed `+halim+` because `_finalize_entry_decision` built a fresh verdict dict without copying `halim_enter` stamps.

### Root cause
1. Prompt echoed schema template (`enter=true|false`) which the 0.5B model repeated as prose.
2. Parser only handled strict JSON; instruction-echo lines were discarded.
3. Success-path `decision = {enter, shares, …}` dropped blend stamps before `_emit_spike_verdict` → `extract_coevolution_halim_signals` saw no `halim_enter`.
4. Zero confidence from partial parses displayed as `0%`.

### Fix
| File | Change |
|------|--------|
| `core/halim_entry_line.py` | Shorter JSON-only prompt; `_normalize_entry_parsed()`; embedded-JSON + instruction-echo heuristics; conf floor 0.45/0.55 when model returns 0 |
| `core/halim_ppo_coevolution.py` | `merge_coevolution_stamps()`, `enrich_decision_halim_peek()`, `COEVOLUTION_STAMP_KEYS` |
| `core/ai_commander.py` | `_entry_verdict()` merges stamps into all verdict payloads; `_emit_spike_verdict` peeks Halim slot when stamps missing |
| `scripts/halim_env.sh` | `HALIM_ENTRY_MAX_TOKENS=36` (shorter generation) |

### Verify
```bash
python3 -c "
from core.halim_entry_line import _parse_entry_lm_response
s='Entry_decision only on clean momentum scalp. False on chop/fakeout.'
print(_parse_entry_lm_response(s))
"
# Restart replay — coevolution tail should show halim_signal true/false not null
grep halim_signal halim/data/coevolution/correction_log.jsonl | tail -5
```

---

## 2026-06-30 — Halim entry await replay + live (participation fix)

### Problem
Halim entry LM never showed `Halim entry fresh` in replay: 0% blend, coevolution v2 `halim_signal=null`. Cloud teacher blocked by `daily_decision_cap_1`. Monitor reported `halim_serve=no` (wrong health URL). Live had `HALIM_ENTRY_AWAIT_LIVE=false` so fast paths skipped await entirely.

### Root cause
1. `HALIM_ENTRY_AWAIT_SEC=2.5` too short for MLX on M2 8GB → silent timeout (DEBUG only).
2. `ring()` dropped new spikes when prior slot `in_flight` → await got `wrong_fp` immediately.
3. Adult stage × proxy multiplier → decision API budget floor of 1/day; sample_skip throttled council.
4. Live await disabled by default in `halim_env.sh`.
5. Monitor probed `/v1/health`; serve exposes `/health`.

### Fix
| File | Change |
|------|--------|
| `scripts/halim_env.sh` | `HALIM_ENTRY_AWAIT_SEC=4.5`, `HALIM_ENTRY_AWAIT_LIVE=true`, `HALIM_ENTRY_AWAIT_ENABLED`, LM timeout 8s, min ring 1s, max age 6s; `REPLAY/LIVE_DECISION_API_DAILY` floors; council sample off in training unless `*_COUNCIL_SAMPLE=true` |
| `core/halim_entry_line.py` | Unified await for replay+live; supersede in-flight ring on new fingerprint; await polls through supersede; INFO logs for LM ready/empty/unparseable; `_parse_entry_lm_response()` heuristic fallback when MLX ramble ≠ JSON |
| `core/ai_commander.py` | INFO logs for all await outcomes (`timeout`, `empty`, `wrong_fp`, `missing`) |
| `core/brain_maturity.py` | `_training_session_decision_floor()`, `_decision_sample_throttle_enabled()` — replay/live gold get higher API budget, no sample_skip by default |
| `scripts/monitor_replay_health.sh` | Try `/health` then `/v1/health` |

### Env vars
```bash
HALIM_ENTRY_AWAIT_ENABLED=true
HALIM_ENTRY_AWAIT_SEC=4.5
HALIM_ENTRY_AWAIT_REPLAY=true
HALIM_ENTRY_AWAIT_LIVE=true          # live scalper now waits for Halim LM too
HALIM_ENTRY_LM_TIMEOUT_SEC=8
HALIM_ENTRY_LM_MIN_RING_SEC=1.0
REPLAY_DECISION_API_DAILY=48
LIVE_DECISION_API_DAILY=16
REPLAY_COUNCIL_SAMPLE=false          # set true to re-enable sample_skip in replay
LIVE_COUNCIL_SAMPLE=false
```

### Verify
```bash
source scripts/halim_env.sh
python3 -c "from core.halim_entry_line import halim_entry_await_sec; from core.config import BotConfig; print('replay', halim_entry_await_sec())"
REPLAY_LIVE= python3 -c "import os; os.environ['HALIM_ENTRY_AWAIT_LIVE']='true'; from core.halim_entry_line import halim_entry_await_sec; print('live', halim_entry_await_sec())"
curl -sf http://127.0.0.1:8765/health | head -1
# Restart replay or live scalper — log should show Halim entry fresh / await timeout / LM ready
grep -E 'Halim entry (fresh|await|LM)' logs/REPLAY_SCALPER.log | tail -20
```

### Follow-ups
- If timeouts dominate, bump `HALIM_ENTRY_AWAIT_SEC` to 5.5 on M2 8GB.
- Restart replay/live session required for env + code to load.

---

## 2026-06-30 — Descriptive auto-commit + journal repair

### Problem
Git sync auto-commits used opaque messages (`auto: 82 change(s) — file.csv`). War-relax fix log entry lost its `##` header. Auto commits could conflict with manual-only journal hook.

### Root cause
`git_sync` watcher used basename preview only; no brain/session context. Header dropped during insert of Halim participation entry. Hook had no exemption for `git_sync` learning/shutdown pushes.

### Fix
| File | Change |
|------|--------|
| `core/git_sync.py` | `_brain_snapshot_line`, `_summarize_changed_files`, `_enrich_commit_message`, `_record_auto_commit_in_brain_log`; descriptive shutdown/learn/auto messages; `GIT_SYNC_AUTO_COMMIT=1` on subprocess commit |
| `scripts/git-hooks/pre-commit` | Skip journal check when `GIT_SYNC_AUTO_COMMIT=1` |
| `docs/ENGINEERING_FIX_LOG.md` | Restored war-relax `##` header |
| `docs/BRAIN_DEVELOPMENT_LOG.md` | Auto-append line on git shutdown/training/auto commits |

### Env vars
```bash
GIT_SYNC_AUTO_COMMIT=1   # set by git_sync only — not for manual use
GIT_BATCH_CHECKPOINTS=true
OWNED_BRAIN_GIT_PUSH=true
```

### Verify
```bash
# After replay teardown — commit message should include brain= stage, artifact buckets
tail -3 docs/BRAIN_DEVELOPMENT_LOG.md
tail -5 logs/git_sync_journal.jsonl
git log -1 --format=%B
```

### Notes
Manual `core/` commits still require fix-log entry. Learning/shutdown auto pushes include `docs/ENGINEERING_FIX_LOG.md` in artifact list when changed.

---

## 2026-06-30 — Forced fix journaling (git hook + Cursor)

### Problem
Fixes were landing in code without a durable audit trail — easy to repeat mistakes or lose verify steps.

### Root cause
Journal was optional prose in chat and a soft cursor rule only; nothing blocked commits.

### Fix
| File | Change |
|------|--------|
| `docs/ENGINEERING_FIX_LOG.md` | Canonical fix log (this file) |
| `scripts/git-hooks/pre-commit` | Fail commit if stack paths change without new `## YYYY-MM-DD` section in this log |
| `scripts/install_git_hooks.sh` | Copies hook into `.git/hooks/pre-commit` |
| `.cursor/hooks.json` | `afterFileEdit` → `require_fix_journal.sh` |
| `.cursor/hooks/require_fix_journal.sh` | Injects mandatory journal reminder on stack edits |
| `.cursor/rules/smart-stack-vision.mdc` | Fix journal section |

### Env vars
```bash
SKIP_FIX_JOURNAL=1   # emergency bypass only — add journal entry immediately after
```

### Verify
```bash
./scripts/install_git_hooks.sh
# Should pass: journal + stack file staged together
# Should fail: stack file only, no new ## section in journal
```

### Notes
Run `./scripts/install_git_hooks.sh` once per clone (or after fresh clone). Hook is not in `.git/hooks` itself (git does not track that dir).

---

## 2026-06-30 — Halim participation + proxy balance + repeat-loser quality

### Problem
PPO `micro_fast` won every entry race before Halim LM returned (`Halim empty`/`in_flight`). Teacher proxy retrain failed `single_class` (all enter labels). Repeat losers (NVDA, PLTR, etc.) kept re-entering on weak micro-fast setups (~12% WR).

### Root cause
1. No await between `_ring_halim_entry` and fast paths — blend never saw `fresh`.
2. Proxy training used `ai_decision_log` enters only; skip verdicts not included.
3. `assess_entry_quality` ignored per-ticker session loss memory on micro-fast.

### Fix
| File | Change |
|------|--------|
| `core/halim_entry_line.py` | `halim_entry_await_sec()`, `wait_for_completion()` |
| `core/ai_commander.py` | `_await_halim_entry_slot()` before `micro_fast`/`spike_fast`; pass `ticker` to quality |
| `core/fast_execution.py` | `ticker` on `should_micro_fast_entry`; repeat-loser quality gate |
| `core/entry_quality.py` | `repeat_loser_prob_bump()`, `ticker` param on `assess_entry_quality` |
| `core/hybrid_distiller.py` | `_load_skip_verdicts()` from `smart_stack_verdicts.jsonl`; merge enter+skip for proxy train |
| `scripts/halim_env.sh` | `HALIM_ENTRY_AWAIT_*`, `REPEAT_LOSER_*` defaults |

### Env vars
```bash
HALIM_ENTRY_AWAIT_SEC=2.5          # replay: wait for Halim JSON before fast path
HALIM_ENTRY_AWAIT_REPLAY=true
HALIM_ENTRY_AWAIT_LIVE=false       # keep live snappy
REPEAT_LOSER_PROB_BUMP=true
REPEAT_LOSER_MICRO_FAST_GATE=true
```

### Verify
```bash
source scripts/halim_env.sh && ./scripts/start_replay_live.sh
grep "Halim entry fresh" logs/REPLAY_SCALPER.log
grep -E 'halim_complement|halim_veto' logs/REPLAY_SCALPER.log
./scripts/coevolution_status.sh   # v2 rows with halim_signal set
# After teardown — proxy train should not say single_class:
python3 -c "from core.hybrid_distiller import train_teacher_proxy; from core.config import BotConfig; print(train_teacher_proxy(BotConfig()))"
```

### Notes
Sniper flash/strong paths unchanged (no await). Increase `HALIM_ENTRY_AWAIT_SEC` if still mostly timeout on M2 8GB.

---

## 2026-06-30 — Replay war relax (entries blocked on $1k replay)

### Problem
Replay scanned spikes but **zero trades**: every entry logged `war:veto` — `LAB_ACTIVE: need $3,495 > settled/bullet ($2,500)`. Bracket notional exceeded lab bullet on $1,000 replay cash.

### Root cause
`war_account.check_entry_allowed()` ran during replay with war enabled. ATR bracket sized ~$3.5k while lab settled cap was $2.5k.

### Fix
- `core/war_account.py` — `war_account_enabled()` returns `False` when `REPLAY_LIVE=true` and `REPLAY_RELAX_WAR=true` (default).
- `scripts/start_replay_live.sh` — `export REPLAY_RELAX_WAR="${REPLAY_RELAX_WAR:-true}"`.

### Verify
```bash
grep war:veto logs/REPLAY_SCALPER.log | tail   # should stop after restart
grep "REPLAY ENTRY" logs/REPLAY_SCALPER.log | tail
```
Live HANOON unchanged — war still active when `REPLAY_LIVE` is not set.

### Notes
Requires **replay restart** after deploy. Confirmed working 2026-06-30 ~02:08 ET: NVDA/ASTS/PLTR entries after restart.

---

## 2026-06-30 — PPO↔Halim coevolution honest labels + complement

### Problem
Coevolution stats showed **3675 “correct PPO” vs 2 “correct Halim”** — training gold implied Halim was always right and PPO always wrong. Companion/PPO mutual learning was skewed.

### Root cause (two bugs)
1. **`ai_commander._record_council_learning`:** When `halim_enter`/`halim_exit` absent, set `halim_signal = decision.enter` (final execute bit) — Halim always matched execution.
2. **`record_coevolution`:** Fallback `halim_signal if not None else executed` doubled the effect.
3. **Learning only on bracket-success path** — most `_finalize_entry_decision` exits never called `_record_council_learning`.

### Fix

| File | Change |
|------|--------|
| `core/halim_ppo_coevolution.py` | `extract_coevolution_halim_signals()` — independent signals only (halim_lm, proxy, council, quality); never final enter/exit |
| | `label_version: 2` on new correction_log rows |
| | `_legacy_mislabeled()` — skip pre-v2 rows where `halim_source=halim` + `correction_for=ppo` in gold export |
| | `attach_trade_outcome()` — `market_proved=ppo\|halim` + experience_buffer weight |
| | Two-way gold: `coevolution_halim_corrected` when `correction_for=halim` |
| | `coevolution_status_report()` + enhanced `coevolution_stats()` |
| `core/ai_commander.py` | Use `extract_coevolution_halim_signals`; stamp `proxy_enter/conf/reason` on proxy path |
| | `_entry_quality_snapshot` → `quality_enter/conf/reason` on decisions |
| | `_stamp_council_signals()` — preserve `council_enter` before merge |
| | `_emit_spike_verdict()` calls `_record_council_learning` on **all** entry paths; removed duplicate call |
| | Pass `ppo_reason` into all `_emit_spike_verdict` calls |
| `core/halim_entry_line.py` | `HALIM_PPO_COMPLEMENT` — PPO HOLD + Halim enter (≥80% min_conf) can set enter + `:halim_complement` pipeline |
| `scripts/halim_env.sh` | `HALIM_PPO_COMPLEMENT=true` |
| `scripts/coevolution_status.sh` | Human + `--json` status (all-time vs label v2) |
| `scripts/monitor_replay_health.sh` | 45s replay/halim/v2 monitor → `logs/replay_monitor.log` |
| `scripts/verify_full_stack.sh` | Runs `coevolution_status.sh` |

### Env vars
```bash
HALIM_PPO_COMPLEMENT=true          # PPO HOLD + Halim enter complement path
HALIM_PPO_COEVOLUTION=true       # master switch (existing)
```

### Verify
```bash
./scripts/coevolution_status.sh
# SINCE LABEL v2 should grow; correction_for should NOT be 99% ppo
# Recent rows: halim=None + src=unknown is OK for ppo:micro_fast-only paths
grep halim_complement logs/REPLAY_SCALPER.log
tail -f logs/replay_monitor.log
```

### Observed after replay restart (2026-06-30)
- 8+ `label_version=2` rows in first minutes
- `halim=None`, `correction_for=none` on `ppo:micro_fast` entries — **correct** (no false Halim credit)
- Entries flowing after `REPLAY_RELAX_WAR` restart

---

## 2026-06-30 — Halim companion voice pipeline (0.5B MLX)

### Problem
Toddler companion output looped (`"shorterishish…"`, `COMPANIONITY:`, prompt echo, training-format leak). 0.5B on M2 8GB cannot hold dense persona + math + long generation.

### Root cause
1. System prompt sent **twice** (`build_companion_context` + `_companion_generate` `system=`).
2. Chat used `HALIM_MAX_TOKENS=512`, `HALIM_TEMPERATURE=0.7` (entry/exit already had tight caps).
3. Bad outputs journaled to `conversation_gold.jsonl` (poisoning retrain).
4. RAG injected raw HTML from `learn_cache`.
5. Native LM success blocked council fallback even when output was garbage.

### Fix

| File | Change |
|------|--------|
| `halim/halim/engine.py` | Chat purposes: `HALIM_CHAT_MAX_TOKENS` (72), `HALIM_CHAT_TEMPERATURE` (0.28); notify: 120 / 0.35 |
| `scripts/halim_env.sh` | Defaults for chat/notify/companion/RAG caps |
| `core/halim_companion.py` | Removed persona from `build_companion_context` (persona once via `system=`) |
| | `companion_output_ok()`, `companion_gold_journalable()` — reject loops/echo/leak |
| | `_companion_generate` — reject bad native → council teacher |
| `core/halim_learn_rag.py` | `sanitize_learn_text()`; default `HALIM_LEARN_RAG_MAX_CHARS=1200` |
| `core/halim_web_learn.py` | Fetch path uses `sanitize_learn_text` |
| `core/halim_commander_report_learn.py` | Sanitize on cache write |
| `core/halim_chat.py` | Require `ok=True` from companion; final path runs output guard |

### Env vars
```bash
HALIM_CHAT_MAX_TOKENS=72
HALIM_CHAT_TEMPERATURE=0.28
HALIM_NOTIFY_MAX_TOKENS=120
HALIM_NOTIFY_TEMPERATURE=0.35
HALIM_COMPANION_MAX_CHARS=400
HALIM_LEARN_RAG_MAX_CHARS=1200
HALIM_LEARN_RAG_MAX_DOCS=2
```

### Verify
```bash
source scripts/halim_env.sh && ./scripts/halim_stop.sh && ./scripts/halim_start.sh
./scripts/halim_chat.sh "status"   # off-hours or replay stopped (HALIM_CHAT_DURING_TRADING=false)
```

### Not changed (by design)
- Model size stays **0.5B MLX** on `m2_8gb` — see `docs/HALIM_MAC_INFERENCE.md`
- `HALIM_CHAT_DURING_TRADING=false` — companion deprioritized during live/replay trading

---

## 2026-06-30 — Auto LM retrain JSON parse

### Problem
`models/halim_lm_evolve_state.json` showed `train.ok: false` despite MLX LoRA completing (`"ok": true` in stdout tail).

### Root cause
`halim_auto_lm._parse_json_stdout()` only parsed single-line JSON; `train_toddler.py` prints progress bars then **multiline** JSON object.

### Fix
- `core/halim_auto_lm.py` — parse from last `{` in stdout; success if `parsed.ok` or `parsed.checkpoint` present.

### Verify
```bash
./scripts/halim_auto_evolve_lm.sh --force
cat models/halim_lm_evolve_state.json | jq '.last_outcome.steps.train.ok'
```

---

## Template (copy for next fix)

```markdown
## YYYY-MM-DD — Short title

### Problem
What broke or what symptom triggered the change?

### Root cause
Why it happened (file, logic, config — be specific).

### Fix
| File | Change |
|------|--------|
| `path` | … |

### Env vars (if any)
\`\`\`bash
VAR=value
\`\`\`

### Verify
\`\`\`bash
commands to confirm fix
\`\`\`

### Rollback / risks
What to watch if this causes regressions.

### Notes
Session context, links, follow-ups.

---

## 2026-07-01 — War RTH reset spam + profit-hunt NameError

### Problem
War account RTH reset logged dozens of times per session (every spike/context read). Position monitor spammed `name 'check_missed_profit_hunt' is not defined` (4000+ ERROR lines) — exits on BITO/TZA broken.

### Root cause
`_roll_rth_session` set `rth_rolled_date` in memory but `war_account_context` / `war_account_state` called `_roll_session` without `save_state`, so the next read re-triggered RTH reset. `scalper_exit_executor` mixin imports from `scalper_mixin_imports` which omitted `check_missed_profit_hunt` (only imported in `scalper_runner.py`).

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `_today_key()` uses `now_et()` only (no UTC fallback); `_roll_session` persists state after calendar/RTH roll; calendar midnight roll logs explicitly |
| `core/scalper_mixin_imports.py` | Export `check_missed_profit_hunt` for exit mixin |

### Env vars (if any)
```bash
WAR_AUTO_RESET_AT_RTH=true   # RTH pool refresh at 09:30 ET only
TZ=America/New_York          # set by main.py + start_hanoon.sh
```

### Verify
```bash
pytest tests/test_war_account_rth.py -q
# Restart bot; one RTH reset per ET day max; no check_missed_profit_hunt errors
grep -c "check_missed_profit_hunt" logs/HANOON.log  # should stop growing after restart
```

### Rollback / risks
Auto-save inside `_roll_session` adds one disk write per roll event (low frequency). Calendar roll now logs at ET midnight — expected once per US session day.

### Notes
War balance boundaries are **US Eastern**: calendar counters at **00:00 ET** (10:00 BDT), pool refresh at **09:30 ET** RTH open (19:30 BDT). Midnight BDT = 14:00 ET — not a war reset boundary.

---

## 2026-07-01 — Multi-position monitor bleed + LIVE_PULSE entry desync

### Problem
With multiple open positions, BITO showed +$0.21 on LIVE_PULSE then exited at -$0.21 via mechanical `trailing_stop`. AAL pulse line showed BITO stop/TP and fake +128% P&L — AAL's price was evaluated against BITO's stale `risk.plan`.

### Root cause
`_load_position_context` only called `risk.open_position` when `_risk_plans[ticker]` existed; otherwise BITO's plan stayed active. `_save_position_context` could write AAL's transient state into BITO's slot if `current_ticker` drifted. LIVE_PULSE used planned `entry_price` instead of `entry_fill_px`.

### Fix
| File | Change |
|------|--------|
| `core/position_context.py` | `slot_entry_price`, `bind_risk_plan_for_ticker`, `risk_plan_sane_for_tick` |
| `core/scalper_runner.py` | Delegates to position_context; load uses fill px; save guarded by `current_ticker` |
| `core/scalper_exit_executor.py` | `try/finally` save in monitor loop; `_risk_plan_sane_for_tick` gate before `evaluate_tick` |
| `core/scalper_entry_executor.py` | Persist `risk_usd` + `atr_at_entry` on slot for plan rebuild |

### Env vars (if any)
None — behavior fix only.

### Verify
```bash
pytest tests/test_position_context_isolation.py -q
# restart bot to pick up check_missed_profit_hunt import fix from prior entry
```

### Notes
Restart HANOON after deploy.

---

## 2026-07-01 — War ledger multi-position + deploy budget import hardening

### Problem
With 2+ open positions, `open_war` was a single slot — second entry overwrote first. Exiting TZA used BITO's entry → phantom -$3212 war PnL while IB showed ~$0. `get_ai_deploy_budget` NameError on some entry paths after mixin split.

### Root cause
`record_entry` assigned `state["open_war"] = {...}` (one ticker). `record_exit` used that slot even when `ticker` didn't match. Mixin star-imports weren't always visible to all entry code paths.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `open_wars`/`open_labs` per-ticker dicts; `_resolve_open_slot`; exit uses matching ticker or `entry_ib_fill` fallback |
| `core/scalper_exit_executor.py` | Pass `entry_ib_fill` to `record_exit` |
| `core/scalper_entry_executor.py`, `core/ai_commander_entry.py` | Explicit `get_ai_deploy_budget` import |

### Verify
```bash
pytest tests/test_war_multi_position.py tests/test_position_context_isolation.py tests/test_war_account_rth.py -q
```

### Rollback / risks
Legacy `open_war`/`open_lab` mirrored for old readers; migration runs on `load_state`.

---

## 2026-07-01 — risk_plan_sane_for_tick mixin import

### Problem
Post-restart monitor spammed `name 'risk_plan_sane_for_tick' is not defined` on BITO+TZA — profit booking still blocked.

### Root cause
`scalper_exit_executor` calls `risk_plan_sane_for_tick` from `position_context` but `scalper_mixin_imports` did not export it.

### Fix
| File | Change |
|------|--------|
| `core/scalper_mixin_imports.py` | Export `risk_plan_sane_for_tick`, `bind_risk_plan_for_ticker`, `slot_entry_price` |

### Verify
```bash
pytest tests/test_position_context_isolation.py -q
grep risk_plan_sane logs/HANOON.log  # should not grow after restart
```

---

## 2026-07-01 — Mid-session git push + IB position recovery on restart

### Problem
`logs push success: trade: BUY` during RTH wasted CPU/network. `risk_plan_sane_for_tick` blocked monitor. After restart, open IB positions (BITO/TZA) not adopted — no live management until re-entry.

### Root cause
`enable_halim_developer_mode` forced `GIT_PUSH_DURING_SESSION=true` and started Halim embedded git watcher (`set_standalone_mode`), bypassing defer policy. `_sync_all_positions_from_ib` returned early when `_position_slots` empty.

### Fix
| File | Change |
|------|--------|
| `core/halim_developer.py` | Respect `GIT_PUSH_DURING_SESSION`; no embedded watcher when false |
| `core/git_sync_defer.py` | Check session defer before standalone bypass |
| `core/position_sync.py` | `adopt_ib_positions_into_slots` for restart recovery |
| `core/scalper_runner.py` | Adopt IB positions at startup + each sync |
| `core/scalper_entry_executor.py` | Skip `push_trade` when defer active |

### Verify
```bash
pytest tests/test_git_sync_defer.py tests/test_position_context_isolation.py -q
# restart — no logs push during RTH; Recovered IB position lines on open holdings
```

---

## 2026-07-01 — Exit finalize crash + git shutdown NameError

### Problem
Recovered-position exit crashed: `'ScalperRunner' object has no attribute '_last_entry_telemetry'`. Shutdown git sync failed: `_brain_snapshot_line` not defined in git_sync_learning. EDBL re-adopted while exit pending.

### Fix
| File | Change |
|------|--------|
| `core/scalper_runner.py` | Init `_last_entry_telemetry`; exclude pending-close tickers from adopt |
| `core/scalper_exit_executor.py` | Safe getattr for telemetry ATR on close |
| `core/git_sync_learning.py` | Call `_gs()._brain_snapshot_line()` |
| `core/position_sync.py` | `exclude_tickers` on adopt |

### Verify
```bash
pytest tests/test_git_sync_defer.py tests/test_position_context_isolation.py -q
```

---

## 2026-07-01 — Strict profit_probability entry gate (Smart Stack)

### Problem
Under default Smart Stack, calculative `profit_probability` / `enter_ok` was advisory only. Fast paths (`ppo:micro_fast`, sniper flash, council timeout, momentum override) entered on vol/score/PPO while quality was red — ~106/500 recent verdicts executed with `halim_signal=false`; war posture skipped low prob only when sniper-strong bypass applied. `_passes_entry_quality_gate` used `pred_1bar` as `live_px`, skewing scores.

### Root cause
`SMART_STACK_ADVISORY_GATES=true` made MTF/regime/quality non-blocking; `quality_blocks_entry()` required `ENTRY_QUALITY_GATE` or hardness ≥0.5 (default 0.45). `SPIKE_FAST_REQUIRES_QUALITY` called `quality_blocks_entry` but still passed. `apply_smart_war_entry` ignored `quality_conf`; strong-spike floor lowered min prob to 48%.

### Fix
| File | Change |
|------|--------|
| `core/smart_stack.py` | `strict_profit_prob_enabled()` default ON; tighten `build_halim_local_entry` + `apply_smart_war_entry` |
| `core/entry_quality.py` | `profit_prob_blocks_entry`, `apply_profit_prob_veto`; wired into `quality_blocks_entry` |
| `core/fast_execution.py` | Quality gate uses real `live_px`; strict mode always assesses; disciplined strong checks quality |
| `core/ai_commander_verdict.py` | Stamp `profit_probability`; disable momentum override when strict; finalize backstop veto |
| `core/scalper_spike_loop.py` | Hard profit-prob veto before `_attempt_entry` even in advisory gate mode |
| `core/live_ai_pipeline.py` | Council timeout / PPO-lead paths require green `enter_ok` when strict |
| `core/capital_discipline.py` | No 48% strong-spike prob floor when strict |
| `core/ai_commander_entry.py` | Pass `live_px` into micro-fast quality assessment |

### Env
- `SMART_STACK_STRICT_PROFIT_PROB=true` (default with Smart Stack) — hard veto on red calculative quality
- `SMART_STACK_STRICT_PROFIT_PROB=false` — restore legacy fast-path bypasses

### Verify
```bash
pytest tests/test_git_sync_defer.py tests/test_position_context_isolation.py -q
venv/bin/python -c "
from core.config import BotConfig
from core.entry_quality import assess_entry_quality, profit_prob_blocks_entry, apply_profit_prob_veto
from core.smart_stack import strict_profit_prob_enabled, build_halim_local_entry
cfg = BotConfig()
assert strict_profit_prob_enabled(cfg)
q = {'enter_ok': False, 'profit_probability': 0.12, 'reason': 'profit_prob=12%'}
assert profit_prob_blocks_entry(cfg, q)
v = apply_profit_prob_veto(cfg, {'enter': True, 'pipeline': 'ppo:micro_fast'}, q)
assert not v['enter'] and 'profit_prob' in v['pipeline']
h = build_halim_local_entry(cfg, halim_live={'status':'missing'}, quality=q, ppo_action=0, ppo_conf=0.5, ppo_reason='', min_conf=0.65, scan_score=57, spike_ratio=1.3)
assert not h['enter']
print('ok')
"
```

## 2026-07-01 — IB recover war ledger overdrawing settled cash

### Problem
After `reset_live_war_session()`, bot restart adopted IB paper positions (e.g. T 347sh ~$7.2k) via `_record_war_adoptions()` → `record_entry(..., pipeline="ib_recover")`, debiting full notional from `settled_cash`. War pool showed `settled_cash: -$3,993` and every spike blocked: `war:block — need $50 > settled/deploy cap ($-3,993)`.

### Root cause
IB position recovery treated pre-existing holdings as fresh war BUYs, double-counting deployment against a $3.5k war NAV.

### Fix
| File | Change |
|------|--------|
| `core/war_account.py` | `adopt_war_ib_recovery()` — ledger adopt without cash debit; `_reconcile_war_cash_from_positions()`; `_heal_war_cash_ledger()` on load when settled < 0 |
| `core/scalper_runner.py` | `_record_war_adoptions()` uses `adopt_war_ib_recovery` not `record_entry` |
| `tests/test_war_multi_position.py` | Coverage for recover adopt + oversized skip |

### Env
- `WAR_IB_RECOVER_MAX_NAV_PCT=0.90` — positions above this fraction of war NAV are monitor-only (not war ledger)

### Verify
```bash
pytest tests/test_war_multi_position.py -q
```

## 2026-07-01 — INTC avgCost + multi-position price cross-talk

### Problem
IB recover showed `INTC @ $140.83` (10× real ~$14) — war ledger, stops, and PnL wrong. Multi-position monitor bled prices across slots (`T` P&L +$8,976 with `BITO` entry $7.98 / 706sh; `plan/price mismatch` on BITO/CELZ).

### Root cause
- Raw IB `avgCost` used without reconciling to live quote (paper 10× drift).
- `_live_price_for` returned another ticker's cached/stream price when entry sanity failed.
- `_force_price_snapshot` mutated `cfg.TICKER` instead of `get_contract(ticker)`.
- Monitor pulse used aggregate `self.shares` instead of per-slot `_ctx_slot_shares`.

### Fix
| File | Change |
|------|--------|
| `core/fill_tracker.py` | `position_entry_price`, `normalize_ib_avg_cost`, `snapshot_market_price` |
| `core/position_sync.py` | Adopt/repair/sync use normalized entry |
| `core/scalper_runner.py` | Sanitized `_live_price_for`; `get_contract(ticker)` snapshots; stricter load/save context |
| `core/scalper_exit_executor.py` | Pulse PnL uses slot shares only |
| `tests/test_position_entry_price.py` | avgCost 10× + cross-price rejection |

### Verify
```bash
pytest tests/test_position_entry_price.py tests/test_position_context_isolation.py -q
```

## 2026-07-01 — AI-sure entry (dynamic Halim + PPO + council, no blind spikes)

### Problem
Entries could fire on blind spike fast-paths (`ppo:micro_fast`, `quality_flash`, council timeout) with green calculative `profit_probability` but without Halim/PPO/API alignment — not "mostly sure", felt like reactive spike chasing.

### Root cause
Static score/vol thresholds on fast paths bypassed deliberation; `build_halim_local_entry` had quality_flash/PPO-lead fallbacks when Halim slow; council timeout/scanner_fast allowed PPO-only entries.

### Fix
| File | Change |
|------|--------|
| `core/smart_stack.py` | `ai_sure_entry_enabled()` default ON; `dynamic_entry_surety()`; `build_halim_local_entry` AI-sure Halim lead only |
| `core/entry_quality.py` | `apply_ai_sure_veto()` — blocks fast pipelines + enforces dynamic floors |
| `core/capital_discipline.py` | All fast-path allows return False when AI-sure |
| `core/live_ai_pipeline.py` | Council fresh path uses AI-sure alignment; no PPO-strong-lead while pending |
| `core/ai_commander_verdict.py` | No momentum override; `apply_ai_sure_veto` in finalize |
| `core/fast_execution.py` | `should_micro_fast_entry` disabled when AI-sure |
| `tests/test_ai_sure_entry.py` | Coverage |

### Env
- `SMART_STACK_AI_SURE_ENTRY=true` (default with Smart Stack) — Halim+PPO+green prob required; no blind fast paths
- `SMART_STACK_AI_SURE_ENTRY=false` — restore micro-fast / quality-flash bypasses

### Verify
```bash
pytest tests/test_ai_sure_entry.py tests/test_position_context_isolation.py -q
```
