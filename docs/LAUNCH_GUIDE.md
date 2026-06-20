# Launch Guide

This guide gets the bot running — first on your Mac in paper mode, later
on a VPS. For how the AI is trained and tuned, see `TRAINING_GUIDE.md`.
For how the code is organized, see `ARCHITECTURE.md`.

> **Read this before anything else:** this bot is for paper trading and
> education. Section 8 ("Going Live") explains what to verify before
> ever connecting it to a real-money account, and even then you should
> start with the smallest size you're willing to lose completely.

---

## 1. System Requirements

| | |
|---|---|
| OS | macOS 12+ (dev), Ubuntu 20+ (VPS) — also works on Windows |
| Python | 3.10 or 3.11 (3.12 not yet supported by Stable-Baselines3) |
| RAM | 8 GB minimum, 16 GB for training |
| GPU | Optional — Apple Silicon (M1/M2/M3/M4) uses Metal automatically; NVIDIA uses CUDA; otherwise CPU |
| Internet | Required during market hours |
| IB Account | Free paper-trading account is enough to start |

---

## 2. Install Python & Dependencies

**macOS:**
```bash
brew install python@3.10
python3.10 -m venv bot_env
source bot_env/bin/activate
```

**Linux / VPS (Ubuntu):**
```bash
sudo apt update && sudo apt install -y python3.10 python3.10-venv python3-pip
python3.10 -m venv bot_env
source bot_env/bin/activate
```

You should see `(bot_env)` in your prompt. Run everything below inside it.

**Install packages:**
```bash
pip install ib_insync gymnasium "stable-baselines3[extra]" \
            pandas numpy torch scipy scikit-learn tqdm
```

Apple Silicon note: PyTorch 2.0+ auto-detects the Metal (MPS) backend —
no extra steps. The bot logs which device it picked at startup so you
can confirm acceleration is active.

**Verify:**
```bash
python -c "import ib_insync, gymnasium, stable_baselines3, torch; print('All OK')"
```

---

## 3. Interactive Brokers Gateway Setup

You need IB Gateway (not Trader Workstation — Gateway is the lightweight
headless version meant for bots).

1. Create an IB account at https://www.interactivebrokers.com/ if you
   don't have one. A paper-only account needs no funding.
2. Download IB Gateway: https://www.interactivebrokers.com/en/trading/ibgateway.html
3. Launch it, log in with **Trading Mode = PAPER**. The title bar will
   say "PAPER TRADING" when you're in the right mode.
4. Configure → Settings → API → Settings:
   - ✅ Enable ActiveX and Socket Clients
   - Socket port → **7497** (paper)
   - ✅ Allow connections from localhost only
   - ❌ Read-Only API must be **OFF** (the bot needs to place orders)
   - Master API client ID → 0
   - ✅ Log API messages to file (helps debugging)
   - ✅ Auto restart after restart
5. Click OK, restart IB Gateway.
6. Confirm in the Gateway status bar: `API: listening on port 7497`.

**Market data:** paper accounts get free 15-min delayed data by default.
For live-feeling paper trading, add a market data subscription in
IB Portal → Settings → Market Data Subscriptions (the "US Securities
Snapshot and Futures Value Bundle" is cheap and usually sufficient).
**Tick-by-tick data** (which this bot prefers for fast stop monitoring)
typically requires a real-time data subscription — if you don't have
one, the bot automatically falls back to 5-second real-time bars, which
still gives strong intrabar stop coverage.

---

## 4. Set Up Telegram Notifications (recommended)

This takes about 3 minutes and works identically on your Mac and later
on a VPS.

1. In Telegram, message **@BotFather** → send `/newbot` → follow the
   prompts (give it any name). BotFather replies with a **token** that
   looks like `123456789:AAFf3...`. Copy it.
2. Send your new bot any message (e.g. "hi") so it can see your chat.
3. In a browser, visit:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Find `"chat":{"id": 123456789, ...}` in the JSON response — that
   number is your **chat ID**.
4. Export both before launching the bot:
   ```bash
   export TRADING_BOT_TELEGRAM_TOKEN="123456789:AAFf3..."
   export TRADING_BOT_TELEGRAM_CHAT_ID="123456789"
   ```
   Add these two lines to your `~/.zshrc` (Mac) or `~/.bashrc` (VPS) so
   they persist across sessions.

You'll get a push notification for every trade opened/closed, every
stop or take-profit trigger, risk halts, reconnect events, errors, and
a daily summary. If you skip this, everything still goes to the
terminal and `trading_bot.log`.

**Email is optional** as a second channel — see the `EMAIL_*` settings
in `core/config.py` and the matching `TRADING_BOT_SMTP_*` environment
variables if you want it too.

---

## 5. Configure the Bot

Open `core/config.py`. The defaults are already set for a **$1,000
account with a $50-per-trade risk ceiling**, which is what most people
start with. Things you're most likely to change:

| Setting | Default | What it does |
|---|---|---|
| `TICKER` | `"SPY"` | The stock the bot trades |
| `INITIAL_CASH` | `1000.0` | Starting capital (also override with `--cash`) |
| `RISK_PER_TRADE_PCT` | `0.05` | % of equity risked per trade |
| `MAX_RISK_PER_TRADE_USD` | `50.0` | Hard dollar ceiling per trade, whichever of this or the % is smaller wins |
| `MAX_DAILY_LOSS_PCT` | `0.03` | Halts all trading if the account is down this much today |
| `PAPER_TRADING` | `True` | Leave this `True` until you've read Section 8 |

You normally don't need to touch anything else. Every other number in
that file is documented inline with why it exists.

---

## 6. Train the Model (one-time, ~20–40 minutes)

With IB Gateway running and logged in to paper mode:

```bash
python main.py --mode warmup
```

This downloads 5 years of daily history, engineers 14 features per bar,
trains the PPO agent for 200,000 steps, and evaluates it on a held-out
30% of the data versus simple buy-and-hold. It saves `ppo_trader.zip`.

See `TRAINING_GUIDE.md` for what's actually happening here and how to
read the evaluation output.

---

## 7. Run Live Paper Trading

```bash
python main.py --mode trade
```

What happens:
1. The bot connects to IB Gateway, loads the trained model, and starts
   a live tick stream (or 5-second bar fallback) for the ticker.
2. Every tick, if a position is open, it checks the hard stop, trailing
   stop, hard take-profit, and trailing profit-taker — and reacts
   immediately if any is breached.
3. Once a minute, it builds the 30-bar feature window, asks the PPO
   agent for HOLD/BUY/SELL, runs that through the risk manager, and —
   for a new BUY — computes position size and stop/target from current
   volatility, then places a **real IB bracket order** (entry + stop +
   target, OCA-linked).
4. Every 30 minutes it briefly fine-tunes the model on recent live data.
5. You get a Telegram message on every meaningful event (if configured)
   and a log line every 5 bars in the terminal and `trading_bot.log`.

Stop with `Ctrl+C` — the bot saves the model and disconnects cleanly.
**If a position is open when you stop the bot, its bracket stop/target
orders stay live on IB's servers** — they keep protecting the position
even with the bot offline. Read the shutdown log line to confirm.

### Run in the background (Mac or VPS)
```bash
nohup python main.py --mode trade > bot_output.log 2>&1 &
echo $! > bot.pid
tail -f trading_bot.log
```

To stop a backgrounded bot cleanly: `kill -SIGINT $(cat bot.pid)` (not
`-9` — that skips the clean shutdown and model save).

---

## 8. Going Live (real money) — read this fully first

Do **not** skip paper trading. Checklist before ever switching to a
live account:

- [ ] Paper traded for at least 30 calendar days
- [ ] Reviewed `trading_bot.log` and `performance.csv` — win rate,
      Sharpe ratio, and max drawdown all look reasonable to you
- [ ] You understand every line of `core/risk.py` — it's the only
      thing standing between the AI and your account
- [ ] You're comfortable with the bot's actual historical behavior,
      not just the backtest numbers
- [ ] Starting size is small enough that the worst case (multiple
      consecutive stop-outs plus the daily circuit breaker) is money
      you can lose without it affecting you

When ready:
1. Log in to IB Gateway with your **live** credentials, port 7496.
2. In `core/config.py`, confirm `PAPER_TRADING = False`.
3. Run: `python main.py --mode trade --port 7496`

The bot prints a loud warning banner whenever port 7496 is used. There
is no way to silence it — that's intentional.

---

## 9. Deploying to a VPS (later)

The bot was written to behave identically on Mac and Linux — same
code, same config. Steps for an Ubuntu VPS:

1. `sudo apt install -y python3.10 python3.10-venv` (as in Section 2)
2. Copy the whole project folder to the VPS (`scp -r tradingbot/ user@vps:~/`)
3. Install IB Gateway on the VPS too — it must run on the same machine
   (or one reachable on `IB_HOST`) as the bot, since `IB_HOST=127.0.0.1`
   by default. Many people run IB Gateway under a VNC session on the
   VPS, or use a headless variant like IBC to auto-login Gateway.
4. Re-export your Telegram env vars on the VPS (Section 4) — they don't
   carry over from your Mac automatically.
5. Use `systemd` instead of `nohup` for real reliability — it restarts
   the bot automatically if the process dies, and survives VPS reboots:

   ```ini
   # /etc/systemd/system/tradingbot.service
   [Unit]
   Description=PPO Trading Bot
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=/home/youruser/tradingbot
   Environment="TRADING_BOT_TELEGRAM_TOKEN=123456:..."
   Environment="TRADING_BOT_TELEGRAM_CHAT_ID=123456789"
   ExecStart=/home/youruser/tradingbot/bot_env/bin/python main.py --mode trade
   Restart=on-failure
   RestartSec=30

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now tradingbot
   journalctl -u tradingbot -f     # live logs
   ```

This is also why the bot places real IB bracket orders rather than
relying only on in-process logic (see `ARCHITECTURE.md`) — even between
a VPS reboot and `systemd` restarting the process, your stop and target
are still resting on IB's servers.

---

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `IB connection failed` | Gateway not running/logged in, wrong port, Read-Only API still on, client ID conflict |
| `Could not qualify contract` | Wrong ticker, or data subscription doesn't cover it |
| `IB returned no historical data` | Same as above, or outside data hours |
| Tick stream falls back to 5-sec bars immediately | Your data subscription doesn't include tick-by-tick — this is fine, just slightly coarser stop monitoring |
| Telegram messages not arriving | Env vars not exported in the shell that launched the bot; re-check with `echo $TRADING_BOT_TELEGRAM_TOKEN` |
| Bot never buys | Check `trading_bot.log` for risk halts, or see `TRAINING_GUIDE.md` if it's a freshly trained model that's overly cautious |
