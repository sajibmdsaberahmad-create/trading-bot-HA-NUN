# M. A. Halim — compact architecture (any device → frontier class)

## Design goal

**Not a direct GPT competitor.** A **portable owned mind** that:

- Runs on **any device** (reflex-only on 8GB; LM when hardware allows)
- Stays **compact** today, **powerful** tomorrow
- **Never blocks** HANOON trading (fast path always inline)
- Earns a **name on the list** via domain depth + owned weights + guardrails

---

## Two-path inference (never mix them)

```
                    ┌─────────────────────────────────┐
                    │         HANOON process           │
                    │  (one loop — profit hunting)     │
                    └───────────────┬─────────────────┘
                                    │
          FAST PATH (always)        │        SLOW PATH (optional)
          microseconds              │        milliseconds–seconds
                                    │
    ┌───────────────────────────────┼───────────────────────────────┐
    ▼                               ▼                               ▼
 PPO zip                      sklearn proxy                   scalper_weights
 (enter/exit reflex)          (enter/skip filter)             (scanner)
    │                               │                               │
    └───────────────────────────────┴───────────────────────────────┘
                                    │
                         NEVER goes over HTTP
                                    │
                    Optional when toddler+ checkpoint exists:
                                    │
                                    ▼
                    ┌─────────────────────────────────┐
                    │  halim serve (127.0.0.1:8765)      │
                    │  GET /v1/status                    │
                    │  POST /v1/complete  → Halim LM     │
                    └─────────────────────────────────┘
                                    │
                         Fallback: Groq/Gemini → then fade out
```

---

## Device tiers (`halim/device.py`)

| Profile | RAM | Reflex | Local LM |
|---------|-----|--------|----------|
| `minimal` | ≤4GB | ✓ | ✗ |
| `m2_8gb` | ≤10GB | ✓ | collect dataset |
| `m2_16gb` | ≤20GB | ✓ | 1–3B quant |
| `m2_32gb_plus` | 32GB+ | ✓ | 7–8B quant |
| `gpu_cloud` | train burst | ✓ | any (weights to git) |

Set: `HALIM_DEVICE=m2_8gb` or `OWNED_BRAIN_DEVICE=...`

---

## How Halim faces billion-dollar labs (without their budget)

| Frontier labs | Halim |
|---------------|-------|
| Pretrain on internet | **Skip** — fine-tune open base on **your** jsonl |
| General knowledge | **Specialize** — trading + your codebase + guardrailed learn |
| Always-on datacenter | **Episodic cloud train** + git-synced weights |
| Rented API brain | **Owned checkpoints** in `halim/data/checkpoints/` |
| Black box | **Constitution + audit + kill switch** |

**Moat:** your trade ledger, not parameter count.

---

## Server (optional today, required for LM tomorrow)

```bash
./scripts/halim_serve.sh          # background: optional
./scripts/halim_status.sh         # engine + server health
```

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness |
| `GET /v1/status` | Phase, dataset size, reflex assets, LM ready |
| `POST /v1/complete` | Reasoning (503 until checkpoint + backend wired) |
| `POST /v1/record` | Journal action → learn-by-doing gold |
| `POST /v1/export` | Merge action log → SFT pairs |
| `POST /v1/evolve` | Halim flush + owned-brain evolution (no git) |
| `POST /v1/chat` | Commander dialogue (phased) |
| `POST /v1/generate` | Code / file / image (phased) |
| `GET /v1/unlock` | Full capability ladder + power score |

**Halim server ≠ Ollama.** Ollama serves frozen weights read-only. Halim server is an **active runtime**: generates, records every action, exports gold, evolves owned weights. External wiki/news stays read-only; Halim itself is read-write on owned assets.

Env flags **blocked**: `HALIM_INFERENCE_ONLY`, `HALIM_READ_ONLY`

Env:

| Variable | Default | Meaning |
|----------|---------|---------|
| `HALIM_SERVER` | `auto` | `auto` \| `off` \| `on` |
| `HALIM_SERVER_URL` | `http://127.0.0.1:8765` | Client target |
| `HALIM_INFERENCE_TIMEOUT_SEC` | `2.5` | Max wait — trading never blocks longer |
| `HALIM_REASONING_VIA_SERVER` | `auto` | Route slow text to server when up |
| `HALIM_MODEL_PATH` | `halim/data/checkpoints/latest` | Future LM |
| `HALIM_LM_BACKEND` | `mlx` on Apple Silicon Mac; `hf` on Linux/Colab | `mlx` \| `hf` \| `llama_cpp` |
| `HALIM_BASE_MODEL` | scaffold registry id (see `halim/halim/scaffold.py`) | HuggingFace/MLX id for **training scaffold** weights — not the Halim product name |

**Mac default:** MLX uses Metal + 4-bit quant — fits M2 8GB. Full guide: [docs/HALIM_MAC_INFERENCE.md](../../docs/HALIM_MAC_INFERENCE.md).

```bash
./scripts/halim_install_lm.sh   # auto-picks MLX on arm64 Mac
```

Bridge in tradingbot: `core/halim_inference.py`

---

## Phase plug-in (nothing breaks when you upgrade)

| Phase | Fast path | Slow path | Server |
|-------|-----------|-----------|--------|
| **newborn** (now) | PPO + proxy | Groq optional | status only |
| **toddler** | same | Halim 1–3B SFT | `complete` live |
| **adult** | same | local LM default | primary reasoning |
| **frontier** | same | multimodal + agents | same process, bigger ckpt |

Each phase **adds** capability; reflex path unchanged.

---

## Learn by action (core philosophy)

Halim does **not** wait for a big bang train. Every guarded task writes to `action_log.jsonl`:

```
trade → reflex gold
council decision → decision_text gold (teacher)
telegram notify → text_compose gold
wiki fetch → read_understand gold
chart vision → chart_read gold (teacher until child phase)
```

Off-hours + post-evolution: `export_action_gold()` merges into `action_gold.jsonl` for toddler SFT.

Capability maturity (0–100%) = actions done ÷ phase threshold. See `core/halim_capabilities.py`.

---

## PPO ↔ Halim co-evolution

Not two separate models — **one flywheel, two reflexes**:

| When | What happens |
|------|----------------|
| Entry/exit | PPO signal compared to Halim proxy/council |
| Deferred council | Late Halim answer corrects PPO micro-train |
| Trade close | Win/loss labels who was right |
| Session end | `run_coevolution_cycle()` exports mutual gold |
| Evolution | Proxy retrains; Halim action gold merges; PPO teacher reads corrections |

Module: `core/halim_ppo_coevolution.py`

---

## Files

| Path | Role |
|------|------|
| `halim/halim/protocol.py` | Version, component lists |
| `halim/halim/device.py` | Any-device profiles |
| `halim/halim/engine.py` | Status + reasoning stub |
| `halim/halim/client.py` | HTTP client, short timeout |
| `halim/halim/serve.py` | stdlib server |
| `core/halim_inference.py` | HANOON bridge |
| `core/halim_runtime.py` | Co-runtime with algo |

---

## Registry (future “name on the list”)

Every train run appends to `halim/data/registry.jsonl`:

- checkpoint hash, dataset size, eval WR, device profile, git commit

Portable proof Halim exists as a **model product**, not only a script.
