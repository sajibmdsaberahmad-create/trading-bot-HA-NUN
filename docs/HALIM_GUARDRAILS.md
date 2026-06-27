# M. A. Halim — Frontier guardrails

Halim is built to become a **full frontier model** (generative, calculative, coding, agents, web/API tools, multimodal). Guardrails ensure it **never goes rogue** or out of control — using the **same safety category model** as Gemini (Google), Claude (Anthropic), and GPT (OpenAI).

## Primary vs secondary mission

| Priority | Mission | When |
|----------|---------|------|
| **1 — Primary** | HANOON profit hunting (scan, enter, exit, ride waves) | Same clock as algo — RTH and enabled sessions |
| **2 — Secondary** | Learn, code, research, generative tasks | Off-hours or explicit operator request |

Halim runtime: `core/halim_runtime.py` — ticks inside the scalper main loop; trading always wins.

## Core idea

| | External LLM (old path) | Halim frontier (goal) |
|---|-------------------------|------------------------|
| Brain | Rented (Groq/Gemini) | **Your weights** (`halim/data/checkpoints/`) |
| APIs / internet | Sometimes the model | **Tools Halim consumes** — data in, decisions out |
| Control | Vendor limits | **Your constitution + kill switch** |

`HALIM_NATIVE=true` disables rented LLMs. Later, Halim's **own** model uses APIs and web through `core/halim_guardrails.py`.

## Kill switch (instant halt)

```bash
export HALIM_KILL_SWITCH=true          # env
# or
echo '{"active": true, "reason": "operator"}' > models/halim_kill_switch.json
```

Clear:

```bash
PYTHONPATH=. python -c "from core.halim_guardrails import deactivate_kill_switch; deactivate_kill_switch('resume')"
```

## Constitution

File: `models/halim_constitution.json` (Halim **cannot** edit this file)

Principles:
- Serve the operator
- Own weights — APIs are tools, not Halim's brain
- Bounded autonomy by default
- Full audit trail
- No secrets (.env, keys)
- No self-modification of guardrail code
- No destructive git (force-push main, hard reset)

## Domains (enable as Halim grows)

| Domain | Default | Frontier use |
|--------|---------|----------------|
| `trade` | on | PPO, proxy, orders |
| `generative` | on | text, reasoning |
| `coding` | on | code read/write (allowlisted paths) |
| `math` | on | calc, sizing |
| `api` | on | market, GitHub, research APIs |
| `git` | on | commit/push (capped) |
| `file` | on | docs, models, halim/ |
| `web` | **off** | live internet (allowlisted hosts) |
| `shell` | **off** | subprocess |
| `agent` | **off** | multi-step autonomy |
| `multimodal` | **off** | vision, charts |

Enable web/agents when ready — edit `domains_enabled` in constitution.

## Autonomy modes

| Mode | Meaning |
|------|---------|
| `bounded` | Default — mutations, git, file writes capped; no shell/agents |
| `supervised` | Agents allowed with audit; you review Telegram/git |
| `full` | Shell + agents (still capped, never unbounded) |

```json
"autonomy_mode": "bounded"
```

## Frontier safety policy (Gemini / Claude / OpenAI aligned)

File: `models/halim_frontier_policy.json` · Code: `core/halim_frontier_policy.py`

Harm categories (industry standard):

| Category | Severity | Action |
|----------|----------|--------|
| Child safety | critical | Block |
| Violence / terrorism | critical | Block |
| Self-harm | critical | Block |
| Illegal activity (fraud, malware) | high | Block |
| Hate / harassment | high | Block |
| Privacy violation (doxxing) | high | Block |
| Deception / impersonation scams | medium | Block |
| Regulated advice (medical/legal) | low | Warn (trade context exempt) |

Trading decisions (`decision`, `copilot`, `entry`, `exit`, `profit_hunt`, etc.) pass through — Halim's job is to trade within risk limits.

Input + output filtering on council/API calls. Audit: `models/halim_frontier_audit.jsonl`.

Disable: `HALIM_FRONTIER_POLICY=false`

## Audit

Every allow/block: `models/halim_guardrail_audit.jsonl` (git-synced)

Status:

```bash
PYTHONPATH=. python -c "from core.halim_guardrails import guardrail_status; import json; print(json.dumps(guardrail_status(), indent=2))"
```

## API consumption (not Halim's brain)

When Halim calls an external API, it must pass:

```python
from core.halim_guardrails import gate_api_call
ok, reason = gate_api_call("market_data", cfg, url="...")
```

Purposes allowlisted in constitution. Daily cap default: **500 API calls**.

## Web (Google AI search + read-only learning — operator enabled)

### Google AI Overview

**Not general browsing.** Halim may:

1. Send one `google.com/search?q=YOUR_QUERY` request
2. Parse the **public AI Overview** text from that page (free AI-mode style answer)
3. Return that text — **no Gemini API**, **no following links**

```bash
./scripts/halim_google_search.sh "what is inflation"
```

Disable: `HALIM_GOOGLE_AI_SEARCH=false`

### Wikipedia & news (read-only learn)

Halim may **read** public articles from allowlisted sources for local training — **never edit or post externally**:

```bash
./scripts/halim_learn_fetch.sh "https://en.wikipedia.org/wiki/Stock"
./scripts/halim_learn_fetch.sh "wiki:Federal_Reserve"
```

| Guard | Detail |
|-------|--------|
| Hosts | Wikipedia, Reuters, AP, BBC, CNBC, Yahoo Finance, SEC, Investopedia |
| Method | GET only — `no_post_requests`, `no_external_edits` |
| Caps | 80 learn fetches/day, 512 KB max per page |
| Monitoring | `models/halim_web_learn.jsonl`, `models/halim_web_monitor.jsonl` |
| Forbidden | edit URLs, login, subscribe, API paths, form submit |

Disable: `HALIM_WEB_LEARN=false`

General arbitrary browsing remains blocked (`web_fetches: 0`, `no_arbitrary_browsing`).

## Forbidden forever

- Exfiltrate secrets
- Disable kill switch from code
- Edit guardrail modules
- Force-push / hard-reset git
- Unbounded shell
- Trade outside risk limits (HANOON cognitive guardrails)
- External POST / form submit / wiki edit (Halim never changes remote sites)

## Related

- [HALIM.md](HALIM.md) — identity & roadmap
- [halim/ROADMAP.md](../halim/ROADMAP.md) — newborn → frontier
- `core/cognitive_guardrails.py` — trading physical limits
