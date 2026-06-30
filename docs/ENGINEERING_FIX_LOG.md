# Engineering fix log

**Purpose:** Track every intentional code/config change with enough detail to debug regressions, avoid duplicate fixes, and know what to verify. Append new entries at the top (newest first).

**Related:** [BRAIN_DEVELOPMENT_LOG.md](BRAIN_DEVELOPMENT_LOG.md) (runtime brain events) · [VISION_SMART_STACK.md](VISION_SMART_STACK.md) (architecture)

**How to add an entry:** Copy the template at the bottom, fill every section, link files and env vars explicitly.

**Enforced:** `scripts/git-hooks/pre-commit` blocks commits that touch `core/`, `halim/halim/`, `scripts/*.sh`, or `.cursor/rules/` without a new dated section here. Install: `./scripts/install_git_hooks.sh`. Cursor `afterFileEdit` hook reminds agents. Emergency bypass: `SKIP_FIX_JOURNAL=1` (document ASAP).

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
```
