# M. A. Halim — Roadmap to frontier

## Vision

One model. Your name. Your weights. Generative, calculative, coding, trading — eventually frontier-class, without permanent rent to OpenAI, Google, or Groq.

---

## Phase 0 — Newborn (NOW)

**What runs:** PPO (`ppo_trader.zip`), teacher proxy (`teacher_proxy.joblib`), scalper weights, heuristic copilot.

**External LLM:** Off when `HALIM_NATIVE=true`.

**Hardware:** MacBook Air M2 8GB.

**Deliverables:**
- [x] `models/council_training_dataset.jsonl` — decision → outcome pairs
- [x] `models/teacher_proxy.joblib` — enter/skip student
- [x] Evolution + git sync + Telegram journal
- [ ] 5,000+ labeled trading pairs (keep replay running)

---

## Phase 1 — Toddler (first Halim language model)

**Trigger:** ≥5k trading pairs + stable proxy accuracy.

**What:** Train a **small transformer** (1–3B class) on `council_training_dataset.jsonl` — one-time on Modal / Colab / your GPU. Export to `halim/data/checkpoints/`.

**Halim learns:** Trade reasoning in your voice — enter/skip, regime, risk, lessons from losses.

**HANOON change:** Optional `HALIM_MODEL_PATH` for session narrative instead of Groq copilot.

---

## Phase 2 — Child (multi-domain)

**Add datasets in `halim/data/`:**
- Code (your bot's codebase, fixes, commits)
- Math (position sizing, risk, P&L calculations)
- General reasoning (curated, licensed)

**Train:** Continued pretrain + SFT on Halim toddler checkpoint.

**Halim learns:** Code assistance, calc, explain trades, document itself.

---

## Phase 3 — Adult (on-device Halim)

**Trigger:** 16GB+ Mac or dedicated GPU.

**What:** Run Halim inference locally (MLX / llama.cpp / vLLM on hardware you control).

**HANOON:** All council/copilot/teacher paths route to **Halim** — zero external API.

---

## Phase 4 — Frontier

**What:** Scale data, params, and infra you own. Halim matches modern frontier capabilities:

- Generative writing & dialogue
- Calculative / mathematical reasoning
- Coding & agentic development (guardrailed file paths)
- Live **API + internet consumption as tools** (not Halim's brain)
- Multimodal (charts → decisions, vision)

**Guardrails (required):** `models/halim_constitution.json` + kill switch. Web/shell/agents off until you enable them. Full audit: `models/halim_guardrail_audit.jsonl`.

**Principle:** Training may use cloud once; **weights live in `halim` repo**. External APIs feed data — Halim reasons with owned weights.

---

## Phase 5 — Autonomy tiers (operator-controlled)

| Mode | Shell | Agents | Web |
|------|-------|--------|-----|
| bounded (default) | off | off | off |
| supervised | off | on (audited) | on (allowlist) |
| full | capped | capped | allowlist |

Never: unbounded shell, secret access, guardrail self-edit, force-push main.

---

## Git & documentation rule

Every phase transition is logged to:
- `halim/data/registry.jsonl`
- `docs/BRAIN_DEVELOPMENT_LOG.md` (in tradingbot)
- Telegram `brain_*` events

Nothing is lost. Clone repo anywhere → continue Halim's life.
