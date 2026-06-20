# Training Guide

This explains how the AI part of the bot actually learns, what the 14
features mean, and how to tune it. If you just want to run the bot, see
`LAUNCH_GUIDE.md` instead — you don't need this file for that.

---

## 1. The Big Picture

The bot uses **PPO (Proximal Policy Optimization)**, a reinforcement
learning algorithm, to decide HOLD / BUY / SELL. It does **not** decide
position size or exact stop/target prices — that's deliberate. Sizing
and exits are computed deterministically by `core/risk.py` from current
market volatility and your account size, so a badly-trained model can
never risk more than the hardcoded limits allow. The AI's only job is
timing: should we be in this stock right now, or not.

```
Historical/live OHLCV bars
        │
        ▼
FeatureEngineer.compute()        [14 features per bar — Section 3]
        │
        ▼
TradingEnv (Gymnasium)            [Section 4]
  obs  = 30 bars × 14 features + [cash_ratio, position_ratio] = 422-dim
  acts = Discrete(3): 0=HOLD 1=BUY 2=SELL
  rew  = Δlog(portfolio_value) − transaction_cost − drawdown_penalty
        │
        ▼
PPO Agent (Stable-Baselines3, MLP [512, 256, 128])   [Section 5]
        │
        ├── Warm-up: trained once on 5 years of daily history (Section 6)
        └── Online fine-tune: brief retrain every 30 live bars (Section 7)
        │
        ▼
RiskManager validates/overrides every proposed action  [see ARCHITECTURE.md]
```

---

## 2. Two Training Modes

| Mode | When | Data | Purpose |
|---|---|---|---|
| **Warm-up** (`--mode warmup`) | Once, before first live run | 5 years daily bars | Teach the agent general market behavior |
| **Online fine-tune** | Automatic, every 30 live bars | Last ~60–300 live bars | Adapt to the current regime without forgetting too fast |
| **Evaluate** (`--mode evaluate`) | Anytime | 1 year daily bars | Offline backtest of the current saved model, no orders placed |

---

## 3. The 14 Features (`core/features.py`)

The original 11 describe **where price has been**; the 3 new ones (11–13)
add signal about **where it might be going**, which is what your request
for "calculative ability to predict moves" maps to concretely.

| # | Feature | What it captures |
|---|---|---|
| 0 | `log_return` | Stationary 1-bar price change (raw prices aren't learnable — they drift) |
| 1 | `volatility_10` | 10-bar rolling stdev of returns — is the market calm or choppy |
| 2 | `rsi_14` | Classic overbought/oversold oscillator, normalized to [0,1] |
| 3 | `macd_signal` | 12/26/9 EMA momentum crossover histogram |
| 4 | `bb_pct` | Where price sits in its 20-bar Bollinger Band, [0,1] |
| 5 | `volume_z` | Volume vs 30-bar mean, Z-score — spots institutional activity |
| 6 | `volume_accel` | Rate of change of volume_z — the "surge" before a big move |
| 7 | `price_momentum_5` | 5-bar cumulative log-return |
| 8 | `vwap_deviation` | Distance from VWAP — institutions revert to this benchmark |
| 9 | `atr_norm` | Average True Range / price — how wide candles are right now |
| 10 | `obv_z` | On-Balance Volume Z-score — accumulation vs distribution |
| **11** | **`trend_strength`** | **New.** Simplified ADX — is there a real trend to ride, or is this noise? Low values tell the agent to favor HOLD even if other indicators look tempting. |
| **12** | **`mean_reversion_z`** | **New.** Distance from a fast 9-bar EMA in standard-deviation units. Helps the agent time entries on pullbacks instead of chasing extended moves. |
| **13** | **`realized_vol_ratio`** | **New.** Short-term vol / long-term vol. Values above 1 mean volatility is expanding right now — a classic precursor to a breakout move, used both by the agent and by the risk engine's ATR-based stop sizing. |

All features are bounded/clipped (see the source for exact ranges).
Unbounded raw values cause gradient explosions and the network simply
won't converge — this is the single most common reason a homemade
trading RL setup fails to learn anything useful.

---

## 4. The Trading Environment (`core/env.py`)

- **Observation**: the last 30 bars × 14 features, flattened, plus the
  current cash ratio and position ratio (so the agent knows its own
  state, not just the market's).
- **Action space**: `Discrete(3)` — HOLD, BUY, SELL. The agent does not
  choose quantity; `TradingEnv` always uses `MAX_POSITION_PCT` of cash
  for sizing during *training* (a simplification — live trading uses
  the real risk-based sizer instead, see `ARCHITECTURE.md`).
- **Reward**: log-return of portfolio value, minus a drawdown penalty
  once unrealized drawdown exceeds 3% from the running peak. Log
  returns are scale-invariant across price levels, which matters
  because training data spans years of price history at very different
  price levels.

---

## 5. PPO Hyperparameters (`core/config.py`)

| Parameter | Default | Why |
|---|---|---|
| `PPO_CLIP_RANGE` | 0.15 | THE PPO stability guarantee — bounds how much the policy can shift in one gradient step. This is what makes continuous online fine-tuning safe; without it, fine-tuning could catastrophically forget prior learning. |
| `PPO_ENT_COEF` | 0.01 | Entropy bonus — prevents the agent collapsing to always-HOLD, a common RL trading failure mode |
| `PPO_N_STEPS` | 1024 | Rollout buffer length before each update — larger = more stable gradient estimate |
| `PPO_NET_ARCH` | (512, 256, 128) | 3-layer MLP sized for a 422-dim observation |
| `PPO_LR` | 2.5e-4 | Learning rate — SB3 decays this internally over training |
| `PPO_GAMMA` | 0.99 | Future reward discount — favors strategies that hold up over many steps, not just the next tick |

Device is auto-selected (`device="auto"` in `core/agent.py`): CUDA on an
NVIDIA VPS, MPS (Metal) on Apple Silicon, CPU otherwise. The startup log
tells you exactly which one was picked — confirm GPU acceleration is
active before a long training run.

---

## 6. Running Warm-Up Training

```bash
python main.py --mode warmup --ticker SPY --cash 1000
```

What happens, step by step:
1. Downloads 5 years of daily bars from IB (`HISTORY_DURATION`).
2. Computes the 14-feature matrix.
3. Splits 70% train / 30% held-out eval (`WARMUP_SPLIT_PCT`) — the eval
   slice is data the agent never trains on, so its performance there is
   a genuine (if rough) test of generalization.
4. Trains PPO for 200,000 steps (`WARMUP_TIMESTEPS`).
5. Runs one deterministic episode on the held-out eval data and prints:
   ```
   Final portfolio value: $...
   PPO agent return:      +X.X%
   Buy-and-hold return:   +Y.Y%
   Alpha vs B&H:          +Z.Z%
   Action breakdown:      HOLD=... BUY=... SELL=...
   ```

**How to read this output:**
- *Alpha vs buy-and-hold* is the headline number — beating a passive
  benchmark is a meaningfully high bar, especially after transaction
  costs are already included in the simulation.
- If `BUY=0` in the action breakdown, the agent learned to never trade.
  This usually means it needs more training steps, or `PPO_ENT_COEF`
  should be nudged up slightly to encourage more exploration. This is
  flagged automatically in the log.
- Daily bars over 5 years (~1,250 bars) is a relatively small RL
  dataset. Don't expect Wall Street–beating alpha from warm-up alone —
  its job is to teach general "is now a reasonable time to be long"
  judgment. The online fine-tuning loop is what actually adapts the
  agent to live, current market conditions.

Retrain monthly (or whenever market regime feels like it's shifted) by
re-running the same command — it overwrites `ppo_trader.zip`.

---

## 7. Online Fine-Tuning (automatic, during live trading)

`core/agent.py`'s `OnlineLearningManager` retrains briefly every 30 new
1-minute bars (`FINE_TUNE_EVERY_BARS`), using only the most recent
window of live data (not the full history). Why a sliding window and
not all history: PPO is **on-policy** — it learns best from data
generated by something close to its current policy, and using stale
old-regime data would bias it toward how the market used to behave
rather than how it behaves now.

`reset_num_timesteps=False` is critical here: it preserves PPO's
internal learning-rate decay schedule across fine-tune sessions instead
of resetting to a high initial learning rate every 30 minutes, which
would otherwise cause destabilizing updates.

You don't need to do anything to make this happen — it's automatic
during `--mode trade`. You'll see `Online fine-tune #N` lines in the log.

---

## 8. Evaluating a Trained Model Without Trading

```bash
python main.py --mode evaluate --ticker SPY
```

Runs the current `ppo_trader.zip` deterministically over the last
year of daily data and prints the same return/alpha/action breakdown
as warm-up, without connecting any order-placement logic — purely a
read of historical data plus a simulated portfolio. Useful for
checking model health after several online fine-tune sessions, or
before deciding whether to retrain from scratch.

---

## 9. Tuning Cheat Sheet

| Symptom | Try |
|---|---|
| Agent never buys | Increase `PPO_ENT_COEF` (e.g. 0.01 → 0.02), or increase `WARMUP_TIMESTEPS` |
| Agent trades too often / churns | Decrease `PPO_ENT_COEF`, check `TRANSACTION_COST_PCT` is realistic (it should be — over-trading should already be penalized) |
| Online fine-tune seems to destabilize the model | Lower `FINE_TUNE_STEPS` or `PPO_CLIP_RANGE` |
| Want a longer-horizon trader instead of scalping | Increase `WINDOW_SIZE` (more bars of context) and consider a coarser `DECISION_BAR` |
| Training is slow | Confirm GPU is actually selected (check the startup log for "MPS" or "CUDA"); reduce `PPO_NET_ARCH` if on CPU only |

Changing `WINDOW_SIZE` or `N_FEATURES` requires retraining from scratch
— the observation dimension changes, and a saved model won't load into
a differently-shaped network.
