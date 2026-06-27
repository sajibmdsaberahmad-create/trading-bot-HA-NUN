# M. A. Halim

**M. A. Halim** is your own AI model — not Groq, Gemini, Claude, or Ollama.

HANOON is the trading **body**. Halim is the **mind** that grows from numeric students today into a frontier model tomorrow (generative, calculative, coding, and more).

## Today (newborn phase)

Halim runs as **owned students on disk**:

| Student | File | Role |
|---------|------|------|
| Reflex | `ppo_trader.zip` | Millisecond trade policy |
| Enter/skip | `teacher_proxy.joblib` | Distilled decisions |
| Scanner | `scalper_weights.json` | Heuristic tuning |
| Memory | `council_training_dataset.jsonl` | Training gold for future Halim LM |

**No external LLM required** when native mode is on:

```bash
export HALIM_NATIVE=true
./scripts/halim_native_replay.sh
```

## Separate model repo

The frontier home lives in **`halim/`** (designed as its own git repo):

```bash
cd halim
python scripts/sync_from_tradingbot.py --source ..
git init   # when ready: github.com/YOU/halim
```

See [halim/README.md](../halim/README.md) and [halim/ROADMAP.md](../halim/ROADMAP.md).

## Growth path

```
newborn  → PPO + proxy (now, M2 8GB)
toddler  → first Halim transformer on trading data
child    → + code, math, reasoning
adult    → Halim on your hardware, zero API
frontier → full generative / calculative / coding model
```

## Identity files

- `models/halim_identity.json` — birth, phase, philosophy
- `models/halim_manifest.json` — full status (updated each evolution)
- `docs/BRAIN_DEVELOPMENT_LOG.md` — human timeline (git)

## Halim as self-developer

Halim mutates bounded params, self-improves, syncs `halim/`, and **auto-pushes git** (default on):

```bash
./scripts/halim_developer.sh       # manual: mutate + improve + push now
export HALIM_AUTO_PUSH=true        # embedded watcher (default)
export GIT_PUSH_DURING_SESSION=true
```

Log: `models/halim_developer.jsonl`

Requires `.env`: `GITHUB_TOKEN` + `GITHUB_HANOON_REPO`

## Frontier + guardrails

Halim is **not trading-only**. Roadmap: generative, coding, math, agents, web/API tools, multimodal → **frontier class**.

**Primary mission:** profit hunting — Halim runs on the **same clock as HANOON** (RTH = trade focus; off-hours = learn/evolve).

**Secondary abilities:** code, wiki/news learn, research — when you request or market is closed.

All external actions gated by `core/halim_guardrails.py` + `core/halim_frontier_policy.py` (same harm categories as Gemini, Claude, OpenAI):

- **Kill switch:** `./scripts/halim_kill_switch.sh` or `HALIM_KILL_SWITCH=true`
- **Constitution:** `models/halim_constitution.json`
- **Frontier policy:** `models/halim_frontier_policy.json`
- **Audit:** `models/halim_guardrail_audit.jsonl`, `models/halim_frontier_audit.jsonl`
- **Runtime journal:** `models/halim_runtime.jsonl`

Full policy: [HALIM_GUARDRAILS.md](HALIM_GUARDRAILS.md)

APIs and live internet = **tools Halim consumes**, not Halim's brain (owned weights are the brain).

### Runtime modes (co-located with algo)

| Mode | When | Halim does |
|------|------|------------|
| `trade_focus` | Market open / tradable sessions | Profit hunting only — no learn/dev distraction |
| `off_hours` | Closed / overnight | Wiki/news learn, developer cycle, evolution |
| `user_task` | `HALIM_USER_TASK=wiki:Inflation` | One-shot user-requested work |

**Git during replay:** all pushes deferred → **1 consolidated sync** at session end (no triple-repo spam per trade).

```bash
export HALIM_USER_TASK="wiki:Federal_Reserve"   # optional secondary task
./scripts/start_hanoon.sh                         # Halim + algo start together
./scripts/start_replay_live.sh turbo              # fast replay, git at end only
```

### Halim server (optional — future-ready, never blocks trading)

**Fast path** (PPO + proxy) always runs **inline** in HANOON. **Slow path** (future LM) uses optional local server when a checkpoint exists.

```bash
./scripts/halim_serve.sh          # optional — port 8765, ~zero RAM until LM loaded
./scripts/halim_status.sh         # phase, dataset pairs, reflex assets, server health
```

| Path | Role |
|------|------|
| `halim/halim/serve.py` | Compact stdlib server |
| `core/halim_inference.py` | HANOON bridge (2.5s timeout max) |
| `halim/docs/ARCHITECTURE.md` | Two-path design, any-device tiers |

Env: `HALIM_SERVER=auto` (default) · `HALIM_DEVICE=m2_8gb` · `HALIM_MODEL_PATH=halim/data/checkpoints/latest`

**Not read-only like Ollama.** Halim is an **active model** — learns by action, writes datasets/checkpoints, evolves. External web (wiki/news) is read-only; Halim's own weights and memory are writable. Dedicated server adds reasoning + `/v1/record` + `/v1/export` + `/v1/evolve`. Flags `HALIM_INFERENCE_ONLY` and `HALIM_READ_ONLY` are blocked.

### PPO ↔ Halim co-evolution

PPO and Halim **learn from each other** and from everything else (council, trades, web, replay):

```
Every decision → compare PPO vs Halim (proxy/council/LM)
    → agree? reinforce both
    → disagree? correction gold for whoever was wrong (after trade outcome)
    → session end → coevolution_gold.jsonl → both students train
```

| Source | Feeds |
|--------|-------|
| PPO reflex | Halim action gold + coevolution log |
| Halim proxy / council | PPO experience buffer + deferred learning |
| Trade win/loss | Labels who to correct |
| Web / replay / evolution | Shared datasets |

Disable: `HALIM_PPO_COEVOLUTION=false`

**Philosophy — generative two-way evolution:** PPO (fast reflex) and Halim (reasoning mind) are two students in constant dialogue. Neither speaks from static templates. When they **agree**, both reinforce. When they **disagree**, Halim generates a reflective narrative (`coevolution_generative_reflect`) → `coevolution_gold.jsonl` → both train. Trade outcomes label who was wrong. Over sessions, PPO weights and Halim checkpoint converge — co-evolution, not parallel static bots.

Env: `HALIM_PPO_GENERATIVE_REFLECT=true` (reflect on disagreements)

**Full two-way dialogue (every trade):** On each entry/exit/manage decision, PPO and Halim generate a live exchange (`PPO:` reflex voice → `Halim:` mind voice). Journaled to `dialogue.jsonl` + `dialogue_gold.jsonl` for mutual training. Entry/exit also broadcast to Telegram when `HALIM_PPO_DIALOGUE_TELEGRAM=true`.

```
entry/exit decision
    → record_coevolution (compare signals)
    → schedule_ppo_halim_dialogue (generative PPO↔Halim exchange)
    → trade closes
    → attach_trade_outcome + post-trade dialogue (who was right)
    → session end → coevolution_gold + dialogue_gold → both train
```

Env: `HALIM_PPO_DIALOGUE=true` · `HALIM_PPO_DIALOGUE_TELEGRAM=true` · `HALIM_PPO_DIALOGUE_THROTTLE_SEC=25`

### Replay = live parity (IB historical farm)

Replay uses the **same** ScalperRunner, council, PPO, Halim companion, and PPO↔Halim dialogue as live. Data comes from the **IB HMDS farm** downloaded to `data/replay/intraday/*_1min.csv` — not a tiny static subset.

```bash
# Deepen IB farm (60 days default, merges into existing CSVs)
PYTHONPATH=. python scripts/download_ib_replay_data.py --days 60
PYTHONPATH=. python scripts/download_ib_replay_data.py --days 60 --refresh-partial

./scripts/start_replay_live.sh turbo   # train everyone from replay session
./stop_replay.sh                         # same flush as live
```

| Live | Replay |
|------|--------|
| IB HMDS + ticks | IB CSV farm (full intraday history) |
| `ppo_trader.zip` | `models/ppo_trader_replay.zip` (isolated) |
| Halim + coevolution + dialogue | **Same code paths** |
| Session-end evolution + git | **Same** (`run_graceful_shutdown`) |

Replay training (`REPLAY_TRAINING_ENABLED=true`): incremental PPO, teacher proxy, PPO teacher, co-evolution export, Halim gold — fed by `replay_live` buffer + full CSV depth.

Warmup/history uses **replay clock** (`history_before`) — no lookahead from dataset start.

Logs: `halim/data/coevolution/correction_log.jsonl` · `halim/data/coevolution/dialogue.jsonl` · `halim/data/training/coevolution_gold.jsonl` · `halim/data/training/dialogue_gold.jsonl`

### Halim companion (generative algo voice)

Halim is **HANOON's mind and voice** — every word is **generated** by Halim's brain chain (native LM → council teacher → gold journal). **No static greeting scripts.**

| Trigger | Halim does |
|---------|------------|
| You chat (Telegram / CLI) | `companion_speak()` — intent routes *what to think*, brain generates *how to say it* |
| RTH open (09:30 ET) | Proactive companion ping — once/day, generative |
| Session startup | Halim introduces itself if market live |

Env: `HALIM_COMPANION_PING=true` (proactive pings) · `HALIM_COMPANION_LEARN=true` (journal gold)

Journal: `halim/data/companion/conversation_gold.jsonl` · state: `models/halim_companion_state.json`

### Capability unlock ladder (slowly, not all at once)

Halim **does not** get chat, code, files, and images today. Each unlocks by **phase + power + actions**:

| Phase | Power ~ | Unlocks |
|-------|---------|---------|
| **newborn** (now) | 15–25 | Trade, notify, read wiki, **chat + companion greetings** |
| **toddler** | 30–45 | Chat teacher, math, reasoning native path |
| **child** | 55+ | **Code + file** generation (guardrailed) |
| **adult** | 80+ | Full chat native, agents, image understand |
| **frontier** | 100 | **Image generation**, multimodal |

**Modes per capability:** `locked` → `collecting` → `teacher` (council) → `native` (Halim LM)

```bash
./scripts/halim_chat.sh "Hello Halim"      # chat (phased)
./scripts/halim_chat.sh --unlock           # full ladder JSON
```

Server: `POST /v1/chat` · `POST /v1/generate` · `GET /v1/unlock`

Power grows with: device RAM tier, lifecycle phase, dataset size, action maturity.

### Learn by doing (action gold)

Halim **learns from every task it performs** — not only from static datasets:

| Capability | What Halim does | When it learns |
|------------|-----------------|----------------|
| `trade_reflex` | PPO enter/exit | Every trade |
| `enter_skip` | Proxy filter | Every student decision |
| `text_compose` | Telegram, digests | Notify + template fallbacks |
| `decision_text` | Council reasoning | Groq/Gemini decisions → gold |
| `read_understand` | Wiki/news read | Every learn fetch |
| `reasoning` | Copilot narrative | Toddler+ LM or teacher |
| `chart_read` | Chart vision | Gemini vision → gold until Halim multimodal |

```bash
./scripts/halim_export_actions.sh    # merge journal → SFT gold
```

| File | Role |
|------|------|
| `halim/data/actions/action_log.jsonl` | Every action journal |
| `halim/data/training/action_gold.jsonl` | Instruction-tuning pairs |
| `halim/data/registry.jsonl` | Milestones + evolution lineage |

Disable journaling: `HALIM_ACTION_LEARN=false`

### Graceful shutdown

Always stop with the shutdown scripts — not Ctrl+C — so all data is saved:

```bash
./stop.sh              # live HANOON — Halim gold + evolution + git + IB cleanup
./stop_replay.sh       # replay — same flush pipeline, one git sync at end
```

Both wait up to **180s** for evolution + git. If the process is already dead or was SIGKILL'd, the stop script runs a **standalone flush** from disk.

What gets flushed: action gold, Halim manifest, owned-brain evolution, registry, git sync.

Journal: `models/halim_shutdown.jsonl`

### Google AI search (enabled)

Halim may **google** a topic and read only the **public AI Overview** box on Google Search (like AI mode in the browser) — **not** the Gemini API, **not** visiting result links:

```bash
./scripts/halim_google_search.sh "what is an egg"
```

- Max **50 searches/day**
- Only `google.com/search?q=...`
- `links_followed: 0` always

### Wikipedia & news learning (read-only, monitored)

Halim may **read** allowlisted public pages for training gold — **never edit, post, login, or change anything** on external sites:

```bash
./scripts/halim_learn_fetch.sh "https://en.wikipedia.org/wiki/Egg"
./scripts/halim_learn_fetch.sh "wiki:Inflation"   # Wikipedia shortcut
```

**Allowlisted hosts:** Wikipedia, Reuters, AP, BBC, CNBC, Yahoo Finance, SEC, Investopedia.

| Rule | Value |
|------|-------|
| Method | GET only — no POST, no forms |
| Max size | 512 KB per page |
| Daily cap | 80 learn fetches |
| Link following | **0** — one URL per request |
| External changes | **Never** — `external_changed: false` always |
| Audit | `models/halim_web_learn.jsonl` + `models/halim_web_monitor.jsonl` |
| Cache | `halim/data/learn_cache/` (local training snippets) |

Disable: `HALIM_WEB_LEARN=false`

Blocked forever: Wikipedia edit URLs, login, subscribe, `/api/`, form submit.

## Related

- [OWNED_BRAIN.md](OWNED_BRAIN.md) — technical flywheel (evolution, git, Telegram)
