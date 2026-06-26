# HANOON — Portable AI Trading Algo

Clone this repo on **any Mac**, run **one command**, and trade. Includes PPO model, AI learning state, and **encrypted environment secrets** — no retraining, no re-typing API keys.

**Repo:** https://github.com/sajibmdsaberahmad-create/HANOON

> Keep this repo **private**. `secrets/sync.key` decrypts your Telegram / IB / GitHub credentials.

---

## New device — 3 steps

### 1. Prerequisites

| Item | Detail |
|------|--------|
| macOS 12+ | 8 GB RAM minimum (M1/M2 OK) |
| Python 3.10–3.11 | Not 3.12 |
| IB Gateway | Paper mode, port **4002**, API enabled |
| Ollama | `brew install ollama` |
| Git | Access to this private repo |

### 2. Clone

```bash
git clone https://github.com/sajibmdsaberahmad-create/HANOON.git
cd HANOON
```

### 3. Start

```bash
chmod +x start.sh scripts/*.sh
./start.sh
```

The launcher will:

1. Create `venv` + install dependencies  
2. **Decrypt** `secrets/hanoon.env.enc` → `.env` (your keys, synced from primary machine)  
3. Start Ollama + pull the right model for your RAM  
4. Connect to IB Gateway and run the HANOON scalper  

Stop: `./scripts/stop_hanoon.sh`  
Logs: `logs/HANOON.log`

---

## What's in this repo (clean — essentials only)

| Included | Purpose |
|----------|---------|
| `core/` | Full trading + AI engine |
| `scripts/start_hanoon.sh` | One-command launcher |
| `ppo_trader.zip` | Trained PPO model (~28 MB) — **no retrain needed** |
| `models/consciousness.json` | AI memory & training history |
| `models/scalper_weights.json` | Learned scanner weights |
| `models/*.json` + guidelines | Parameters, improvements, pilot XP |
| `secrets/hanoon.env.enc` | Encrypted `.env` (Telegram, IB, GitHub) |
| `secrets/sync.key` | Vault decryption key |

| **Excluded** (stays on dev machine / other repos) | Why |
|---------------------------------------------------|-----|
| `logs/`, `*.log`, `audit_trail.jsonl` | Runtime noise |
| `models/*_ledger.jsonl` | Grows forever |
| `backtest_results/`, `archive/` | Dev bloat |
| `mac-cleaner/`, Colab notebooks | Not needed to trade |

---

## Environment secrets (automatic)

Plaintext `.env` is **never** in git. The encrypted vault is.

**Primary machine** (after editing `.env`):

```bash
python3 -c "from core.env_secrets import encrypt_env_to_vault; encrypt_env_to_vault(force=True)"
git add secrets/hanoon.env.enc && git commit -m "sync env" && git push
```

**New machine:** `git pull && ./start.sh` — decrypts automatically.

Disable: `export ENV_SYNC_ENABLED=false`

### Variables inside the vault

```bash
IB_HOST=127.0.0.1
IB_PORT=4002
TRADING_BOT_TELEGRAM_TOKEN=...
TRADING_BOT_TELEGRAM_CHAT_ID=...
GITHUB_TOKEN=...
GITHUB_HANOON_REPO=sajibmdsaberahmad-create/HANOON
```

Copy `.env.example` only if you need to bootstrap from scratch (no vault).

---

## Optional: PPO from GitHub Release

`ppo_trader.zip` is **in the repo**. For a slimmer workflow you can also mirror via release:

```bash
./scripts/bootstrap_from_release.sh   # downloads from latest release if zip missing
```

Publish a new release (from primary dev machine):

```bash
./scripts/release_hanoon.sh
```

---

## Publish clean snapshot (dev machine only)

From the full **trading-bot-HA-NUN** workspace, push a fresh clean bundle to this repo:

```bash
./scripts/publish_hanoon_repo.sh
```

### Automatic updates (enabled by default)

While HANOON runs on your dev machine, the **clean algo repo auto-updates** when:

| Event | What syncs |
|-------|------------|
| **Bot shutdown** | Full clean snapshot (code, PPO, learning, encrypted env) |
| **Model release** | After PPO retrain / new `ppo_trader.zip` |
| **Daily IB learning** | After end-of-day analyze + PPO train |
| **Git sync daemon** | Dev workspace → `trading-bot-HA-NUN` (full history) |

Config in `.env`:

```bash
GITHUB_CLEAN_ALGO_REPO=sajibmdsaberahmad-create/HANOON
HANOON_CLEAN_REPO_AUTO_PUBLISH=true
HANOON_CLEAN_PUBLISH_MIN_SEC=3600   # min 1h between auto publishes
```

Manual publish anytime: `./scripts/publish_hanoon_repo.sh`

Other repos (Grandmaster, Logs) are unchanged.

This copies only essential files + encrypts env. Other repos are untouched.

---

## IB Gateway (paper)

1. Install [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway.html)  
2. Login → **Paper Trading**  
3. API Settings: enable socket clients, port **4002**, Read-Only **OFF**  
4. Start Gateway **before** `./start.sh`

---

## What HANOON does

- **AI entries** — Ollama council + PPO alignment; quality over blind spikes  
- **AI profit** — ride winners, trail stops; green profit lock if AI stalls  
- **Daily IB learning** — end-of-day full IB data → Ollama analyze → PPO train (beat yesterday)  
- **Pre-market + RTH only** — no after-hours order spam  

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No `.env` after clone | `pip install cryptography` then `./start.sh` |
| IB connection refused | Start IB Gateway on port 4002 |
| Ollama errors | `brew install ollama` |
| Missing `ppo_trader.zip` | `git pull` or `./scripts/bootstrap_from_release.sh` |

---

## Repo architecture

| Repo | Role |
|------|------|
| **HANOON** (this) | Clean portable algo — clone & run anywhere |
| trading-bot-HA-NUN | Full dev workspace (unchanged) |
| trading-bot-Grandmaster | Large model weights (optional) |
| trading-bot-Logs | Historical logs (optional) |

---

## Disclaimer

Paper trading and education. Live trading risks total loss. You are responsible for broker compliance and capital risk.

More detail: [docs/LAUNCH_GUIDE.md](docs/LAUNCH_GUIDE.md)
