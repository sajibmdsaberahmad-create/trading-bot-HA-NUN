# Engineering fix log

**Purpose:** Track every intentional code/config change with enough detail to debug regressions, avoid duplicate fixes, and know what to verify. Append new entries at the top (newest first).

**Related:** [BRAIN_DEVELOPMENT_LOG.md](BRAIN_DEVELOPMENT_LOG.md) (runtime brain events) · [VISION_SMART_STACK.md](VISION_SMART_STACK.md) (architecture)

**How to add an entry:** Copy the template at the bottom, fill every section, link files and env vars explicitly.

**Enforced:** `scripts/git-hooks/pre-commit` blocks commits that touch `core/`, `halim/halim/`, `scripts/*.sh`, or `.cursor/rules/` without a new dated section here. Install: `./scripts/install_git_hooks.sh`. Cursor `afterFileEdit` hook reminds agents. Emergency bypass: `SKIP_FIX_JOURNAL=1` (document ASAP).

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
