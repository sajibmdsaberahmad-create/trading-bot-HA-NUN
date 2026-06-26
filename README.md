# HANOON — AI Trading Algo (HA-NUN)

**HANOON** is a full-time AI profit-hunting scalper for US equities. It connects to **Interactive Brokers** (paper or live), runs **PPO reinforcement learning**, and uses **Ollama** (local LLM council) for entries, exits, and daily learning.

This repo is designed so you can **clone on any Mac/device and run immediately** — code, models, learning state, and **encrypted environment secrets** all sync through git.

---

## Clone & run on a new device (3 steps)

### 1. Prerequisites

| Requirement | Notes |
|-------------|--------|
| **macOS** 12+ (or Linux) | Apple Silicon (M1/M2) supported — 8 GB RAM minimum |
| **Python 3.10 or 3.11** | 3.12 not supported by Stable-Baselines3 |
| **IB Gateway** | Paper port **4002** (or TWS paper **7497**) — logged in before start |
| **Ollama** | Installed (`brew install ollama`) — script pulls model automatically |
| **Git** | Access to this **private** repo |

### 2. Clone the algo repo

```bash
git clone https://github.com/sajibmdsaberahmad-create/HANOON.git
cd HANOON
```

Everything you need is in the repo:

- `core/` — trading engine, AI pipeline, risk, broker
- `models/` — PPO weights (`ppo_trader.zip`), guidelines, learning journals
- `scripts/start_hanoon.sh` — one-command launcher
- `secrets/hanoon.env.enc` — **encrypted copy of `.env`** (Telegram, IB, GitHub keys)
- `secrets/sync.key` — decryption key (private repo only)

Plaintext `.env` is **never** committed. On startup the bot decrypts the vault automatically.

### 3. Start HANOON

```bash
chmod +x start.sh scripts/start_hanoon.sh scripts/stop_hanoon.sh
./start.sh
```

On first run the script will:

1. Create Python `venv` and install `requirements.txt`
2. **Decrypt** `secrets/hanoon.env.enc` → `.env` (needs `cryptography` package)
3. Start **Ollama** and pull the right model for your RAM
4. Connect to **IB Gateway** on `127.0.0.1:4002`
5. Launch the **HANOON scalper** (scanner + AI council + PPO)

Stop cleanly:

```bash
./scripts/stop_hanoon.sh
```

Logs: `logs/HANOON.log` · Ollama: `logs/ollama.log`

---

## Environment & secrets (cross-device sync)

### How it works

| File | In git? | Purpose |
|------|---------|---------|
| `.env` | **No** (local only) | Plaintext secrets on disk |
| `secrets/hanoon.env.enc` | **Yes** | Fernet-encrypted `.env` |
| `secrets/sync.key` | **Yes** | Decrypt key (private repo) |

On **device A** (after editing `.env`):

```bash
python3 -c "from core.env_secrets import encrypt_env_to_vault; encrypt_env_to_vault(force=True)"
git add secrets/hanoon.env.enc && git commit -m "sync env" && git push
```

On **device B** (new machine):

```bash
git pull
./start.sh   # auto-decrypts vault → .env
```

Disable sync: `export ENV_SYNC_ENABLED=false`

### Typical `.env` variables (stored inside encrypted vault)

These are loaded automatically after decrypt — you do **not** re-type them on a new device if the vault is in git:

```bash
# Interactive Brokers
IB_HOST=127.0.0.1
IB_PORT=4002
IB_CLIENT_ID=1

# Telegram commander bot
TRADING_BOT_TELEGRAM_TOKEN=...
TRADING_BOT_TELEGRAM_CHAT_ID=...
TRADING_BOT_TELEGRAM_LISTEN=true

# GitHub auto-sync (optional)
GITHUB_TOKEN=...
GITHUB_REPO=sajibmdsaberahmad-create/HANOON

# Ollama (optional overrides)
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_HOST=http://localhost:11434
```

---

## What the algo does

### Trading session

- **Pre-market + RTH only** — no after-hours order spam (configurable)
- **AI entries** — Ollama council + PPO must align; no blind spike chasing
- **AI profit full power** — ride winners, trail stops, raise TP when green
- **Green profit lock** — if AI stalls while in profit, mechanical quick-scalp locks green
- **Capital discipline** — paper treated as live; quality over frequency

### Learning (beat yesterday every day)

At **session end** and **market open**, the bot:

1. Fetches **full IB day data** — executions, orders, trades, account snapshot
2. Merges bot journals, PPO entry ledger, profit-hunt events
3. **Ollama analyzes** — lessons, mistakes, beat-yesterday plan
4. **Ingests** into experience buffer and **trains PPO**
5. Saves reports to `models/daily_ib_learning/`

Config (set in `scripts/start_hanoon.sh` or env):

```
DAILY_IB_LEARNING_ENABLED=true
GREEN_PROFIT_LOCK_ENABLED=true
AI_PROFIT_FULL_POWER=true
CAPITAL_DISCIPLINE=true
```

---

## Project layout

```
trading-bot-HA-NUN/   # local folder name after clone
├── start.sh                    # → scripts/start_hanoon.sh
├── scripts/
│   ├── start_hanoon.sh         # Full launcher (venv, env, ollama, scalper)
│   ├── stop_hanoon.sh          # Clean shutdown
│   └── start_git_sync.sh       # Auto git push on learning changes
├── core/
│   ├── scalper_runner.py       # Main live trading loop
│   ├── env_secrets.py          # Encrypted .env vault
│   ├── daily_ib_learning.py    # End-of-day IB → Ollama + PPO
│   ├── green_profit_lock.py    # Mechanical green scalp fallback
│   ├── ai_commander.py         # Ollama council
│   ├── config.py               # All parameters
│   └── ...
├── models/
│   ├── ppo_trader.zip          # PPO model (synced in git)
│   ├── experience_buffer.jsonl
│   ├── daily_ib_learning/      # Per-day IB learning packs
│   └── ...
├── secrets/
│   ├── hanoon.env.enc          # Encrypted environment
│   └── sync.key                # Vault key
└── docs/                       # Architecture, training, launch details
```

---

## IB Gateway setup (paper)

1. Install [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway.html)
2. Log in with **Paper Trading** mode
3. Configure → API → Settings:
   - Enable ActiveX and Socket Clients
   - Port **4002** (Gateway paper) or **7497** (TWS paper)
   - Read-Only API **OFF**
   - Allow localhost connections
4. Keep Gateway running before `./start.sh`

Override port: `IB_PORT=7497 ./start.sh`

---

## Manual CLI (without launcher)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 -c "from core.env_secrets import bootstrap_env; bootstrap_env()"
source .env
python main.py --mode scalper --port 4002 --client-id 1
```

---

## Git sync between devices

The repo includes a **git auto-push daemon** (optional) that commits learning artifacts, model updates, and encrypted env vault when files change — so a second machine always gets the latest brain.

```bash
./scripts/start_git_sync.sh   # usually started by start_hanoon.sh
```

**Keep this repo private.** `secrets/sync.key` decrypts your Telegram/IB/GitHub credentials.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No `.env` after clone | `pip install cryptography` then `./start.sh` (decrypts vault) |
| `secrets/sync.key missing` | `git pull` — key is in the private repo |
| IB connection refused | Start IB Gateway, check port `4002` |
| Ollama not responding | `brew install ollama` or `ollama serve` |
| Error 201 after 16:00 ET | Expected — bot halts orders outside RTH/pre-market |
| RAM pressure on 8 GB M2 | Default model `qwen2.5:3b`; heavy training off-hours disabled |

---

## Security

- **Private GitHub repo required** — encrypted vault + sync key are pushed intentionally for multi-device use
- Never make this repo public without rotating `secrets/sync.key` and all API tokens
- `.env` plaintext stays on disk only; rotate tokens if a machine is compromised

---

## More documentation

| Doc | Topic |
|-----|--------|
| [docs/LAUNCH_GUIDE.md](docs/LAUNCH_GUIDE.md) | Detailed install, VPS, systemd |
| [docs/TRAINING_GUIDE.md](docs/TRAINING_GUIDE.md) | PPO / off-hours training |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design |
| [docs/GIT_SYNC.md](docs/GIT_SYNC.md) | Auto-commit behavior |
| [docs/COLAB_TRAINING.md](docs/COLAB_TRAINING.md) | Grandmaster distillation on Colab |

---

## License & disclaimer

Paper trading and education only. Live trading risks total loss. You are responsible for compliance with broker and local regulations.
