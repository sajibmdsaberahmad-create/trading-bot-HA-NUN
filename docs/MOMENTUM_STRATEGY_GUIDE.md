# 🚀 Upgraded PPO Trading Bot: Momentum & Full Cash Strategy Guide

This guide details the major architectural upgrades implemented to match your specific manual momentum-riding strategy, along with automated model journaling, model versioning, Telegram setup, and background terminal deployment instructions.

---

## 1. 🎯 Sizing Mode: `full_cash`

In momentum and penny-stock trading on a small account, deploying your full capital maximizes leverage on quick upward movements, but managing risk is critical. We introduced a new `full_cash` Sizing Mode (`core/config.py` and `core/risk.py`).

### How it works:
1.  **Direct Order Sizing:** In `full_cash` mode, you can specify the exact dollar amount for your trade using the `--order-size-usd` CLI argument. If not specified, it will use your *entire available cash*.
2.  **The Risk Constraint:** The bot then automatically calculates the `stop_distance` to ensure your risk per trade (e.g., $50) is honored, given your chosen order size.
    $$\text{Shares} = \frac{\text{Order Size (USD)}}{\text{Entry Price}}$$
    $$\text{Stop Distance} = \frac{\text{Risk Amount (e.g. \$50)}}{\text{Shares}}$$
3.  **The Advantage:** This allows you to deploy exactly the capital you want into a trade, while the bot dynamically calculates the corresponding stop-loss to ensure your **maximum loss remains mathematically capped** at your configured dollar risk (e.g., $50). (Note: Minor exchange slippage is mitigated with tick-by-tick trailing stops).

---

## 2. 📓 Automated Training Journaling & Model Versioning

To ensure you can track, optimize, and safely revert models without losing progress, we have implemented an automatic tracking ledger in `core/journal.py`:

*   **Automatic Backup Directory (`/models`):** Every warmup training (`--mode warmup`) and live fine-tuning session automatically copies your active model into the `/models` directory with a unique timestamp, e.g., `models/ppo_trader_warmup_20260620_134000.zip`.
*   **JSON Ledger (`training_journal.json`):** Your training and fine-tuning history is logged in a central ledger, storing:
    *   Timestamp, Ticker, and Sizing Mode.
    *   Saved model paths and hyperparameters (PPO learning rate, entropy coefficient, etc.).
    *   Held-out evaluation metrics: portfolio value, PPO returns, Buy-and-Hold benchmark returns, alpha, and action breakdowns (`BUY`, `SELL`, `HOLD`).
*   **Git Integration:** If your directory is a Git repository, the journal will automatically stage and commit `training_journal.json` updates, enabling direct version control on GitHub!

---

## 3. 📲 How to Set Up Telegram Notifications

The bot sends instant push notifications to your phone for all trades, risk halts, and daily summaries.

1.  **Create your Telegram Bot:**
    *   Search for **@BotFather** in Telegram.
    *   Send the `/newbot` command and follow the instructions to choose a name and username.
    *   Copy the **API Token** returned (looks like `123456789:AAFf3...`).
2.  **Create your `.env` file:**
    *   Rename the `.env.example` file in the project root to `.env`.
    *   Open `.env` and fill in your `TRADING_BOT_TELEGRAM_TOKEN` and `TRADING_BOT_TELEGRAM_CHAT_ID`.
    *   The bot will automatically load these on startup. **Do NOT commit your `.env` file to Git!**

    *   To get your Chat ID: Send your new bot a message (e.g., `\"hi\"`), then open a web browser and visit:
        `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
        (Replace `<YOUR_TOKEN>` with your bot\`s token).
        Find the `\"chat\":{\"id\": 123456789}` object in the JSON response. That integer is your **Chat ID**.

---

## 4. 💻 Terminal Launch & Deployment

### Step 4.1: Activate Virtual Environment and Install Dependencies
```bash
python3 -m venv bot_env
source bot_env/bin/activate
pip install -r requirements.txt
```

### Step 4.2: Train Your Model on Your Chosen Stock
Train your PPO model on daily charts to help the AI learn general trend-following and momentum wave-riding structures:
```bash
python3 main.py --mode warmup --ticker SPY --cash 1000 --risk-pct 0.05 --max-risk-usd 50 --sizing-mode full_cash --order-size-usd 900
```
*Your model is saved as `ppo_trader.zip` and versioned backups are saved inside `/models`.*

### Step 4.3: Live Paper Trading on Terminal
Connect to IB Gateway (make sure it is logged in, paper mode, API port 7497 active) and run:
```bash
python3 main.py --mode trade --ticker SPY --cash 1000 --risk-pct 0.05 --max-risk-usd 50 --sizing-mode full_cash --order-size-usd 900
```

### Step 4.4: Background VPS Deployment (systemd service)
To keep the bot running 24/7 on your server and survive restarts, create the file `/etc/systemd/system/tradingbot.service`:

```ini
[Unit]
Description=PPO Momentum Trading Bot (Full Cash Edition)
After=network.target

[Service]
Type=simple
WorkingDirectory=/Users/mdsabersajib/Downloads/tradingbot
ExecStart=/Users/mdsabersajib/Downloads/tradingbot/bot_env/bin/python main.py --mode trade --ticker SPY --cash 1000 --risk-pct 0.05 --max-risk-usd 50 --sizing-mode full_cash --order-size-usd 900
EnvironmentFile=-/Users/mdsabersajib/Downloads/tradingbot/.env  # Load environment variables from .env
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and run the service background process:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tradingbot
journalctl -u tradingbot -f   # Follow live logs
```
