# War Account — Paper Training & Live Cash Setup

HANOON does **not** size trades from raw IB buying power. It uses the **war account** (`core/war_account.py`): a virtual ledger with fees, slippage, T+1 settlement, bullets, and daily trip caps.

IB (paper or live) is only the **order router**. The brain believes **war NAV**, not `$900k` paper.

---

## Modes at a glance

| Mode | When | Entries | PPO promotion |
|------|------|---------|----------------|
| `WAR_ACTIVE` | Paper, war cash + bullets available | Yes | Yes |
| `LIVE_WAR` | Real account, same rules | Yes | Yes |
| `LAB_ACTIVE` | War settled out; lab pool has cash | Yes (small) | No — gold only |
| `OBSERVE` | War + lab dry | No — watch + log | No |

---

## Paper war (default — training as $1k cash)

Already set in `scripts/start_hanoon.sh`:

```bash
WAR_ACCOUNT_ENABLED=true
WAR_CAPITAL_USD=1000
WAR_BULLETS=5
WAR_MAX_ROUND_TRIPS_PER_DAY=3
MAX_ENTRIES_PER_HOUR=2
WAR_SNIPER_MODE=true
AI_PAPER_FREE_LEARNING=false
AI_FULL_CAPITAL_ACCESS=false
```

Start:

```bash
./stop.sh
./scripts/start_hanoon.sh
```

Confirm in log:

```text
⚔️ War account ready — PAPER cap=$1,000 settled=$1,000 mode=WAR_ACTIVE
```

---

## Live account (real money)

Add to `.env` (or export before start). **Set operating capital to what you actually deploy — not full IB balance.**

```bash
# ── LIVE WAR — required ──────────────────────────────────────────
PAPER_TRADING=false
WAR_ACCOUNT_ENABLED=true
WAR_LIVE_OPERATING_CAPITAL=1200    # e.g. $1,200 cash you trade with
WAR_SNIPER_MODE=true

# ── Sniper discipline (recommended defaults) ─────────────────────
WAR_BULLETS=5
WAR_MAX_ROUND_TRIPS_PER_DAY=3
MAX_ENTRIES_PER_HOUR=2
CONFIDENCE_THRESHOLD=0.68
MIN_PROFIT_PROBABILITY=0.65
PPO_BYPASS_REQUIRES_BUY=true

# ── Friction model ───────────────────────────────────────────────
WAR_COMMISSION_PER_SIDE_USD=0.35
WAR_SETTLEMENT_DAYS=1
TRANSACTION_COST_PCT=0.001

# ── Never use IB fantasy balance for sizing ──────────────────────
AI_PAPER_FREE_LEARNING=false
AI_FULL_CAPITAL_ACCESS=false
```

Then:

```bash
./stop.sh
./scripts/start_hanoon.sh
```

Confirm:

```text
⚔️ War account ready — LIVE cap=$1,200 settled=$1,200 mode=LIVE_WAR
```

### What live war guarantees

- Sizing uses **`WAR_LIVE_OPERATING_CAPITAL` only** (e.g. $1,200 even if IB shows more).
- **~5 bullets** — ~20% of NAV per shot, not all-in.
- **Max 3 round-trips/day** on war pool (configurable).
- **T+1 settlement** — sells free unsettled cash until next session; GFV-style veto blocks bad entries.
- **Fees + slippage** applied on virtual fills (pennies slip more).
- Council/Halim prompts include: `WAR ACCOUNT [LIVE_WAR]: nav=… settled=… trips=…`

### If you add more cash later

Update one value — behavior stays the same:

```bash
WAR_LIVE_OPERATING_CAPITAL=2500
```

Restart HANOON. Do **not** enable `AI_FULL_CAPITAL_ACCESS`.

---

## Paper surplus ($900k) — experience, not belief

When war $1k is **settled out** or trip-capped:

1. **`OBSERVE`** — stream, council, Halim gold; no war entries.
2. **`LAB_ACTIVE`** (if `WAR_LAB_ENABLED=true`) — optional ~$2.5k virtual lab, max 2 trips/day, **not** for PPO promotion.

IB paper balance is **never** used for war sizing.

---

## Monitor after restart

```bash
tail -f logs/HANOON.log | grep -v "📡 Streams" | grep -E "⚔️|war:|WAR|ENTRY|EXIT|evict|loss_pressure|guard:"
```

| Log line | Meaning |
|----------|---------|
| `⚔️ War account ready` | Ledger loaded |
| `⚔️ war:block` | Entry vetoed (cash, trips, GFV, cooldown) |
| `⚔️ WAR ENTRY` | Virtual fill + fees logged |
| `⚔️ WAR EXIT` | Net PnL after fees/slip |
| `⚔️ WAR evict lock` | Repeat loser removed from scanner lock |
| `mode→OBSERVE` | War dry — learning only until settlement |

State files:

- `models/war_account_state.json` — NAV, settled cash, mode, trips today
- `models/war_account_ledger.jsonl` — every war/lab fill

---

## Pre-live checklist

- [ ] `PAPER_TRADING=false` on live IB port (e.g. `7496`)
- [ ] `WAR_LIVE_OPERATING_CAPITAL` = actual deployable cash
- [ ] `AI_PAPER_FREE_LEARNING=false` and `AI_FULL_CAPITAL_ACCESS=false`
- [ ] `WAR_SNIPER_MODE=true`
- [ ] Log shows `LIVE_WAR`, not paper cap
- [ ] Paper war session was profitable **after fees** in `war_account_ledger.jsonl` (optional but recommended)

---

## Quick reference — key env vars

| Variable | Paper default | Live |
|----------|---------------|------|
| `WAR_CAPITAL_USD` | `1000` | ignored |
| `WAR_LIVE_OPERATING_CAPITAL` | `0` | **your cash** (e.g. `1200`) |
| `WAR_BULLETS` | `5` | `5` |
| `WAR_MAX_ROUND_TRIPS_PER_DAY` | `3` | `3` |
| `WAR_LAB_CAPITAL_USD` | `2500` | optional observe pool |
| `WAR_SETTLEMENT_DAYS` | `1` | `1` (cash account) |

---

## Related code

- `core/war_account.py` — ledger, modes, slip, settlement
- `core/live_trade_guard.py` — per-ticker loss cooldown (works with war)
- `scripts/start_hanoon.sh` — default env for paper war
- `docs/LAUNCH_GUIDE.md` — general HANOON launch

**Remember:** On live, the only number that matters for entries is `WAR_LIVE_OPERATING_CAPITAL`. Everything else is infrastructure.
