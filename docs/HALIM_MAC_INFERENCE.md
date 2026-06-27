# Halim inference on Mac (Apple Silicon)

Halim’s toddler LM runs **locally on your Mac** via **MLX** — Apple’s Metal-optimized stack. This is the default on `arm64` Macs (M1/M2/M3/M4). HuggingFace (`torch` + `transformers`) is reserved for **Linux and Google Colab** training/export.

## Why MLX on Mac (not HuggingFace)

| | **MLX** (Mac default) | **HuggingFace** (Colab/Linux) |
|---|------------------------|-------------------------------|
| Hardware | Apple Silicon GPU (Metal) | CUDA / CPU |
| RAM on M2 8GB | ~0.5B @ 4-bit fits comfortably | Often OOM or very slow |
| Install | `mlx-lm` + `mlx` (~light) | `torch` + `transformers` + `peft` (~heavy) |
| Checkpoint | Same LoRA adapter + base quant model | Merged full weights or LoRA |
| Training | On-device LoRA (`mlx_lm.lora`) | Colab HF path |

**Comparison baseline:** Halim toddler = **Qwen2.5-0.5B-Instruct** fine-tuned with your action gold. Compare against the public base **`Qwen/Qwen2.5-0.5B-Instruct`** (unfine-tuned), not against Groq/Gemini — those are optional **teacher** models when API budget allows.

## Auto-config (`scripts/halim_env.sh`)

On `Darwin` + `arm64`:

```bash
HALIM_LM_BACKEND=mlx
HALIM_BASE_MODEL=mlx-community/Qwen2.5-0.5B-Instruct-4bit
HALIM_MODEL_PATH=halim/data/checkpoints/latest
HALIM_FORCE_LM=true
```

On `Darwin` + `arm64`, `halim_env.sh` sets **MLX** even if `.env` still has `HALIM_LM_BACKEND=hf` from an older setup. To force HF on Mac: `export HALIM_LM_BACKEND_LOCKED=true HALIM_LM_BACKEND=hf`.

```bash
export HALIM_LM_BACKEND=hf
export HALIM_BASE_MODEL=Qwen/Qwen2.5-0.5B-Instruct
./scripts/halim_install_lm.sh
```

## One-time setup

```bash
cd tradingbot
source venv/bin/activate          # if you use venv
./scripts/halim_install_lm.sh     # installs mlx-lm on Mac
./scripts/halim_stop.sh
./scripts/halim_start.sh          # or START_HALIM.command
```

Verify:

```bash
curl -s http://127.0.0.1:8765/v1/status | python3 -m json.tool
# reasoning.backend should be "mlx"

curl -s -X POST http://127.0.0.1:8765/v1/complete \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Say hello in one sentence.","purpose":"chat"}' | python3 -m json.tool
# ok: true, source: halim_lm
```

## Device tiers vs LM

From `halim/halim/device.py`:

| Profile | Typical Mac | Local LM |
|---------|-------------|----------|
| `m2_8gb` | M2 8GB (yours) | MLX 0.5B 4-bit when `HALIM_FORCE_LM=true` |
| `m2_16gb` | M2/M3 16GB | 1–3B MLX quant |
| `m2_32gb_plus` | 32GB+ | up to ~8B quant |

Set explicitly: `export HALIM_DEVICE=m2_8gb`

## Colab train → Mac serve flow

1. **Colab:** train LoRA on `Qwen/Qwen2.5-0.5B-Instruct`, export zip (`halim_toddler_v1.zip`).
2. **Mac:** `./scripts/halim_start_toddler.sh ~/Downloads/halim_toddler_v1.zip`
   - Registers checkpoint with `--backend mlx`
   - Installs MLX (not torch)
3. **Serve:** `./scripts/halim_serve.sh` — loads 4-bit MLX base + your LoRA adapter.

See also: [halim/colab/COLAB_GUIDE.md](../halim/colab/COLAB_GUIDE.md)

## Wired loops (RAG + auto-retrain)

| Feature | Env | What it does |
|---------|-----|--------------|
| **Learn RAG** | `HALIM_LEARN_RAG=true` | Injects `learn_cache` into chat prompts immediately |
| **Auto LM retrain** | `HALIM_AUTO_LM_RETRAIN=true` | Off-hours: export gold → SFT → short MLX LoRA when +150 new pairs |
| **Standalone maint** | `HALIM_STANDALONE_MAINT=true` | Telegram-only mode still learns wiki + checks retrain every 2h |

Manual retrain now:

```bash
./scripts/halim_auto_evolve_lm.sh          # respects thresholds
./scripts/halim_auto_evolve_lm.sh --force # skip thresholds
```

State: `models/halim_lm_evolve_state.json` · journal: `models/halim_lm_evolve.jsonl`

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `transformers_not_installed` | You’re on HF backend — run `./scripts/halim_install_lm.sh` or set `HALIM_LM_BACKEND=mlx` |
| `mlx_lm_not_installed` | `./scripts/halim_install_lm.sh` |
| Empty Telegram reply, “via unavailable” | Serve up but LM failed — check `/v1/complete`; install MLX; restart serve |
| `halim_serve.log` empty | Normal — `HALIM_SERVE_QUIET=true` suppresses HTTP logs |
| Cloud teacher blocked | Adult brain maturity zeros copilot API budget — native MLX is the intended chat path |

## Related

- [HALIM.md](HALIM.md) — full Halim overview
- [halim/docs/ARCHITECTURE.md](../halim/docs/ARCHITECTURE.md) — two-path design
- [OWNED_BRAIN.md](OWNED_BRAIN.md) — teacher vs owned weights
